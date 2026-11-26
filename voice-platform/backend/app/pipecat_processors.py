"""
Pipecat FrameProcessor Wrappers for STL Voice Agent Services
Adapts our existing STT / AgentRouter / TTS services into Pipecat's pipeline.
"""

import re
import logging
import asyncio
import os
import io
import wave
import statistics
from datetime import datetime
from typing import Optional, Dict, Any, List

import struct
try:
    import audioop
except ImportError:
    import audioop_lts as audioop

from .interfaces import BaseSTTService, BaseTTSService
from .agent_router import AgentRouter
from .event_logger import event_logger
from .langfuse_tracer import langfuse_tracer
from .database import async_session
from .models import Call, CallStatus, TranscriptMessage
from .websocket_manager import dashboard_manager
from .config import AppConfig

from sqlalchemy import select

from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.frames.frames import (
    OutputAudioRawFrame,
    TextFrame,
    TranscriptionFrame,
    EndFrame,
    Frame,
    StartFrame,
    CancelFrame,
    InterruptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)

logger = logging.getLogger(__name__)

_NOISE_TOKENS = frozenset(["okay", "ok", "thank you", "thanks", "you", "bye", "yeah", "i love you", "love you"])

def _is_noise_hallucination(transcript: str, pcm_duration_sec: float) -> bool:
    clean = transcript.strip().lower().rstrip(".,!?")
    return clean in _NOISE_TOKENS and pcm_duration_sec < 0.7


class CallMetricsCollector:
    """Accumulates per-turn latency metrics for a single call session."""

    def __init__(self):
        self.stt_latencies_ms: List[float] = []
        self.llm_route_latencies_ms: List[float] = []
        self.tts_ttfb_latencies_ms: List[float] = []
        self.total_turn_latencies_ms: List[float] = []
        self.turn_count: int = 0
        self.interruption_count: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.security_events: List[Dict[str, Any]] = []
        # Which engine served the call — picks the right cost model and lets the dashboard know the
        # per-stage (STT/LLM/TTS) latencies don't exist for a single speech-to-speech model.
        self.engine: str = "groq"

    def set_engine(self, name: str):
        self.engine = name or "groq"

    def set_tokens(self, prompt: int, completion: int):
        """Overwrite token totals (Gemini Live reports cumulative session usage, not per-turn deltas)."""
        self.prompt_tokens = int(prompt or 0)
        self.completion_tokens = int(completion or 0)

    def record_stt(self, ms: float):
        self.stt_latencies_ms.append(ms)

    def record_llm_route(self, ms: float):
        self.llm_route_latencies_ms.append(ms)

    def record_tts_ttfb(self, ms: float):
        self.tts_ttfb_latencies_ms.append(ms)

    def record_turn(self, total_ms: float):
        self.total_turn_latencies_ms.append(total_ms)
        self.turn_count += 1

    def record_interruption(self):
        self.interruption_count += 1

    def record_tokens(self, prompt: int, completion: int):
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    def record_security_event(self, turn: int, guard_result: Dict[str, Any]):
        self.security_events.append({"turn": turn, **guard_result})

    def _percentiles(self, data: List[float]) -> Dict[str, float]:
        if not data:
            return {}
        s = sorted(data)
        n = len(s)
        return {
            "min": round(s[0], 1),
            "max": round(s[-1], 1),
            "avg": round(statistics.mean(s), 1),
            "median": round(statistics.median(s), 1),
            "p90": round(s[int(n * 0.9)] if n >= 10 else s[-1], 1),
            "p95": round(s[int(n * 0.95)] if n >= 20 else s[-1], 1),
        }

    def to_dict(self) -> Dict[str, Any]:
        total_tokens = self.prompt_tokens + self.completion_tokens
        if self.engine == "gemini_live":
            # One speech-to-speech model: no separate STT/TTS spend. Gemini Live bills per token
            # (audio tokens dominate); a single blended rate is a good-enough estimate, env-overridable.
            rate = float(os.getenv("GEMINI_LIVE_COST_PER_MTOKEN", "5.0"))
            llm_cost = total_tokens / 1_000_000 * rate
            stt_cost = 0.0
            tts_cost = 0.0
        else:
            llm_cost = total_tokens / 1_000_000 * 0.59
            stt_cost = sum(self.stt_latencies_ms) / 1000.0 * 0.00005
            tts_chars_est = self.turn_count * 800
            tts_cost = tts_chars_est / 1000.0 * 0.015
        total_cost = llm_cost + stt_cost + tts_cost

        return {
            "engine": self.engine,
            "turn_count": self.turn_count,
            "interruption_count": self.interruption_count,
            "stt_latency_ms": self._percentiles(self.stt_latencies_ms),
            "llm_route_latency_ms": self._percentiles(self.llm_route_latencies_ms),
            "tts_ttfb_ms": self._percentiles(self.tts_ttfb_latencies_ms),
            "total_turn_latency_ms": self._percentiles(self.total_turn_latencies_ms),
            "token_usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": total_tokens,
            },
            "estimated_cost_usd": {
                "llm": round(llm_cost, 6),
                "stt": round(stt_cost, 6),
                "tts": round(tts_cost, 6),
                "total": round(total_cost, 6),
            },
            "security": {
                "guard_events": self.security_events,
                "max_threat_severity": max((e.get("threat_severity", 0) for e in self.security_events), default=0),
                "jailbreak_attempts": sum(1 for e in self.security_events if e.get("is_jailbreak")),
                "attack_types_seen": list(set(e.get("attack_type", "benign") for e in self.security_events if e.get("attack_type") != "benign")),
            },
        }


class SmartfloSTTProcessor(SegmentedSTTService):
    """
    Segmented STT Service that buffers audio on VADUserStartedSpeakingFrame,
    and runs transcription via GroqWhisper on VADUserStoppedSpeakingFrame.
    Also broadcasts InterruptionFrame when user starts speaking if bot is active.
    """

    def __init__(
        self,
        stt_service: BaseSTTService,
        config: AppConfig,
        session_state_ref: Dict[str, Any],
        websocket=None,
        stream_sid_ref: Optional[List] = None,
        sample_rate: int = 16000,
        metrics_collector: Optional[CallMetricsCollector] = None,
        **kwargs,
    ):
        super().__init__(sample_rate=sample_rate, trailing_silence_secs=0.25, **kwargs)
        self._stt = stt_service
        self._config = config
        self._session_state = session_state_ref
        self._websocket = websocket
        self._stream_sid_ref = stream_sid_ref
        self._call_metrics = metrics_collector

    async def _handle_user_started_speaking(self, frame: VADUserStartedSpeakingFrame):
        await super()._handle_user_started_speaking(frame)
        if self._session_state.get("is_agent_speaking"):
            logger.info("[STTProcessor] User spoke while agent speaking -> Broadcasting interruption.")
            self._session_state["is_agent_speaking"] = False
            self._session_state["interrupted"] = True
            if self._call_metrics:
                self._call_metrics.record_interruption()

            # Instant carrier audio buffer flush
            if self._websocket and self._stream_sid_ref and self._stream_sid_ref[0]:
                try:
                    import json
                    await self._websocket.send_text(json.dumps({
                        "event": "clear",
                        "streamSid": self._stream_sid_ref[0]
                    }))
                    logger.info(f"[STTProcessor] Sent carrier buffer clear for stream {self._stream_sid_ref[0]}")
                except Exception as ex:
                    logger.warning(f"[STTProcessor] Failed to send clear event: {ex}")

            await self.broadcast_interruption()

    async def run_stt(self, audio: bytes):
        """Called automatically by SegmentedSTTService with the complete 16kHz WAV bytes."""
        try:
            pcm = audio[44:] if audio.startswith(b"RIFF") and len(audio) > 44 else audio
            duration_sec = len(pcm) / (16000 * 2) if pcm else 0.0
            if duration_sec < 0.2:
                return

            wav_16k = audio if (audio.startswith(b"RIFF") and len(audio) > 44) else (
                b'RIFF' +
                struct.pack('<I', len(pcm) + 36) +
                b'WAVEfmt ' +
                struct.pack('<IHHIIHH', 16, 1, 1, 16000, 32000, 2, 16) +
                b'data' +
                struct.pack('<I', len(pcm)) +
                pcm
            )

            if getattr(self._config.infra, "save_recordings", False):
                try:
                    os.makedirs("recordings", exist_ok=True)
                    ts = int(datetime.utcnow().timestamp() * 1000)
                    with open(f"recordings/turn_{ts}.wav", "wb") as rf:
                        rf.write(wav_16k)
                except Exception:
                    pass

            _t_stt = datetime.utcnow()
            transcript = await self._stt.transcribe_bytes(wav_16k, format="wav")
            _stt_ms = int((datetime.utcnow() - _t_stt).total_seconds() * 1000)
            if self._call_metrics:
                self._call_metrics.record_stt(_stt_ms)
            if not transcript or not transcript.strip():
                return

            if _is_noise_hallucination(transcript, duration_sec):
                logger.info(f"[STTProcessor] Filtered hallucination: '{transcript}'")
                return

            logger.info(f"[Caller STT] (stt={_stt_ms}ms, dur={duration_sec:.1f}s): {transcript}")
            yield TranscriptionFrame(
                text=transcript,
                user_id="user",
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            logger.error(f"[STTProcessor] Error in run_stt: {e}", exc_info=True)


class AgentRouterProcessor(FrameProcessor):
    """
    FrameProcessor that receives TranscriptionFrame -> calls AgentRouter.route_and_respond()
    -> handles DB updates, logging, tool calls, human transfer -> emits TextFrame.
    """

    def __init__(
        self,
        router: AgentRouter,
        config: AppConfig,
        session_state_ref: Dict[str, Any],
        db_call_id_ref: List,
        call_sid_ref: List,
        caller_phone_ref: List,
        stream_sid_ref: List,
        chat_history: List[Dict[str, str]],
        current_node_id_ref: List,
        transcript_sequence_ref: List,
        telephony_service,
        metrics_collector: Optional[CallMetricsCollector] = None,
        workflow_id_ref: Optional[List] = None,
    ):
        super().__init__()
        self._workflow_id_ref = workflow_id_ref if workflow_id_ref is not None else [None]
        self._router = router
        self._config = config
        self._session_state = session_state_ref
        self._db_call_id_ref = db_call_id_ref
        self._call_sid_ref = call_sid_ref
        self._caller_phone_ref = caller_phone_ref
        self._stream_sid_ref = stream_sid_ref
        self._chat_history = chat_history
        self._current_node_id_ref = current_node_id_ref
        self._transcript_sequence_ref = transcript_sequence_ref
        self._telephony_service = telephony_service
        self._call_metrics = metrics_collector

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            self._session_state["interrupted"] = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            await self._route(frame.text)
            return

        await self.push_frame(frame, direction)

    async def _route(self, transcript: str):
        self._session_state["interrupted"] = False
        self._session_state["is_agent_speaking"] = True

        t0 = datetime.utcnow()
        seq = self._transcript_sequence_ref[0]
        self._transcript_sequence_ref[0] += 2

        db_id = self._db_call_id_ref[0]
        csid = self._call_sid_ref[0]
        cphone = self._caller_phone_ref[0]

        await event_logger.emit("call.transcript_turn", call_sid=csid,
            role="user", text=transcript, seq=seq)

        if db_id:
            async def _persist_user_turn():
                try:
                    async with async_session() as session:
                        session.add(TranscriptMessage(call_id=db_id, role="user",
                            message=transcript, sequence_number=seq))
                        await session.commit()
                    await dashboard_manager.broadcast({
                        "type": "transcript",
                        "call_id": db_id,
                        "role": "user",
                        "message": transcript,
                        "sequence_number": seq,
                        "timestamp": datetime.utcnow().isoformat(),
                    })
                except Exception as _ex:
                    logger.warning(f"[RouterProcessor] user-turn persist failed: {_ex}")
            asyncio.create_task(_persist_user_turn())

        provider = "twilio" if "twilio" in str(csid or "").lower() or str(csid or "").startswith("CA") else "tata"

        # Fill the dead air before a slow ticketing round-trip: creating a Symphony ticket
        # can take ~2s normally but has spiked to 20s, leaving the caller in silence. When
        # the caller explicitly asks to raise a ticket, speak a brief holding line first.
        _tl = (transcript or "").lower()
        _create_phrases = ("raise a ticket", "raise the ticket", "raise it", "create a ticket",
                           "make a ticket", "log a ticket", "open a ticket", "register a ticket",
                           "file a ticket", "generate a ticket", "raise a complaint", "log a complaint")
        if any(p in _tl for p in _create_phrases):
            try:
                await self.push_frame(TextFrame(
                    text="Okay, let me take care of that for you right away — one moment, please."))
            except Exception as _fe:
                logger.warning(f"[RouterProcessor] filler push failed: {_fe}")

        t_route = datetime.utcnow()

        try:
            response_data = await self._router.route_and_respond(
                session_id=str(db_id or csid),
                user_message=transcript,
                history=self._chat_history,
                current_node_id=self._current_node_id_ref[0],
                caller_phone=cphone,
                provider=provider,
                call_id=db_id,
                call_sid=csid,
                language_code=getattr(self._config.stt, "language_code", "en-IN"),
                workflow_id=self._workflow_id_ref[0],
            )
        except Exception as e:
            logger.error(f"[RouterProcessor] route_and_respond error: {e}", exc_info=True)
            self._session_state["is_agent_speaking"] = False
            await self.push_frame(TextFrame(text="I am sorry, I encountered an error. Please try again."))
            return

        route_ms = int((datetime.utcnow() - t_route).total_seconds() * 1000)
        if self._call_metrics:
            self._call_metrics.record_llm_route(route_ms)
            usage = response_data.get("usage") or {}
            if usage.get("prompt_tokens"):
                self._call_metrics.record_tokens(
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )
            guard = response_data.get("guard_result")
            if guard:
                self._call_metrics.record_security_event(
                    self._call_metrics.turn_count + 1, guard)
        answer = response_data.get("text", "I am looking into that for you.")
        if isinstance(answer, dict):
            answer = answer.get("result", str(answer))
        elif not isinstance(answer, str):
            answer = str(answer)

        answer = re.sub(r'Auth Status:.*', '', answer).strip()
        answer = re.sub(r'Dispatched via.*', '', answer).strip()
        answer = re.sub(r'Validated with Summit Token.*', '', answer).strip()

        agent_name = response_data.get("agent", "Agent")
        agent_voice = response_data.get("voice", "Aarohi")
        tool_called = response_data.get("tool_called")
        self._current_node_id_ref[0] = response_data.get("next_node_id")

        is_end_call = (
            tool_called == "end_call"
            or "END_CALL" in answer
            or "HANGUP_CALL" in answer
            or "END_CALL" in str(response_data)
        )
        answer = re.sub(r'\[?END_CALL\]?', '', answer).strip()
        answer = re.sub(r'\[?HANGUP_CALL\]?', '', answer).strip()

        logger.info(f"[{agent_name} ({agent_voice}) route={route_ms}ms]: {answer}")

        await event_logger.emit("call.agent_active", call_sid=csid,
            node_id=self._current_node_id_ref[0], agent_name=agent_name, voice=agent_voice)
        await event_logger.emit("call.transcript_turn", call_sid=csid,
            role="agent", text=answer, seq=seq + 1, voice=agent_voice)

        if (tool_called == "transfer_to_human"
                or "TRANSFER_HUMAN:" in answer
                or "TRANSFERHUMAN:" in answer):
            # Put the caller through to THIS company's handover number. A company with none is never sent to the
            # platform's own line: the agent keeps talking and takes a message instead.
            from .transfer import resolve_transfer_number
            call_tenant = None
            if db_id:
                async with async_session() as session:
                    result = await session.execute(select(Call).where(Call.id == db_id))
                    existing = result.scalars().first()
                    call_tenant = existing.tenant_id if existing else None
            target_number = await resolve_transfer_number(call_tenant)
            if target_number:
                if db_id:
                    async with async_session() as session:
                        result = await session.execute(select(Call).where(Call.id == db_id))
                        call = result.scalars().first()
                        if call:
                            call.transferred_to_human = True
                            call.transferred_to = target_number
                            await session.commit()
                await self._telephony_service.transfer_call(csid, target_number)
                self._session_state["is_agent_speaking"] = False
                await self.push_frame(EndFrame())
                return
            answer = re.sub(r"TRANSFER_?HUMAN:?\s*\+?[\d\s-]*", "", answer).strip()
            if not answer:
                answer = ("I am not able to transfer you from here, but I will pass your details to the team and someone will "
                          "call you back. May I have your name and the best number to reach you?")

        self._chat_history.append({"role": "user", "content": transcript})
        self._chat_history.append({"role": "assistant", "content": answer})

        # Push the answer to TTS IMMEDIATELY. DB writes, dashboard broadcasts and
        # telemetry are not prerequisites for speaking — keep them off the critical path.
        text_frame = TextFrame(text=answer)
        text_frame.metadata["agent_voice"] = agent_voice
        text_frame.metadata["is_end_call"] = is_end_call
        await self.push_frame(text_frame)

        total_router_ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
        if self._call_metrics:
            self._call_metrics.record_turn(total_router_ms)

        async def _persist_agent_turn():
            try:
                if db_id:
                    async with async_session() as session:
                        session.add(TranscriptMessage(call_id=db_id, role="agent",
                            message=answer, sequence_number=seq + 1))
                        result = await session.execute(select(Call).where(Call.id == db_id))
                        call = result.scalars().first()
                        if call:
                            call.current_agent = agent_name
                            await session.commit()
                            await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
                        await dashboard_manager.broadcast({
                            "type": "transcript",
                            "call_id": db_id,
                            "role": "agent",
                            "message": answer,
                            "sequence_number": seq + 1,
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                await event_logger.emit("call.streamed_turn_latency", call_sid=csid, seq=seq,
                    route_ms=route_ms,
                    total_router_ms=total_router_ms,
                    agent=agent_name)
            except Exception as _ex:
                logger.warning(f"[RouterProcessor] agent-turn persist failed: {_ex}")
        asyncio.create_task(_persist_agent_turn())


class SmartfloTTSProcessor(FrameProcessor):
    """
    FrameProcessor that receives TextFrame -> splits sentences -> synthesizes
    audio via MurfTTS/CartesiaTTS -> emits AudioRawFrame downstream.
    Stops immediately if interrupted.
    """

    def __init__(
        self,
        tts_service: BaseTTSService,
        config: AppConfig,
        session_state_ref: Dict[str, Any],
        call_sid_ref: List,
        telephony_service,
        metrics_collector: Optional[CallMetricsCollector] = None,
    ):
        super().__init__()
        self._tts = tts_service
        self._config = config
        self._session_state = session_state_ref
        self._call_sid_ref = call_sid_ref
        self._call_metrics = metrics_collector
        self._telephony_service = telephony_service

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            self._session_state["interrupted"] = True
            self._session_state["is_agent_speaking"] = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            await self._synthesize(frame)
            return

        await self.push_frame(frame, direction)

    async def _synthesize(self, frame: TextFrame):
        text = frame.text
        agent_voice = frame.metadata.get("agent_voice", "Aarohi")
        is_end_call = frame.metadata.get("is_end_call", False)
        csid = self._call_sid_ref[0]

        if not text or not text.strip():
            self._session_state["is_agent_speaking"] = False
            return

        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()] or [text]
        t_tts_start = datetime.utcnow()
        first_audio_sent = False

        async def _fetch_sentence(s: str) -> Optional[bytes]:
            if self._session_state.get("interrupted"):
                return None
            try:
                return await self._tts.synthesize_bytes(s, format="pcm", speaker=agent_voice)
            except Exception as ex:
                logger.error(f"[TTSProcessor] TTS error sentence '{s[:30]}': {ex}", exc_info=True)
                return None

        # Start synthesizing first sentence immediately
        pending_task = asyncio.create_task(_fetch_sentence(sentences[0])) if sentences else None

        for i, sentence in enumerate(sentences):
            if self._session_state.get("interrupted"):
                logger.info("[TTSProcessor] Synthesis aborted by interruption.")
                break

            # Concurrently launch synthesis of next sentence in background
            next_task = None
            if i + 1 < len(sentences) and not self._session_state.get("interrupted"):
                next_task = asyncio.create_task(_fetch_sentence(sentences[i + 1]))

            # Await current sentence
            tts_pcm = await pending_task if pending_task else None
            pending_task = next_task

            if self._session_state.get("interrupted"):
                logger.info("[TTSProcessor] Synthesis aborted after chunk wait by interruption.")
                break

            if not tts_pcm:
                continue

            if not first_audio_sent:
                ttfb_ms = int((datetime.utcnow() - t_tts_start).total_seconds() * 1000)
                logger.info(f"[TTSProcessor] TTFB sentence[0]: {ttfb_ms}ms")
                await event_logger.emit("call.tts_ttfb", call_sid=csid,
                    ttfb_ms=ttfb_ms, sentence_index=0, sentences_total=len(sentences))
                if self._call_metrics:
                    self._call_metrics.record_tts_ttfb(ttfb_ms)
                first_audio_sent = True

            tts_sr = (
                getattr(self._tts, "sample_rate", None)
                or getattr(getattr(self._tts, "settings", None), "sample_rate", None)
                or 8000
            )

            await self.push_frame(OutputAudioRawFrame(audio=tts_pcm, sample_rate=tts_sr, num_channels=1))

        self._session_state["is_agent_speaking"] = False

        if is_end_call and not self._session_state.get("interrupted"):
            logger.info("[TTSProcessor] END_CALL — sending EndFrame after 1.2s")
            await asyncio.sleep(1.2)
            try:
                await self._telephony_service.end_call(csid)
            except Exception as exc:
                logger.warning(f"[TTSProcessor] end_call failed: {exc}")
            await self.push_frame(EndFrame())
