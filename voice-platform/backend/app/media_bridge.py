"""
WebSocket Media Stream Bridge — Pipecat Edition
Replaces hand-rolled loop with Pipecat pipeline using FastAPIWebsocketTransport,
built-in Silero VAD, GroqWhisper STT, AgentRouter, and Murf/Cartesia streaming TTS.

All business logic (AgentRouter, Symphony ticketing, tool calls, DB writes,
event logging, dashboard broadcasts) is preserved.
FastAPI routes, auth, pre-cached greeting, and DB lifecycle are preserved.
"""

import json
import logging
import asyncio
import re
import os
import secrets
import io
import wave
import hmac
import hashlib
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .database import async_session
from .models import Call, CallStatus, TranscriptMessage, Event
from .websocket_manager import dashboard_manager

from .interfaces import BaseSTTService, BaseTTSService, BaseLLMService, BaseTelephonyService, BaseAudioTranscoder, BaseVADDetector
from .config import AppConfig, get_config
from .audio_services import AudioTranscoder, EnergyVADDetector
from .ai_services import STTEngineFactory, LLMEngineFactory, TTSEngineFactory
from .agent_router import AgentRouter
from .gemini_graph import DOCUMENT_TOOL
from .tool_executor import ToolExecutor
from .rag_engine import RAGEngine
from .twilio_client import twilio_sync_client
from .event_logger import event_logger
from .langfuse_tracer import langfuse_tracer

logger = logging.getLogger(__name__)
router = APIRouter(tags=["media-bridge"])

ACTIVE_CALL_QUEUES: Dict[str, asyncio.Queue] = {}
_GREETING_PCM_CACHE: Dict[str, bytes] = {}


class PipecatMediaSession:
    """
    Manages an individual Twilio / Tata SmartFlo WebSocket session using
    Pipecat's frame-based pipeline.
    """

    def __init__(self, websocket: WebSocket, config: AppConfig):
        self.websocket = websocket
        self.config = config

        self._stream_sid: List[Optional[str]] = [None]
        self._call_sid: List[Optional[str]] = [None]
        self._db_call_id: List[Optional[int]] = [None]
        self._caller_phone: List[Optional[str]] = [None]
        self._workflow_id: List[Optional[int]] = [None]       # the agent chosen by the dialled number (None = the active workflow)
        self._tenant_id: Optional[int] = None
        self._org_suspended: bool = False

        self._chat_history: List[Dict[str, str]] = []
        self._current_node_id: List[Optional[str]] = [None]
        self._transcript_sequence: List[int] = [1]
        self._session_state: Dict[str, Any] = {
            "is_agent_speaking": False,
            "interrupted": False,
        }

        self._session_start_time = datetime.utcnow()
        self._media_chunks_count: int = 0
        self._total_bytes_received: int = 0
        self._injection_task: Optional[asyncio.Task] = None

        cfg = config
        self._stt_service = STTEngineFactory.get_engine(cfg)
        self._tts_service = TTSEngineFactory.get_engine(cfg)
        self._llm_service = LLMEngineFactory.get_engine(cfg)
        self._tool_exec = ToolExecutor()
        self._rag = RAGEngine()
        self._router_service = AgentRouter(
            llm=self._llm_service,
            tool_executor=self._tool_exec,
            rag_engine=self._rag,
            config=cfg,
        )
        self._transcoder = AudioTranscoder()

    @property
    def stream_sid(self): return self._stream_sid[0]
    @property
    def call_sid(self): return self._call_sid[0]
    @property
    def db_call_id(self): return self._db_call_id[0]
    @property
    def caller_phone(self): return self._caller_phone[0]

    async def handle_session(self):
        await self.websocket.accept()
        logger.info("[PipecatBridge] WebSocket accepted.")
        try:
            await self._bootstrap_stream_metadata()
        except Exception as e:
            logger.error(f"[PipecatBridge] Bootstrap error: {e}", exc_info=True)
            return

        if self._org_suspended:
            # The number belongs to a suspended organization: no agent answers. (A spoken notice would need speech
            # synthesis for that organization; for now the line is closed and the call is logged.)
            logger.warning(f"[PipecatBridge] call to a number of suspended organization {self._tenant_id}; closing it")
            try:
                async with async_session() as session:
                    call = (await session.execute(select(Call).where(Call.id == self._db_call_id[0]))).scalars().first()
                    if call:
                        call.status, call.live_status, call.end_reason = CallStatus.COMPLETED, "completed", "org_suspended"
                        call.ended_at = datetime.utcnow()
                        await session.commit()
                await self.websocket.close()
            except Exception as exc:
                logger.warning(f"[PipecatBridge] could not close the suspended call cleanly: {exc}")
            await self._cleanup()
            return

        await self._apply_workflow_settings()

        try:
            await self._run_pipecat_pipeline()
        except (Exception, asyncio.CancelledError) as e:
            logger.info(f"[PipecatBridge] Pipeline finished or cancelled: {e}")
        finally:
            await self._cleanup()

    async def _apply_workflow_settings(self):
        """Use the active workflow's LLM/STT/TTS choices for this call (falls back to the env defaults)."""
        try:
            from .workflow_runtime import effective_config
            cfg = await effective_config(workflow_id=self._workflow_id[0], base=self.config)
            if cfg == self.config:
                return
            self.config = cfg
            self._stt_service = STTEngineFactory.get_engine(cfg)
            self._tts_service = TTSEngineFactory.get_engine(cfg)
            self._llm_service = LLMEngineFactory.get_engine(cfg)
            self._router_service = AgentRouter(
                llm=self._llm_service, tool_executor=self._tool_exec, rag_engine=self._rag, config=cfg)
            logger.info(f"[PipecatBridge] workflow settings applied: llm={cfg.llm.provider} "
                        f"stt={cfg.stt.provider}/{cfg.stt.language_code} tts={cfg.tts.provider}")
        except Exception as exc:
            logger.warning(f"[PipecatBridge] workflow settings not applied, using defaults: {exc}")

    async def _bootstrap_stream_metadata(self):
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                raw = await asyncio.wait_for(self.websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                return

            try:
                data = json.loads(raw)
            except Exception:
                continue

            event = data.get("event", "")
            if event == "connected":
                logger.info("[PipecatBridge] Connected event received.")
                continue
            if event == "start":
                await self._on_stream_start(data)
                return

        logger.warning("[PipecatBridge] No stream-start event within 5s timeout.")

    async def _on_stream_start(self, data: Dict[str, Any]):
        start_data = data.get("start", {})
        stream_sid = (
            data.get("streamSid")
            or start_data.get("streamSid")
            or f"stream-{int(datetime.utcnow().timestamp())}"
        )
        call_sid = start_data.get("callSid") or stream_sid
        from_num = start_data.get("from") or "Smartflo Caller"
        to_num = start_data.get("to") or "+918069879488"

        self._stream_sid[0] = stream_sid
        self._call_sid[0] = call_sid

        logger.info(f"[PipecatBridge] Stream Start: SID={stream_sid}, CallSid={call_sid}, From={from_num}, To={to_num}")

        # Detect outbound click-to-call: Smartflo sends from=DID, to=customer
        # For inbound calls: from=customer, to=DID (correct order)
        # For outbound click-to-call: from=DID, to=customer (must swap for display)
        our_did = os.getenv("SMARTFLO_CALLER_ID", "918065250182").strip().lstrip("+")
        from_normalized = str(from_num).strip().lstrip("+")
        is_outbound_ctc = from_normalized == our_did or from_normalized == our_did.lstrip("91")
        # Display number: always the customer's number
        display_number = to_num if is_outbound_ctc else from_num
        call_direction = "outbound" if is_outbound_ctc else "inbound"

        if is_outbound_ctc:
            logger.info(f"[PipecatBridge] Outbound click-to-call detected — customer: {to_num}")

        dialled = to_num if not is_outbound_ctc else from_num
        try:
            from .numbers import resolve_inbound
            async with async_session() as route_db:
                route = await resolve_inbound(route_db, dialled) if not is_outbound_ctc else None
            self._route_found = bool(route and route.found)
            if route and route.found:
                self._tenant_id, self._org_suspended = route.tenant_id, route.org_suspended
                if route.workflow_id is not None:
                    self._workflow_id[0] = route.workflow_id
                logger.info(f"[PipecatBridge] Number {route.number} -> org={route.tenant_id} workflow={route.workflow_id}"
                            f"{' (organization suspended)' if route.org_suspended else ''}")
            else:
                logger.info(f"[PipecatBridge] Number {dialled} is not in the inventory; using the active workflow")
        except Exception as exc:                      # routing must never take a call down
            logger.warning(f"[PipecatBridge] number routing failed, using the active workflow: {exc}")

        async with async_session() as session:
            result = await session.execute(select(Call).where(Call.call_sid == call_sid))
            call = result.scalars().first()
            if not call:
                call = Call(
                    call_sid=call_sid,
                    caller_number=display_number,   # ← customer number (not DID)
                    called_number=to_num if not is_outbound_ctc else from_num,
                    status=CallStatus.ACTIVE,
                    live_status="in-progress",
                    source="smartflo-stream",
                    tenant_id=self._tenant_id,
                    started_at=datetime.utcnow(),
                )   # no agent yet: the one that answers is recorded as soon as it is known (_record_current_agent)
                session.add(call)
                await session.commit()
                await dashboard_manager.broadcast({"type": "call_created", "call": call.to_dict()})

            self._db_call_id[0] = call.id
            self._caller_phone[0] = display_number
            call.live_status = "in-progress"
            await session.commit()
            await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})

        await event_logger.emit("call.stream_started", call_sid=call_sid, stream_sid=stream_sid, caller=display_number, called=to_num, direction=call_direction, source="stream")

        # Check for API-triggered outbound context
        from .outbound_api import retrieve_outbound_context
        outbound_ctx = await retrieve_outbound_context(call_sid)
        if not outbound_ctx and self._caller_phone[0]:
            outbound_ctx = await retrieve_outbound_context(self._caller_phone[0])
        if outbound_ctx:
            self._outbound_ctx = outbound_ctx
            logger.info(f"[PipecatBridge] Loaded API outbound context for recipient: {outbound_ctx.get('recipient_name')}")
            await self._adopt_outbound_context(outbound_ctx)

        if self._caller_phone[0]:
            normalized = self._caller_phone[0].replace("whatsapp:", "")
            ACTIVE_CALL_QUEUES[normalized] = asyncio.Queue()

            async def _process_injections():
                while True:
                    msg = await ACTIVE_CALL_QUEUES[normalized].get()
                    if msg is None:
                        break
                    logger.info(f"[PipecatBridge] Injected cross-channel: {msg}")
                    self._chat_history.append({"role": "system", "content": msg})

            self._injection_task = asyncio.create_task(_process_injections())

        asyncio.create_task(self._send_greeting())

    async def _adopt_outbound_context(self, ctx: Dict[str, Any]):
        """An outbound call (API or a scheduled follow-up) carries who it is for and why it is calling.
        Take its company and agent, record its metadata on the call (so tools and the call-ended hook can find
        the task), and give the model the brief so it knows the purpose of the call."""
        try:
            if ctx.get("tenant_id") is not None:
                self._tenant_id = ctx["tenant_id"]
            if ctx.get("workflow_id") is not None:
                self._workflow_id[0] = ctx["workflow_id"]
            await self._take_over_planned_call(ctx)
            meta = ctx.get("metadata") or {}
            if self._db_call_id[0] and (meta or ctx.get("tenant_id") is not None):
                async with async_session() as session:
                    call = (await session.execute(select(Call).where(Call.id == self._db_call_id[0]))).scalars().first()
                    if call is not None:
                        if meta:
                            call.metadata_json = {**(call.metadata_json or {}), **meta}
                        if ctx.get("tenant_id") is not None:
                            call.tenant_id = ctx["tenant_id"]         # a call row made here is stamped with the platform's company by default
                        task_id = meta.get("task_id")
                        if task_id:
                            from .models import FollowUpTask
                            task = await session.get(FollowUpTask, int(task_id))
                            if task is not None:
                                task.last_call_id = call.id
                        await session.commit()
            brief = ctx.get("message_context")
            if brief:
                self._chat_history.append({"role": "system", "content": f"THIS CALL (why you are calling):\n{brief}"})
        except Exception as exc:                      # the brief must never take a call down
            logger.warning(f"[PipecatBridge] could not adopt the outbound context: {exc}")

    def _gemini_session_choices(self) -> Dict[str, Any]:
        """This agent's own Gemini Live choices (model, turn-taking, thinking level) as constructor arguments. Empty when it has none."""
        from .gemini_live import live_settings
        chosen = live_settings(getattr(self, "_gemini_overrides", None))
        out: Dict[str, Any] = {}
        if chosen["model"]:
            out["model"] = chosen["model"]
        if chosen["vad"]:
            out["vad"] = chosen["vad"]
        if chosen["thinking_level"]:
            out["thinking_level"] = chosen["thinking_level"]
        if chosen.get("transcript_languages"):
            out["transcript_languages"] = chosen["transcript_languages"]
        return out

    async def _take_over_planned_call(self, ctx: Dict[str, Any]):
        """A scheduled call already has a row (its company, its task). The stream just made a second one with the carrier's id.
        Move the stream's facts onto the scheduled row, drop the second one, and carry on with the scheduled one."""
        planned_id, current = ctx.get("call_row_id"), self._db_call_id[0]
        if not planned_id or not current or int(planned_id) == int(current):
            return
        async with async_session() as session:
            planned = await session.get(Call, int(planned_id))
            extra = await session.get(Call, int(current))
            if planned is None or extra is None:
                return
            planned.call_sid = extra.call_sid
            planned.caller_number = extra.caller_number
            planned.called_number = extra.called_number
            planned.source = extra.source
            planned.started_at = extra.started_at
            planned.live_status = "in-progress"
            await session.delete(extra)
            await session.commit()
        self._db_call_id[0] = int(planned_id)
        try:
            async with async_session() as session:
                fresh = await session.get(Call, int(planned_id))
                await dashboard_manager.broadcast({"type": "call_updated", "call": fresh.to_dict()})
        except Exception:
            pass

    @staticmethod
    def _resolve_gemini_tools(node_data: dict):
        """Pick the tool set for a Gemini call by workflow domain/agent — mirrors the Groq router's
        logic so Gemini and the scripted path expose the same tools per workflow."""
        nd = node_data or {}
        if nd.get("tools_explicit"):
            # An agent made from a description or a template says exactly which tools it has (often none): nothing is guessed from
            # its name or its words. [] means no tools, which is different from None (not said: the guesses below apply).
            names = [str(t) for t in (nd.get("tools") or [])]
            if nd.get("use_rag") and DOCUMENT_TOOL not in names:
                names.append(DOCUMENT_TOOL)
            return names
        from .sanjeevani_tools import uses_patient_care_tools
        if uses_patient_care_tools(nd.get("tools")):
            return list(nd.get("tools"))          # chosen by name in the builder; never second-guessed from the agent's name
        agent_name = str(nd.get("_node_name") or nd.get("name") or "").lower()
        sys_p = str(nd.get("system_prompt") or "").lower()
        is_medical = (str(nd.get("domain", "")).lower() == "medical"
                      or any(k in agent_name for k in ["clinic", "patient", "doctor", "medical", "appointment"])
                      or any(k in sys_p for k in ["clinic", "appointment", "doctor", "patient"]))
        if is_medical:
            return ["find_doctor", "schedule_appointment", "create_kyc_session",
                    "send_kyc_link_email", "get_kyc_status"]
        it_tools = ["create_symphony_ticket", "get_symphony_ticket_status",
                    "list_symphony_incidents", "check_complaint_status"]
        tool_names = list(nd.get("tools") or [])
        if not tool_names and any(k in agent_name for k in [
                "specialist", "service desk", "reception", "incident", "support",
                "triage", "inquiry", "complaint", "general", "technical", "status", "help desk", "helpdesk"]):
            tool_names = list(it_tools)
        elif tool_names and any(t in str(tool_names).lower() for t in ["ticket", "symphony", "incident", "complaint"]):
            tool_names = list(dict.fromkeys(tool_names + it_tools))
        # last resort: if nothing matched but the prompt is clearly IT/helpdesk, give the ticket tools
        if not tool_names and any(k in sys_p for k in ["ticket", "incident", "it service", "service desk", "helpdesk"]):
            tool_names = list(it_tools)
        return tool_names or None

    async def _adopt_workflow_tenant(self, tenant_id: int):
        """A call to a number that is not in the inventory is served by the active workflow, so it belongs to that workflow's
        company (not to the platform's): its patients, its documents, its records."""
        if getattr(self, "_route_found", False) or self._tenant_id == tenant_id:
            return
        self._tenant_id = tenant_id
        try:
            async with async_session() as db:
                call = await db.get(Call, self._db_call_id[0]) if self._db_call_id[0] else None
                if call is not None and call.tenant_id != tenant_id:
                    call.tenant_id = tenant_id
                    await db.commit()
        except Exception as exc:
            logger.warning("[GeminiLive] could not move the call to the workflow's company: %s", exc)

    async def _recognise_caller(self) -> str:
        """A patient who phones in: match the number to the company's patient directory. The match is a hint, not proof, so the
        call is only told who it probably is; nothing about them is shared until they confirm their date of birth."""
        from .sanjeevani_desk import patient_by_phone
        try:
            async with async_session() as db:
                patient = await patient_by_phone(db, self._tenant_id, self._caller_phone[0])
                if patient is None:
                    return ("CALLER: this number is not in our patient directory. Treat them as a new patient: you will need their "
                            "name, and for a booking their date of birth.")
                call = await db.get(Call, self._db_call_id[0]) if self._db_call_id[0] else None
                if call is not None:
                    call.metadata_json = {**(call.metadata_json or {}), "patient_id": patient.id, "recognised_by_phone": True}
                    await db.commit()
            return ("CALLER: this number matches a patient in our directory, but that is not proof of who is speaking. Do not say their "
                    "name or anything about them first. If they want anything about their visits or medicines, ask for their date "
                    "of birth and call confirm_identity. For a new booking you can use the name they give you.")
        except Exception as exc:
            logger.warning("[GeminiLive] caller recognition failed: %s", exc)
            return ""

    async def _graph_persona(self, graph, wf_row):
        """(system_instruction, greeting, voice) for a workflow with an orchestrator and specialists. The first step is the
        orchestrator for a call coming in, or the outbound step for a call we placed; the call context is the brief we placed it with,
        or, for a call coming in, what we know about the number."""
        ctx = getattr(self, "_outbound_ctx", None)
        entry = graph.entry_outbound if ctx else graph.entry_inbound
        spec = graph.nodes[entry]
        self._gemini_graph, self._gemini_entry = graph, entry
        self._gemini_agent_name = spec.name or getattr(self, "_gemini_agent_name", "")
        if ctx:
            self._gemini_call_context = "\n\n".join(x for x in (ctx.get("message_context"), ctx.get("system_prompt")) if x)
            greeting = ctx.get("greeting") or spec.greeting
        else:
            self._gemini_call_context = await self._recognise_caller()
            greeting = spec.greeting
        return spec.prompt, greeting or "Thank you for calling. How can I help you today?", spec.voice

    async def _record_current_agent(self, name: str):
        """Put the agent that answers on the call record and on the console. A call row is made before anyone knows which agent
        will take it, so without this the console shows whatever the row was made with until the first handover (if there is one)."""
        call_id = self._db_call_id[0]
        if not name or not call_id:
            return
        try:
            async with async_session() as db:
                call = await db.get(Call, call_id)
                if call is None or call.current_agent == name:
                    return
                call.current_agent = name
                await db.commit()
                await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
        except Exception as exc:                      # a label must never take a call down
            logger.warning("[PipecatBridge] could not record the agent on the call: %s", exc)

    async def _gemini_persona(self):
        """(system_instruction, greeting, voice) for the active workflow, for a Gemini Live call.
        Also resolves self._gemini_tool_names — the tools this workflow's agent should expose to Gemini
        (medical gets the clinic/KYC set, IT/service-desk gets the Summit ticket set) so e.g. a ticket
        workflow on Gemini can actually create tickets instead of being stuck with medical tools."""
        system_instruction, voice = "", None
        greeting = "Thank you for calling. How can I help you today?"
        self._gemini_tool_names = None   # None -> processor falls back to its medical default
        self._gemini_kb_docs = []        # the documents a single agent was given (none named: all of the company's)
        self._gemini_graph, self._gemini_entry, self._gemini_call_context = None, None, ""
        self._gemini_agent_name = ""      # who answers first: shown on the call record and the console
        try:
            from .models import WorkflowNode, WorkflowEdge, Workflow
            from .gemini_graph import build_graph
            wid = self._workflow_id[0]
            async with async_session() as db:
                if wid is None:   # dialled number not in inventory -> use the system active workflow
                    from .tenancy import system_tenant_id_cached
                    w = (await db.execute(select(Workflow).where(Workflow.is_active == True).order_by(
                        (Workflow.tenant_id == system_tenant_id_cached()).desc(), Workflow.id))).scalars().first()
                    wid = w.id if w else None
                nodes = (await db.execute(
                    select(WorkflowNode).where(WorkflowNode.workflow_id == wid))).scalars().all()
            self._gemini_overrides = {}
            async with async_session() as db2:
                wf_row = await db2.get(Workflow, wid) if wid is not None else None
                self._gemini_overrides = ((wf_row.settings_json or {}).get("gemini_live") or {}) if wf_row is not None else {}
                edges = (await db2.execute(select(WorkflowEdge).where(WorkflowEdge.workflow_id == wid))).scalars().all() if wid is not None else []
            self._gemini_agent_name = (wf_row.name or "") if wf_row is not None else ""
            if wf_row is not None and wf_row.tenant_id is not None:
                await self._adopt_workflow_tenant(wf_row.tenant_id)
            graph = build_graph(nodes, edges)
            if graph is not None:
                return await self._graph_persona(graph, wf_row)
            matched = {}
            for n in nodes:
                d = getattr(n, "data_json", None) or {}
                if d.get("system_prompt"):
                    system_instruction = d["system_prompt"]
                    voice = d.get("voice") or voice
                    opener = d.get("opening_line") or d.get("greeting")
                    if opener:
                        greeting = opener
                    matched = dict(d)
                    self._gemini_kb_docs = [str(x) for x in (d.get("kb_docs") or [])]
                    matched["_node_name"] = getattr(n, "name", "") or d.get("name", "")
                    self._gemini_agent_name = matched["_node_name"] or self._gemini_agent_name
                    break
            self._gemini_tool_names = self._resolve_gemini_tools(matched)
        except Exception as exc:
            logger.warning("[GeminiLive] persona lookup failed: %s", exc)
        try:
            g = await self._router_service.get_initial_greeting(self._workflow_id[0])
            greeting = g.get("text", greeting)
        except Exception:
            pass
        ctx = getattr(self, "_outbound_ctx", None)
        if ctx:
            brief = ctx.get("message_context")
            if brief:
                system_instruction = (system_instruction or "") + f"\n\n### This call (why you are calling)\n{brief}"
            if ctx.get("system_prompt"):
                system_instruction += f"\n\n{ctx['system_prompt']}"
            greeting = ctx.get("greeting") or greeting
        if not system_instruction:
            system_instruction = ("You are a warm, professional voice receptionist. Keep replies short "
                                  "and conversational, ask one question at a time, and never invent facts.")
        # our stored voice is a TTS voice (e.g. "Aarohi"); Gemini Live uses its own voice names, so only
        # pass it through if it is a Gemini voice, else let the processor use its default.
        from .gemini_graph import GEMINI_VOICES
        voice = voice if (voice or "").lower() in GEMINI_VOICES else None
        return system_instruction, greeting, voice

    async def _send_greeting(self):
        """Pre-cached greeting playback."""
        from .gemini_live_processor import use_gemini_live
        if use_gemini_live(self._workflow_id[0]):
            return  # Gemini Live greets the caller itself; skip the TTS greeting to avoid a double hello
        self._session_state["is_agent_speaking"] = True
        try:
            if hasattr(self, "_outbound_ctx") and self._outbound_ctx:
                greeting = self._outbound_ctx.get("greeting", "Hello, how can I assist you today?")
                agent_voice = self._outbound_ctx.get("voice", "Aarohi")
                initial_agent = "Outbound Voice Agent"
                system_prompt = self._outbound_ctx.get("system_prompt")
                if system_prompt:
                    self._chat_history.append({"role": "system", "content": system_prompt})
            else:
                greeting_data = await self._router_service.get_initial_greeting()
                greeting = greeting_data.get("text", "Thank you for calling. How can I help you today?")
                agent_voice = greeting_data.get("voice", "Aarohi")
                initial_agent = greeting_data.get("agent", "Primary Agent")
                self._current_node_id[0] = greeting_data.get("node_id")

            self._chat_history.append({"role": "assistant", "content": greeting})

            await event_logger.emit("call.agent_active", call_sid=self._call_sid[0], node_id=self._current_node_id[0], agent_name=initial_agent, voice=agent_voice)
            await event_logger.emit("call.transcript_turn", call_sid=self._call_sid[0], role="agent", text=greeting, seq=0, voice=agent_voice)

            db_id = self._db_call_id[0]
            if db_id:
                async with async_session() as session:
                    result = await session.execute(select(Call).where(Call.id == db_id))
                    call = result.scalars().first()
                    if call:
                        call.current_agent = initial_agent
                    session.add(TranscriptMessage(call_id=db_id, role="agent", message=greeting, sequence_number=0))
                    await session.commit()
                    if call:
                        await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
                    await dashboard_manager.broadcast({"type": "transcript", "call_id": db_id, "role": "agent", "message": greeting, "sequence_number": 0, "timestamp": datetime.utcnow().isoformat()})

            cache_key = f"{greeting}_{agent_voice}"
            if cache_key in _GREETING_PCM_CACHE:
                tts_pcm = _GREETING_PCM_CACHE[cache_key]
                logger.info("[PipecatBridge] Greeting delivered from PCM cache.")
            else:
                tts_pcm = await self._tts_service.synthesize_bytes(greeting, format="pcm", speaker=agent_voice)
                if tts_pcm:
                    _GREETING_PCM_CACHE[cache_key] = tts_pcm

            if tts_pcm and not self._session_state.get("interrupted"):
                tts_sr = (
                    getattr(self._tts_service, "sample_rate", None)
                    or getattr(getattr(self._tts_service, "settings", None), "sample_rate", None)
                    or 8000
                )
                ulaw_b64 = self._transcoder.pcm16_to_ulaw_b64(tts_pcm, input_sample_rate=tts_sr)
                chunk_size = 640
                for j in range(0, len(ulaw_b64), chunk_size):
                    if self._session_state.get("interrupted"):
                        break
                    chunk = ulaw_b64[j:j + chunk_size]
                    try:
                        await self.websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": self._stream_sid[0],
                            "media": {"payload": chunk},
                        }))
                        await asyncio.sleep(0.04)
                    except Exception:
                        break

                if not self._session_state.get("interrupted"):
                    try:
                        await self.websocket.send_text(json.dumps({
                            "event": "mark",
                            "streamSid": self._stream_sid[0],
                            "mark": {"name": "greeting_complete"},
                        }))
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[PipecatBridge] Greeting error: {e}", exc_info=True)
        finally:
            self._session_state["is_agent_speaking"] = False

    async def _run_pipecat_pipeline(self):
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.task import PipelineTask, PipelineParams
        from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport, FastAPIWebsocketParams
        from pipecat.serializers.twilio import TwilioFrameSerializer
        from pipecat.frames.frames import Frame, EndFrame
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        from pipecat.processors.audio.vad_processor import VADProcessor

        class SmartfloFrameSerializer(TwilioFrameSerializer):
            async def deserialize(self, data: str | bytes) -> Frame | None:
                try:
                    if isinstance(data, (str, bytes)):
                        msg = json.loads(data)
                        event = msg.get("event")
                        if event in ("stop", "closed", "hangup"):
                            logger.info(f"[SmartfloSerializer] Received '{event}' event from carrier -> emitting EndFrame.")
                            return EndFrame()
                except Exception:
                    pass
                return await super().deserialize(data)

        from .pipecat_processors import (
            SmartfloSTTProcessor,
            AgentRouterProcessor,
            SmartfloTTSProcessor,
            CallMetricsCollector,
        )

        self._metrics_collector = CallMetricsCollector()

        serializer = SmartfloFrameSerializer(
            stream_sid=self._stream_sid[0] or "",
            params=TwilioFrameSerializer.InputParams(
                auto_hang_up=False,
                sample_rate=16000,
            ),
        )

        transport = FastAPIWebsocketTransport(
            websocket=self.websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                serializer=serializer,
            ),
        )

        # ── Gemini 3.8 Live mode (flag-gated per workflow) ──────────────────────────────────────
        # Replace the whole VAD -> STT -> AgentRouter -> TTS middle with a single Gemini Live session:
        # native speech-to-speech, Gemini is the brain. Groq path untouched for every other workflow.
        from .gemini_live_processor import GeminiLiveProcessor, use_gemini_live
        if use_gemini_live(self._workflow_id[0]):
            sys_instr, greeting, voice = await self._gemini_persona()
            await self._record_current_agent(self._gemini_agent_name)
            gproc = GeminiLiveProcessor(
                system_instruction=sys_instr, greeting=greeting, voice=voice,
                websocket=self.websocket, stream_sid_ref=self._stream_sid,
                metrics_collector=self._metrics_collector,
                tool_executor=self._tool_exec, call_id_ref=self._db_call_id,
                caller_phone_ref=self._caller_phone,
                tool_names=getattr(self, "_gemini_tool_names", None), kb_docs=getattr(self, "_gemini_kb_docs", None),
                guard_engine=getattr(self._router_service, "jev_engine", None),
                graph=self._gemini_graph, entry_id=self._gemini_entry, call_context=self._gemini_call_context,
                **self._gemini_session_choices())
            g_pipeline = Pipeline([transport.input(), gproc, transport.output()])
            g_task = PipelineTask(g_pipeline, params=PipelineParams(
                allow_interruptions=True, audio_in_sample_rate=16000, audio_out_sample_rate=8000))

            @transport.event_handler("on_client_disconnected")
            async def _gemini_disconnected(*args, **kwargs):
                logger.info("[PipecatBridge] client disconnected -> ending Gemini Live task.")
                try:
                    await g_task.queue_frame(EndFrame())
                except Exception:
                    await g_task.cancel()

            logger.info("[PipecatBridge] GEMINI LIVE mode for workflow %s", self._workflow_id[0])
            await PipelineRunner().run(g_task)
            return

        vad_analyzer = SileroVADAnalyzer(
            sample_rate=16000,
            params=VADParams(
                stop_secs=self.config.vad.stop_secs,
                start_secs=self.config.vad.start_secs,
                confidence=self.config.vad.confidence,
                min_volume=self.config.vad.min_volume,
            )
        )
        vad_proc = VADProcessor(vad_analyzer=vad_analyzer)

        stt_proc = SmartfloSTTProcessor(
            stt_service=self._stt_service,
            config=self.config,
            session_state_ref=self._session_state,
            websocket=self.websocket,
            stream_sid_ref=self._stream_sid,
            sample_rate=16000,
            metrics_collector=self._metrics_collector,
        )

        asyncio.create_task(self._router_service.warm_up())      # the greeting plays meanwhile; the first turn then starts warm
        router_proc = AgentRouterProcessor(
            router=self._router_service,
            config=self.config,
            session_state_ref=self._session_state,
            db_call_id_ref=self._db_call_id,
            call_sid_ref=self._call_sid,
            caller_phone_ref=self._caller_phone,
            stream_sid_ref=self._stream_sid,
            chat_history=self._chat_history,
            current_node_id_ref=self._current_node_id,
            transcript_sequence_ref=self._transcript_sequence,
            telephony_service=twilio_sync_client,
            metrics_collector=self._metrics_collector,
            workflow_id_ref=self._workflow_id,
        )

        tts_proc = SmartfloTTSProcessor(
            tts_service=self._tts_service,
            config=self.config,
            session_state_ref=self._session_state,
            call_sid_ref=self._call_sid,
            telephony_service=twilio_sync_client,
            metrics_collector=self._metrics_collector,
        )

        pipeline = Pipeline([
            transport.input(),
            vad_proc,
            stt_proc,
            router_proc,
            tts_proc,
            transport.output(),
        ])

        task = PipelineTask(
            pipeline,
            params=PipelineParams(
                allow_interruptions=True,
                audio_in_sample_rate=16000,
                audio_out_sample_rate=8000,
            ),
        )

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(*args, **kwargs):
            logger.info("[PipecatBridge] Client WebSocket disconnected -> ending pipeline task.")
            try:
                await task.queue_frame(EndFrame())
            except Exception:
                await task.cancel()

        runner = PipelineRunner()
        await runner.run(task)

    async def _cleanup(self):
        duration_sec = round((datetime.utcnow() - self._session_start_time).total_seconds(), 2)
        await event_logger.emit("call.stream_stopped", call_sid=self._call_sid[0],
            duration_seconds=duration_sec, chunks_count=self._media_chunks_count,
            total_bytes=self._total_bytes_received)

        if self._injection_task:
            self._injection_task.cancel()
        if self._caller_phone[0]:
            normalized = self._caller_phone[0].replace("whatsapp:", "")
            if normalized in ACTIVE_CALL_QUEUES:
                del ACTIVE_CALL_QUEUES[normalized]

        db_id = self._db_call_id[0]
        if db_id:
            async with async_session() as session:
                result = await session.execute(
                    select(Call).options(
                        selectinload(Call.transcript_messages),
                        selectinload(Call.tickets),
                        selectinload(Call.appointments),
                    )
                    .where(Call.id == db_id)
                )
                call = result.scalars().first()
                if call:
                    call.status = CallStatus.COMPLETED
                    call.live_status = "completed"
                    call.ended_at = datetime.utcnow()
                    call.duration_seconds = duration_sec
                    await session.commit()
                    await dashboard_manager.broadcast({"type": "call_completed", "call": call.to_dict()})
                    if (call.metadata_json or {}).get("task_id"):
                        try:
                            from .followup_scheduler import settle_followup_call
                            await settle_followup_call(call.id)
                        except Exception as exc:
                            logger.warning(f"[PipecatBridge] could not settle the follow-up task: {exc}")
                    try:
                        provider = event_logger.get_telephony_provider(call.source, self._call_sid[0])
                        transcripts = []
                        for msg in (call.transcript_messages or []):
                            speaker = (
                                f"Agent ({call.current_agent or 'Service Desk'})"
                                if msg.role == "agent"
                                else f"Caller ({self._caller_phone[0] or 'User'})"
                            )
                            transcripts.append({
                                "sequence": msg.sequence_number,
                                "speaker": speaker,
                                "role": msg.role,
                                "text": msg.message,
                            })
                        ticket_info = {"created": False, "ticket_id": None, "issue_summary": None, "priority": None}
                        if call.tickets:
                            latest = call.tickets[0]
                            ticket_info = {
                                "created": True,
                                "ticket_id": latest.ticket_number or latest.id,
                                "issue_summary": latest.issue_description or latest.title,
                                "priority": latest.priority,
                                "status": latest.status,
                            }
                        # Medical receptionist: patient details + appointment for the per-call JSON
                        patient_details = None
                        appointment_info = None
                        if call.appointments:
                            appt = call.appointments[0]
                            patient_details = {
                                "patient_name": appt.patient_name,
                                "contact": appt.caller_number or self._caller_phone[0],
                                "problem": appt.problem,
                                "is_new_patient": bool(appt.is_new_patient),
                            }
                            appointment_info = {
                                "appointment_id": appt.appointment_id,
                                "doctor": appt.doctor_name,
                                "specialty": appt.doctor_specialty,
                                "datetime": appt.appointment_time,
                                "status": appt.status,
                            }
                        # Identity (KYC) verification outcome — minimal: no Aadhaar number, no photos.
                        kyc_info = None
                        try:
                            from . import kyc_core
                            _k = (await kyc_core.summaries_for_calls([db_id])).get(db_id)
                            if _k:
                                kyc_info = {
                                    "status": _k["status"], "source": "demo" if _k.get("demo") else "uidai",
                                    "verified_name": _k["verified_name"], "aadhaar_last4": _k["aadhaar_last4"],
                                    "face_score": _k["face_score"], "liveness_passed": _k["liveness_passed"],
                                    "verified_at": _k["verified_at"],
                                }
                        except Exception as _kyc_exc:
                            logger.debug(f"[GCS] KYC summary skipped: {_kyc_exc}")
                        call_kpis = {}
                        if hasattr(self, "_metrics_collector") and self._metrics_collector:
                            call_kpis = self._metrics_collector.to_dict()
                        call_summary = {
                            "call_id": db_id,
                            "telephony": {
                                "provider": provider,
                                "channel": call.source or "stream",
                                "call_sid": self._call_sid[0],
                                "caller_phone": self._caller_phone[0],
                                "called_phone": call.called_number,
                            },
                            "status": "completed",
                            "started_at": self._session_start_time.isoformat(),
                            "ended_at": datetime.utcnow().isoformat(),
                            "duration_seconds": duration_sec,
                            "summary": call.summary,
                            "routing": {"final_agent": call.current_agent or "Incident Management Assistant"},
                            "ticket": ticket_info,
                            "patient_details": patient_details,
                            "appointment": appointment_info,
                            "kyc": kyc_info,
                            "transcript": transcripts,
                            "telephony_stats": {
                                "duration_seconds": duration_sec,
                                "audio_chunks": self._media_chunks_count,
                                "total_bytes": self._total_bytes_received,
                            },
                            "call_kpis": call_kpis,
                        }
                        await event_logger.upload_call_json(call_summary)

                        if hasattr(self, "_outbound_ctx") and self._outbound_ctx:
                            callback_url = self._outbound_ctx.get("callback_url")
                            if callback_url:
                                async def _deliver_webhook(target_url: str, payload_data: dict):
                                    try:
                                        import httpx
                                        async with httpx.AsyncClient(timeout=10.0) as client:
                                            resp = await client.post(target_url, json=payload_data)
                                            logger.info(f"[PipecatBridge] Webhook callback delivered to {target_url}: HTTP {resp.status_code}")
                                    except Exception as w_err:
                                        logger.warning(f"[PipecatBridge] Webhook delivery failed ({w_err}) for {target_url}")
                                asyncio.create_task(_deliver_webhook(callback_url, call_summary))
                    except Exception as exc:
                        logger.warning(f"[PipecatBridge] Call completion processing notice: {exc}")


MediaStreamSession = PipecatMediaSession


def create_media_session(websocket: WebSocket) -> PipecatMediaSession:
    return PipecatMediaSession(websocket=websocket, config=get_config())


# Set TELEPHONY_STREAM_SECRET in production; without it each process signs with its own random secret.
_STREAM_SECRET_FALLBACK = secrets.token_hex(32)


def generate_stream_token(call_sid: str) -> str:
    secret = os.getenv("TELEPHONY_STREAM_SECRET", os.getenv("SECRET_KEY", _STREAM_SECRET_FALLBACK))
    return hmac.new(secret.encode("utf-8"), (call_sid or "").encode("utf-8"), hashlib.sha256).hexdigest()


def verify_stream_token(call_sid: str, token: str) -> bool:
    if not token or not call_sid:
        return False
    return hmac.compare_digest(generate_stream_token(call_sid), token)


@router.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call(request: Request):
    # Validate Tata webhook secret header if configured
    tata_webhook_secret = os.getenv("TATA_WEBHOOK_SECRET", "").strip()
    if tata_webhook_secret:
        incoming_secret = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(incoming_secret, tata_webhook_secret):
            logger.warning(
                "[IncomingCall] Rejected webhook — invalid or missing X-Webhook-Secret from %s",
                request.client.host if request.client else "unknown"
            )
            return Response(status_code=403, content="Forbidden")

    # Log ALL fields Tata sends — handles both GET (query params) and POST (form/JSON body)
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8", errors="replace")

    # Try JSON first, then form data (Tata sends JSON even with form-urlencoded Content-Type)
    form_data = {}
    try:
        import json as _json
        form_data = _json.loads(body_str) if body_str.strip().startswith("{") else {}
    except Exception:
        form_data = {}
    if not form_data and request.method == "POST":
        try:
            raw_form = await request.form()
            form_data = dict(raw_form)
        except Exception:
            pass

    # Merge query params + body — covers GET and POST
    all_fields = dict(request.query_params)
    all_fields.update(form_data)
    logger.info("[IncomingCall] Method=%s body=%s", request.method, body_str)
    logger.info("[IncomingCall] All fields (query+form+json): %s", all_fields)
    logger.info("[IncomingCall] All headers: %s", dict(request.headers))

    # Flexible field reading — handles any casing/naming Tata uses
    call_sid = (
        all_fields.get("CallSid") or all_fields.get("call_sid") or
        all_fields.get("callsid") or all_fields.get("call_id") or
        all_fields.get("session_id") or all_fields.get("SessionId") or
        all_fields.get("uuid") or all_fields.get("UUID")
    )
    caller = (
        all_fields.get("From") or all_fields.get("from") or
        all_fields.get("caller") or all_fields.get("caller_number") or
        all_fields.get("CallerNumber") or all_fields.get("ANI") or "Unknown"
    )
    called = (
        all_fields.get("To") or all_fields.get("to") or
        all_fields.get("fo") or all_fields.get("did") or all_fields.get("DID") or
        all_fields.get("called") or all_fields.get("CalledNumber") or
        all_fields.get("DNIS") or ""
    )
    logger.info("[IncomingCall] Resolved — call_sid=%s, caller=%s, called=%s", call_sid, caller, called)
    if not call_sid:
        # Tata sometimes sends empty body — generate a call_sid so the session is tracked
        import uuid as _uuid
        call_sid = f"tata-{_uuid.uuid4().hex[:12]}"
        logger.warning("[IncomingCall] No call_sid in request, generated: %s. Fields: %s", call_sid, list(all_fields.keys()))
    async with async_session() as session:
        result = await session.execute(select(Call).where(Call.call_sid == call_sid))
        call = result.scalars().first()
        if not call:
            call = Call(
                call_sid=call_sid,
                caller_number=caller,
                called_number=called,
                status=CallStatus.ACTIVE,
                live_status="ringing",
                source="tata-smartflo",
                started_at=datetime.utcnow(),
                current_agent="custom-agent",
            )
            session.add(call)
            await session.commit()
            await dashboard_manager.broadcast({"type": "call_created", "call": call.to_dict()})
    await event_logger.emit("call.created", call_sid=call_sid, caller=caller, called=called, source="tata-smartflo")
    domain = "voicebot-agent.stldigitaltech.com"
    stream_token = generate_stream_token(call_sid)
    ws_url = f"wss://{domain}/ws/stream?token={stream_token}&call_sid={call_sid}"
    logger.info("[IncomingCall] Returning ws_url=%s", ws_url)
    # Tata SmartFlo Dynamic endpoint expects JSON {"url": "wss://..."}
    # Twilio expects TwiML XML — detect by Accept header
    accept = request.headers.get("accept", "")
    if "xml" in accept:
        twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Connect><Stream url="{ws_url}" /></Connect></Response>'
        return Response(content=twiml, media_type="application/xml")
    return Response(content=json.dumps({"url": ws_url}), media_type="application/json")


@router.websocket("/ws/stream")
@router.websocket("/ws/media-stream")
async def media_stream_endpoint(websocket: WebSocket):
    require_stream_auth = os.getenv("REQUIRE_STREAM_AUTH", "false").lower() == "true"
    token = websocket.query_params.get("token")
    call_sid = websocket.query_params.get("call_sid")
    if require_stream_auth:
        if not token or not call_sid or not verify_stream_token(call_sid, token):
            logger.warning("[PipecatBridge] Unauthorized WebSocket stream attempt: call_sid=%s", call_sid)
            await websocket.close(code=4403)
            return
    session = create_media_session(websocket)
    await session.handle_session()