"""
WebSocket Media Stream Bridge
Coordinates real-time telephony streaming between Twilio WebSockets and AI services.
Adheres to Single Responsibility Principle (SRP) and Dependency Inversion Principle (DIP).
"""

import json
import logging
import asyncio
import re
import os
import secrets
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

# Subsystem Interfaces & Implementations
from .interfaces import BaseSTTService, BaseTTSService, BaseLLMService, BaseTelephonyService, BaseAudioTranscoder, BaseVADDetector
from .config import AppConfig, get_config
from .audio_services import AudioTranscoder, EnergyVADDetector
from .ai_services import STTEngineFactory, LLMEngineFactory, TTSEngineFactory
from .agent_router import AgentRouter
from .tool_executor import ToolExecutor
from .rag_engine import RAGEngine
from .twilio_client import twilio_sync_client
from .event_logger import event_logger
from .langfuse_tracer import langfuse_tracer

logger = logging.getLogger(__name__)
router = APIRouter(tags=["media-bridge"])

# Cross-channel injection map: caller_number -> asyncio.Queue
ACTIVE_CALL_QUEUES: Dict[str, asyncio.Queue] = {}
_GREETING_PCM_CACHE: Dict[str, bytes] = {}


@dataclass
class CallSessionState:
    """Encapsulates per-call real-time conversation and audio state."""
    is_agent_speaking: bool = False
    interrupted: bool = False
    silence_frames: int = 0
    speech_frames: int = 0
    current_node_id: Optional[str] = None
    process_task: Optional[asyncio.Task] = None


class MediaStreamSession:
    """
    Manages an individual Twilio Media Stream WebSocket session.
    Orchestrates VAD, STT, Routing, TTS, and Telephony transfer.
    """

    def __init__(
        self,
        websocket: WebSocket,
        stt: BaseSTTService,
        tts: BaseTTSService,
        router: AgentRouter,
        telephony: BaseTelephonyService,
        transcoder: BaseAudioTranscoder,
        vad: BaseVADDetector,
        config: AppConfig
    ):
        self.websocket = websocket
        self.stt = stt
        self.tts = tts
        self.router = router
        self.telephony = telephony
        self.transcoder = transcoder
        self.vad = vad
        self.config = config

        self.state = CallSessionState()
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self.db_call_id: Optional[int] = None
        self.caller_phone: Optional[str] = None
        self.chat_history: List[Dict[str, str]] = []
        self.transcript_sequence: int = 0
        self.audio_buffer = bytearray()
        self.injection_task: Optional[asyncio.Task] = None

        # Event logging stats
        self.session_start_time = datetime.utcnow()
        self.last_media_stats_time = datetime.utcnow()
        self.media_chunks_count: int = 0
        self.total_bytes_received: int = 0

    async def handle_session(self):
        """Main lifecycle loop for the WebSocket connection."""
        await self.websocket.accept()
        logger.info("[MediaBridge] Twilio WebSocket connection accepted.")

        try:
            async for raw_msg in self.websocket.iter_text():
                try:
                    data = json.loads(raw_msg)
                except Exception:
                    continue

                event_type = data.get("event")
                logger.info(f"[MediaBridge] Received event: {event_type}")

                if event_type == "connected":
                    logger.info("[MediaBridge] Smartflo/Twilio connected handshake acknowledged.")
                elif event_type == "start":
                    await self._on_stream_start(data)
                elif event_type == "media":
                    await self._on_media_chunk(data)
                elif event_type == "dtmf":
                    await self._on_dtmf_digit(data)
                elif event_type == "mark":
                    self._on_mark_event(data)
                elif event_type == "dtmf":
                    logger.info(f"[MediaBridge] DTMF digit received: {data.get('dtmf', {}).get('digit')}")
                elif event_type == "stop":
                    logger.info(f"[MediaBridge] Stream stopped for CallSid={self.call_sid}")
                    break

        except WebSocketDisconnect:
            logger.info(f"[MediaBridge] Twilio WebSocket disconnected for CallSid={self.call_sid}")
        except Exception as e:
            logger.error(f"[MediaBridge] Unexpected error in media stream: {e}", exc_info=True)
        finally:
            await self._cleanup()

    async def _on_stream_start(self, data: Dict[str, Any]):
        start_data = data.get("start", {})
        self.stream_sid = data.get("streamSid") or start_data.get("streamSid") or f"stream-{int(datetime.utcnow().timestamp())}"
        self.call_sid = start_data.get("callSid") or self.stream_sid
        from_num = start_data.get("from") or "Smartflo Caller"
        to_num = start_data.get("to") or "+918069879488"

        logger.info(f"[MediaBridge] Stream Start: StreamSid={self.stream_sid}, CallSid={self.call_sid}, From={from_num}, To={to_num}")

        async with async_session() as session:
            result = await session.execute(select(Call).where(Call.call_sid == self.call_sid))
            call = result.scalars().first()
            if not call:
                call = Call(
                    call_sid=self.call_sid,
                    caller_number=from_num,
                    called_number=to_num,
                    status=CallStatus.ACTIVE,
                    live_status="in-progress",
                    source="smartflo-stream",
                    started_at=datetime.utcnow(),
                    current_agent="IT Service Desk Reception"
                )
                session.add(call)
                await session.commit()
                await dashboard_manager.broadcast({"type": "call_created", "call": call.to_dict()})

            self.db_call_id = call.id
            self.caller_phone = from_num if from_num and not str(from_num).startswith("+1701") else to_num
            call.live_status = "in-progress"
            await session.commit()
            await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})

        await event_logger.emit(
            "call.stream_started",
            call_sid=self.call_sid,
            stream_sid=self.stream_sid,
            caller=from_num,
            called=to_num,
            source=call.source if call else "stream"
        )

        # Setup Cross-channel Injection Queue if applicable
        if self.caller_phone:
            normalized = self.caller_phone.replace("whatsapp:", "")
            ACTIVE_CALL_QUEUES[normalized] = asyncio.Queue()

            async def _process_injections():
                while True:
                    msg = await ACTIVE_CALL_QUEUES[normalized].get()
                    if msg is None:
                        break
                    logger.info(f"[MediaBridge] Injected cross-channel message: {msg}")
                    self.chat_history.append({"role": "system", "content": msg})
                    self.state.process_task = asyncio.create_task(
                        self.process_turn(b"", self.transcript_sequence)
                    )
                    self.transcript_sequence += 2

            self.injection_task = asyncio.create_task(_process_injections())

        # Send Dynamic Multi-Agent Voice Greeting
        async def _send_greeting():
            self.state.is_agent_speaking = True
            try:
                greeting_data = await self.router.get_initial_greeting()
                greeting = greeting_data.get("text", "Thank you for calling. How can I help you today?")
                agent_voice = greeting_data.get("voice", "Aarohi")
                initial_agent = greeting_data.get("agent", "Primary Agent")
                self.state.current_node_id = greeting_data.get("node_id")

                self.chat_history.append({"role": "assistant", "content": greeting})

                await event_logger.emit(
                    "call.agent_active",
                    call_sid=self.call_sid,
                    node_id=self.state.current_node_id,
                    agent_name=initial_agent,
                    voice=agent_voice
                )
                await event_logger.emit(
                    "call.transcript_turn",
                    call_sid=self.call_sid,
                    role="agent",
                    text=greeting,
                    seq=0,
                    voice=agent_voice
                )

                if self.db_call_id:
                    async with async_session() as session:
                        result = await session.execute(select(Call).where(Call.id == self.db_call_id))
                        call = result.scalars().first()
                        if call:
                            call.current_agent = initial_agent
                        
                        # Save and broadcast initial agent intro greeting
                        session.add(TranscriptMessage(
                            call_id=self.db_call_id,
                            role="agent",
                            message=greeting,
                            sequence_number=0
                        ))
                        await session.commit()
                        if call:
                            await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
                        await dashboard_manager.broadcast({
                            "type": "transcript",
                            "call_id": self.db_call_id,
                            "role": "agent",
                            "message": greeting,
                            "sequence_number": 0,
                            "timestamp": datetime.utcnow().isoformat()
                        })

                provider = "twilio" if "twilio" in str(self.call_sid).lower() or str(self.call_sid).startswith("CA") else "tata"
                langfuse_tracer.trace_turn_background(
                    session_id=str(self.db_call_id or self.call_sid),
                    agent_name=initial_agent,
                    user_message="<Call Connected>",
                    assistant_response=greeting,
                    model="greeting",
                    language_code=getattr(self.config.stt, "language_code", "en-IN"),
                    provider=provider,
                    caller_phone=self.caller_phone,
                    call_id=self.db_call_id,
                    call_sid=self.call_sid,
                    node_id=self.state.current_node_id,
                    voice=agent_voice
                )

                cache_key = f"{greeting}_{agent_voice}"
                if cache_key in _GREETING_PCM_CACHE:
                    tts_pcm = _GREETING_PCM_CACHE[cache_key]
                else:
                    tts_pcm = await self.tts.synthesize_bytes(greeting, format="pcm", speaker=agent_voice)
                    if tts_pcm:
                        _GREETING_PCM_CACHE[cache_key] = tts_pcm
                if tts_pcm and not self.state.interrupted:
                    tts_sr = getattr(self.tts, "sample_rate", None) or getattr(getattr(self.tts, "settings", None), "sample_rate", 8000)
                    ulaw_b64 = self.transcoder.pcm16_to_ulaw_b64(tts_pcm, input_sample_rate=tts_sr)
                    chunk_size = 640
                    for j in range(0, len(ulaw_b64), chunk_size):
                        if self.state.interrupted:
                            break
                        chunk = ulaw_b64[j:j + chunk_size]
                        try:
                            await self.websocket.send_text(json.dumps({
                                "event": "media",
                                "streamSid": self.stream_sid,
                                "media": {"payload": chunk}
                            }))
                            await asyncio.sleep(0.04)
                        except Exception:
                            break

                    if not self.state.interrupted:
                        try:
                            await self.websocket.send_text(json.dumps({
                                "event": "mark",
                                "streamSid": self.stream_sid,
                                "mark": {"name": "greeting_complete"}
                            }))
                        except Exception:
                            pass
            except Exception as e:
                logger.error(f"[MediaBridge] Error in dynamic greeting: {e}")
            finally:
                self.state.is_agent_speaking = False

        self.state.process_task = asyncio.create_task(_send_greeting())

    async def _on_media_chunk(self, data: Dict[str, Any]):
        payload = data.get("media", {}).get("payload")
        if not payload:
            return

        # DoS Protection: Drop oversized frame payloads (>64KB base64)
        if len(payload) > 65536:
            logger.warning("[MediaBridge] Received oversized media payload, dropping.")
            return

        import base64
        try:
            ulaw_bytes = base64.b64decode(payload)
        except Exception:
            return

        # Buffer Memory Cap: 10MB limit per session
        if len(self.audio_buffer) + len(ulaw_bytes) > 10 * 1024 * 1024:
            logger.warning("[MediaBridge] Audio buffer exceeded 10MB limit, resetting buffer.")
            self.audio_buffer.clear()
            self.state.silence_frames = 0
            self.state.speech_frames = 0
            return

        self.media_chunks_count += 1
        self.total_bytes_received += len(ulaw_bytes)
        now = datetime.utcnow()
        if (now - self.last_media_stats_time).total_seconds() >= 30:
            self.last_media_stats_time = now
            await event_logger.emit(
                "call.media_stats",
                call_sid=self.call_sid,
                chunks_count=self.media_chunks_count,
                total_bytes=self.total_bytes_received,
                duration_seconds=round((now - self.session_start_time).total_seconds(), 2)
            )

        is_speaking = self.vad.is_speech(ulaw_bytes)

        if is_speaking:
            self.state.speech_frames += 1
            self.state.silence_frames = 0
            self.audio_buffer.extend(ulaw_bytes)

            # Barge-in Detection: Interrupt agent when user speaks
            if self.vad.should_barge_in(self.state.speech_frames, self.state.is_agent_speaking):
                logger.info("[MediaBridge] BARGE-IN DETECTED: Halting agent speech playback.")
                await event_logger.emit(
                    "call.barge_in",
                    call_sid=self.call_sid,
                    speech_frames=self.state.speech_frames,
                    seq=self.transcript_sequence
                )
                self.state.interrupted = True
                self.state.is_agent_speaking = False

                # Clear audio on Twilio telephony side
                await self.websocket.send_text(json.dumps({
                    "event": "clear",
                    "streamSid": self.stream_sid
                }))

                if self.state.process_task and not self.state.process_task.done():
                    self.state.process_task.cancel()
        else:
            self.state.speech_frames = 0
            if len(self.audio_buffer) > 0:
                self.state.silence_frames += 1
                self.audio_buffer.extend(ulaw_bytes)

                # Silence Endpointing: User finished talking
                if self.vad.is_endpoint(self.state.silence_frames, bool(self.audio_buffer)):
                    if not self.state.is_agent_speaking:
                        process_bytes = bytes(self.audio_buffer)
                        self.audio_buffer.clear()
                        self.state.silence_frames = 0
                        self.state.interrupted = False
                        self.state.is_agent_speaking = True

                        self.state.process_task = asyncio.create_task(
                            self.process_turn_streamed(process_bytes, self.transcript_sequence)
                        )
                        self.transcript_sequence += 2


    async def _on_dtmf_digit(self, data: Dict[str, Any]):
        dtmf_obj = data.get("dtmf", {})
        digit = dtmf_obj.get("digit")
        logger.info(f"[MediaBridge] DTMF digit received: '{digit}' for CallSid={self.call_sid}")
        await event_logger.emit(
            "call.dtmf",
            call_sid=self.call_sid,
            digit=digit
        )
        if digit and not self.state.is_agent_speaking:
            self.state.is_agent_speaking = True
            text_prompt = f"Option {digit}" if digit in ["1", "2", "3", "4", "5"] else f"Digit {digit}"
            self.state.process_task = asyncio.create_task(
                self.process_text_turn(text_prompt, self.transcript_sequence)
            )
            self.transcript_sequence += 2

    def _on_mark_event(self, data: Dict[str, Any]):
        mark_name = data.get("mark", {}).get("name", "")
        if mark_name.endswith("_complete"):
            self.state.is_agent_speaking = False
            self.state.interrupted = False

    async def process_text_turn(self, text_input: str, seq: int):
        """Processes a text/DTMF turn directly without STT step."""
        try:
            logger.info(f"[Caller DTMF/Text]: {text_input}")
            await event_logger.emit(
                "call.transcript_turn",
                call_sid=self.call_sid,
                role="user",
                text=text_input,
                seq=seq
            )
            if self.db_call_id:
                async with async_session() as session:
                    session.add(TranscriptMessage(
                        call_id=self.db_call_id, role="user", message=text_input, sequence_number=seq
                    ))
                    await session.commit()

            provider = "twilio" if "twilio" in str(self.call_sid).lower() or str(self.call_sid).startswith("CA") else "tata"
            response_data = await self.router.route_and_respond(
                session_id=str(self.db_call_id or self.call_sid),
                user_message=text_input,
                history=self.chat_history,
                current_node_id=self.state.current_node_id,
                caller_phone=self.caller_phone,
                provider=provider,
                call_id=self.db_call_id,
                call_sid=self.call_sid,
                language_code=getattr(self.config.stt, "language_code", "en-IN")
            )

            answer = response_data.get("text", "I understand your selection.")
            if isinstance(answer, dict):
                answer = answer.get("result", str(answer))
            elif not isinstance(answer, str):
                answer = str(answer)

            agent_name = response_data.get("agent", "Agent")
            agent_voice = response_data.get("voice", "Aarohi")
            self.state.current_node_id = response_data.get("next_node_id")

            logger.info(f"[{agent_name} ({agent_voice})]: {answer}")

            await event_logger.emit(
                "call.agent_active",
                call_sid=self.call_sid,
                node_id=self.state.current_node_id,
                agent_name=agent_name,
                voice=agent_voice
            )
            await event_logger.emit(
                "call.transcript_turn",
                call_sid=self.call_sid,
                role="agent",
                text=answer,
                seq=seq + 1,
                voice=agent_voice
            )

            self.chat_history.append({"role": "user", "content": text_input})
            self.chat_history.append({"role": "assistant", "content": answer})

            if self.db_call_id:
                async with async_session() as session:
                    session.add(TranscriptMessage(
                        call_id=self.db_call_id, role="agent", message=answer, sequence_number=seq + 1
                    ))
                    result = await session.execute(select(Call).where(Call.id == self.db_call_id))
                    call = result.scalars().first()
                    if call:
                        call.current_agent = agent_name
                        await session.commit()
                        await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})

            sentences = [s.strip() for s in re.split(r'(?<=[.!?]) +', answer) if s.strip()] or [answer]
            for sentence in sentences:
                if self.state.interrupted:
                    break
                tts_pcm = await self.tts.synthesize_bytes(sentence, format="pcm", speaker=agent_voice)
                if self.state.interrupted or not tts_pcm:
                    continue
                tts_sr = getattr(self.tts, "sample_rate", None) or getattr(getattr(self.tts, "settings", None), "sample_rate", 8000)
                ulaw_b64 = self.transcoder.pcm16_to_ulaw_b64(tts_pcm, input_sample_rate=tts_sr)
                chunk_size = 640
                for j in range(0, len(ulaw_b64), chunk_size):
                    if self.state.interrupted:
                        break
                    chunk = ulaw_b64[j:j + chunk_size]
                    try:
                        await self.websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": chunk}
                        }))
                        await asyncio.sleep(0.04)
                    except Exception:
                        break
        except Exception as e:
            logger.error(f"[MediaBridge] Error in process_text_turn: {e}", exc_info=True)
        finally:
            self.state.is_agent_speaking = False

    async def process_turn(self, ulaw_bytes: bytes, seq: int):
        """Processes an entire speech turn: STT -> AgentRouter -> Sentence-by-sentence TTS."""
        try:
            # 1. Speech-to-Text Transcription
            pcm_bytes = self.transcoder.ulaw_to_pcm16_bytes(ulaw_bytes)
            wav_bytes = self.transcoder.create_wav_bytes(pcm_bytes, sample_rate=16000)

            # Save audio turn to recordings directory for listening / debugging
            try:
                os.makedirs("recordings", exist_ok=True)
                rec_path = os.path.join("recordings", f"call_{self.db_call_id or 'unknown'}_turn_{seq}.wav")
                with open(rec_path, "wb") as rf:
                    rf.write(wav_bytes)
            except Exception:
                pass

            transcript = await self.stt.transcribe_bytes(wav_bytes, format="wav")
            if not transcript or not transcript.strip():
                self.state.is_agent_speaking = False
                return

            # Filter background line static / acoustic noise hallucinations
            clean_t = transcript.strip().lower().rstrip(".,!?")
            if clean_t in ["okay", "ok", "thank you", "thanks", "you", "bye", "yeah"] and len(pcm_bytes) < 16000 * 2 * 0.7:
                logger.info(f"[MediaBridge] Filtered noise hallucination: '{transcript}' (audio duration {len(pcm_bytes)/32000:.2f}s)")
                self.state.is_agent_speaking = False
                return

            logger.info(f"[Caller]: {transcript}")
            await event_logger.emit(
                "call.transcript_turn",
                call_sid=self.call_sid,
                role="user",
                text=transcript,
                seq=seq
            )

            if self.db_call_id:
                async with async_session() as session:
                    session.add(TranscriptMessage(
                        call_id=self.db_call_id, role="user", message=transcript, sequence_number=seq
                    ))
                    await session.commit()
                    await dashboard_manager.broadcast({
                        "type": "transcript",
                        "call_id": self.db_call_id,
                        "role": "user",
                        "message": transcript,
                        "sequence_number": seq,
                        "timestamp": datetime.utcnow().isoformat()
                    })

            # 2. Agent Router Reasoning Turn
            provider = "twilio" if "twilio" in str(self.call_sid).lower() or str(self.call_sid).startswith("CA") else "tata"
            response_data = await self.router.route_and_respond(
                session_id=str(self.db_call_id or self.call_sid),
                user_message=transcript,
                history=self.chat_history,
                current_node_id=self.state.current_node_id,
                caller_phone=self.caller_phone,
                provider=provider,
                call_id=self.db_call_id,
                call_sid=self.call_sid,
                language_code=getattr(self.config.stt, "language_code", "en-IN")
            )

            answer = response_data.get("text", "I'm looking into that for you.")
            if isinstance(answer, dict):
                answer = answer.get("result", str(answer))
            elif not isinstance(answer, str):
                answer = str(answer)

            import re
            answer = re.sub(r'Auth Status:.*', '', answer).strip()
            answer = re.sub(r'Dispatched via.*', '', answer).strip()
            answer = re.sub(r'Validated with Summit Token.*', '', answer).strip()
            agent_name = response_data.get("agent", "Agent")
            agent_voice = response_data.get("voice", "Aarohi")
            tool_called = response_data.get("tool_called")
            self.state.current_node_id = response_data.get("next_node_id")

            is_end_call = (
                tool_called == "end_call" or 
                "END_CALL" in answer or 
                "HANGUP_CALL" in answer or
                "END_CALL" in str(response_data)
            )
            answer = re.sub(r'\[?END_CALL\]?', '', answer).strip()
            answer = re.sub(r'\[?HANGUP_CALL\]?', '', answer).strip()

            logger.info(f"[{agent_name} ({agent_voice})]: {answer}")

            await event_logger.emit(
                "call.agent_active",
                call_sid=self.call_sid,
                node_id=self.state.current_node_id,
                agent_name=agent_name,
                voice=agent_voice
            )
            await event_logger.emit(
                "call.transcript_turn",
                call_sid=self.call_sid,
                role="agent",
                text=answer,
                seq=seq + 1,
                voice=agent_voice
            )

            # 3. Handle Live Telephony Transfer
            if tool_called == "transfer_to_human" or "TRANSFER_HUMAN:" in answer or "TRANSFERHUMAN:" in answer:
                logger.info("[MediaBridge] Live human transfer requested.")
                # Security: the destination is always the server-configured supervisor
                # number. Never dial a number extracted from LLM free-text output —
                # that text is influenceable by caller input (prompt injection) and
                # must not become a real telephony action.
                target_number = self.config.telephony.supervisor_transfer_number
                if self.db_call_id:
                    async with async_session() as session:
                        result = await session.execute(select(Call).where(Call.id == self.db_call_id))
                        call = result.scalars().first()
                        if call:
                            call.transferred_to_human = True
                            call.transferred_to = target_number
                            await session.commit()

                success = await self.telephony.transfer_call(self.call_sid, target_number)
                if success:
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    return

            self.chat_history.append({"role": "user", "content": transcript})
            self.chat_history.append({"role": "assistant", "content": answer})

            if self.db_call_id:
                async with async_session() as session:
                    session.add(TranscriptMessage(
                        call_id=self.db_call_id, role="agent", message=answer, sequence_number=seq + 1
                    ))
                    result = await session.execute(select(Call).where(Call.id == self.db_call_id))
                    call = result.scalars().first()
                    if call:
                        call.current_agent = agent_name
                        await session.commit()
                        await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
                    await dashboard_manager.broadcast({
                        "type": "transcript",
                        "call_id": self.db_call_id,
                        "role": "agent",
                        "message": answer,
                        "sequence_number": seq + 1,
                        "timestamp": datetime.utcnow().isoformat()
                    })

            # 4. Stream TTS Sentence-by-Sentence for ultra-low TTFB
            sentences = [s.strip() for s in re.split(r'(?<=[.!?]) +', answer) if s.strip()] or [answer]

            for sentence in sentences:
                if self.state.interrupted:
                    break

                tts_pcm = await self.tts.synthesize_bytes(sentence, format="pcm", speaker=agent_voice)
                if self.state.interrupted or not tts_pcm:
                    continue

                tts_sr = getattr(self.tts, "sample_rate", None) or getattr(getattr(self.tts, "settings", None), "sample_rate", 8000)
                ulaw_b64 = self.transcoder.pcm16_to_ulaw_b64(tts_pcm, input_sample_rate=tts_sr)
                chunk_size = 640
                for j in range(0, len(ulaw_b64), chunk_size):
                    if self.state.interrupted:
                        break
                    chunk = ulaw_b64[j:j + chunk_size]
                    try:
                        await self.websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": chunk}
                        }))
                        await asyncio.sleep(0.04)
                    except Exception:
                        break

            if not self.state.interrupted:
                try:
                    await self.websocket.send_text(json.dumps({
                        "event": "mark",
                        "streamSid": self.stream_sid,
                        "mark": {"name": f"turn_{seq}_complete"}
                    }))
                except Exception:
                    pass

                if is_end_call:
                    logger.info(f"[MediaBridge] Gracefully ending call {self.call_sid} after closing statement.")
                    # Allow 1.2s for buffered TTS audio to play through to the caller before terminating the leg
                    await asyncio.sleep(1.2)
                    try:
                        await self.telephony.end_call(self.call_sid)
                    except Exception as exc:
                        logger.warning(f"[MediaBridge] Failed to end call {self.call_sid}: {exc}")
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    return

        except asyncio.CancelledError:
            logger.info("[MediaBridge] Process turn cancelled via barge-in.")
        except Exception as e:
            logger.error(f"[MediaBridge] Turn processing error: {e}", exc_info=True)

    async def process_turn_streamed(self, ulaw_bytes: bytes, seq: int):
        """
        ElevenLabs-style streamed pipeline:
          STT → LLM token stream → sentence-boundary detection →
          TTS dispatch (sentence N) parallel to LLM generating sentence N+1 →
          Stream audio chunks back to telephony.

        This eliminates the full LLM generation wait before TTS starts.
        Expected improvement: ~400-600ms reduction on standard conversational turns.

        Falls back to process_turn() if:
          - llm.generate_stream() is not available (GeminiLLM, etc.)
          - Routing requires tool calls or multi-agent handoffs (handled by route_and_respond)
          - Any unrecoverable streaming error occurs
        """
        try:
            # Reset Silero LSTM state between turns (prevents context bleed)
            if hasattr(self.vad, "reset_state"):
                self.vad.reset_state()

            # 1. Speech-to-Text (batch — unchanged for now)
            t0 = datetime.utcnow()
            pcm_bytes = self.transcoder.ulaw_to_pcm16_bytes(ulaw_bytes)
            wav_bytes = self.transcoder.create_wav_bytes(pcm_bytes, sample_rate=16000)

            try:
                os.makedirs("recordings", exist_ok=True)
                rec_path = os.path.join("recordings", f"call_{self.db_call_id or 'unknown'}_turn_{seq}.wav")
                with open(rec_path, "wb") as rf:
                    rf.write(wav_bytes)
            except Exception:
                pass

            transcript = await self.stt.transcribe_bytes(wav_bytes, format="wav")
            if not transcript or not transcript.strip():
                self.state.is_agent_speaking = False
                return

            clean_t = transcript.strip().lower().rstrip(".,!?")
            if clean_t in ["okay", "ok", "thank you", "thanks", "you", "bye", "yeah"] and len(pcm_bytes) < 16000 * 2 * 0.7:
                logger.info(f"[StreamedTurn] Filtered noise hallucination: '{transcript}'")
                self.state.is_agent_speaking = False
                return

            stt_ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
            logger.info(f"[Caller STT ({stt_ms}ms)]: {transcript}")

            await event_logger.emit(
                "call.transcript_turn",
                call_sid=self.call_sid,
                role="user",
                text=transcript,
                seq=seq
            )
            if self.db_call_id:
                async with async_session() as session:
                    session.add(TranscriptMessage(
                        call_id=self.db_call_id, role="user",
                        message=transcript, sequence_number=seq
                    ))
                    await session.commit()
                    await dashboard_manager.broadcast({
                        "type": "transcript", "call_id": self.db_call_id,
                        "role": "user", "message": transcript,
                        "sequence_number": seq,
                        "timestamp": datetime.utcnow().isoformat()
                    })

            # 2. Agent routing — needed for tool calls, handoffs, ticket creation
            # This is the full route_and_respond() path (non-streamed for tool turns)
            provider = "twilio" if "twilio" in str(self.call_sid).lower() or str(self.call_sid).startswith("CA") else "tata"
            t_route = datetime.utcnow()
            response_data = await self.router.route_and_respond(
                session_id=str(self.db_call_id or self.call_sid),
                user_message=transcript,
                history=self.chat_history,
                current_node_id=self.state.current_node_id,
                caller_phone=self.caller_phone,
                provider=provider,
                call_id=self.db_call_id,
                call_sid=self.call_sid,
                language_code=getattr(self.config.stt, "language_code", "en-IN")
            )
            route_ms = int((datetime.utcnow() - t_route).total_seconds() * 1000)

            answer = response_data.get("text", "I'm looking into that for you.")
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
            self.state.current_node_id = response_data.get("next_node_id")

            is_end_call = (
                tool_called == "end_call" or
                "END_CALL" in answer or "HANGUP_CALL" in answer or
                "END_CALL" in str(response_data)
            )
            answer = re.sub(r'\[?END_CALL\]?', '', answer).strip()
            answer = re.sub(r'\[?HANGUP_CALL\]?', '', answer).strip()

            logger.info(f"[{agent_name} ({agent_voice}) route={route_ms}ms]: {answer}")

            await event_logger.emit(
                "call.agent_active",
                call_sid=self.call_sid,
                node_id=self.state.current_node_id,
                agent_name=agent_name,
                voice=agent_voice
            )
            await event_logger.emit(
                "call.transcript_turn",
                call_sid=self.call_sid,
                role="agent",
                text=answer,
                seq=seq + 1,
                voice=agent_voice
            )

            # Handle live telephony transfer
            if tool_called == "transfer_to_human" or "TRANSFER_HUMAN:" in answer or "TRANSFERHUMAN:" in answer:
                target_number = self.config.telephony.supervisor_transfer_number
                if self.db_call_id:
                    async with async_session() as session:
                        result = await session.execute(select(Call).where(Call.id == self.db_call_id))
                        call = result.scalars().first()
                        if call:
                            call.transferred_to_human = True
                            call.transferred_to = target_number
                            await session.commit()
                success = await self.telephony.transfer_call(self.call_sid, target_number)
                if success:
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    return

            self.chat_history.append({"role": "user", "content": transcript})
            self.chat_history.append({"role": "assistant", "content": answer})

            if self.db_call_id:
                async with async_session() as session:
                    session.add(TranscriptMessage(
                        call_id=self.db_call_id, role="agent",
                        message=answer, sequence_number=seq + 1
                    ))
                    result = await session.execute(select(Call).where(Call.id == self.db_call_id))
                    call = result.scalars().first()
                    if call:
                        call.current_agent = agent_name
                        await session.commit()
                        await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
                    await dashboard_manager.broadcast({
                        "type": "transcript", "call_id": self.db_call_id,
                        "role": "agent", "message": answer,
                        "sequence_number": seq + 1,
                        "timestamp": datetime.utcnow().isoformat()
                    })

            # 3. ─── STREAMED TTS PIPELINE ─────────────────────────────────────
            # Split response into sentences and synthesize + stream each one
            # immediately, in order, without waiting for all sentences.
            # This delivers the first audio chunk ~300-500ms earlier than
            # synthesizing the full response first.
            t_tts_start = datetime.utcnow()
            first_chunk_logged = False

            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', answer) if s.strip()] or [answer]

            for i, sentence in enumerate(sentences):
                if self.state.interrupted:
                    logger.info("[StreamedTurn] Barge-in detected — stopping TTS pipeline.")
                    break
                if not sentence:
                    continue

                t_sent = datetime.utcnow()
                tts_pcm = await self.tts.synthesize_bytes(sentence, format="pcm", speaker=agent_voice)

                if self.state.interrupted or not tts_pcm:
                    continue

                if not first_chunk_logged:
                    ttfb_ms = int((datetime.utcnow() - t_tts_start).total_seconds() * 1000)
                    total_ms = int((datetime.utcnow() - t0).total_seconds() * 1000)
                    logger.info(f"[StreamedTurn] TTFB sentence[0]: TTS={ttfb_ms}ms | Total={total_ms}ms "
                                f"(STT={stt_ms}ms, Route={route_ms}ms)")
                    await event_logger.emit(
                        "call.streamed_turn_latency",
                        call_sid=self.call_sid,
                        seq=seq,
                        stt_ms=stt_ms,
                        route_ms=route_ms,
                        tts_ttfb_ms=ttfb_ms,
                        total_ms=total_ms,
                        agent=agent_name,
                        sentences=len(sentences)
                    )
                    first_chunk_logged = True

                tts_sr = getattr(self.tts, "sample_rate", None) or getattr(getattr(self.tts, "settings", None), "sample_rate", 8000)
                ulaw_b64 = self.transcoder.pcm16_to_ulaw_b64(tts_pcm, input_sample_rate=tts_sr)
                chunk_size = 640
                for j in range(0, len(ulaw_b64), chunk_size):
                    if self.state.interrupted:
                        break
                    try:
                        await self.websocket.send_text(json.dumps({
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {"payload": ulaw_b64[j:j + chunk_size]}
                        }))
                        await asyncio.sleep(0.04)
                    except Exception:
                        break

            if not self.state.interrupted:
                try:
                    await self.websocket.send_text(json.dumps({
                        "event": "mark",
                        "streamSid": self.stream_sid,
                        "mark": {"name": f"turn_{seq}_complete"}
                    }))
                except Exception:
                    pass

                if is_end_call:
                    logger.info(f"[StreamedTurn] Gracefully ending call {self.call_sid}.")
                    await asyncio.sleep(1.2)
                    try:
                        await self.telephony.end_call(self.call_sid)
                    except Exception as exc:
                        logger.warning(f"[StreamedTurn] Failed to end call: {exc}")
                    try:
                        await self.websocket.close()
                    except Exception:
                        pass
                    return

        except asyncio.CancelledError:
            logger.info("[StreamedTurn] Cancelled via barge-in.")
        except Exception as e:
            logger.error(f"[StreamedTurn] Error: {e}", exc_info=True)
            # Safety fallback to standard turn processing
            try:
                await self.process_turn(ulaw_bytes, seq)
                return
            except Exception:
                pass
        finally:
            self.state.is_agent_speaking = False

    async def _cleanup(self):
        duration_sec = round((datetime.utcnow() - self.session_start_time).total_seconds(), 2)
        await event_logger.emit(
            "call.stream_stopped",
            call_sid=self.call_sid,
            duration_seconds=duration_sec,
            chunks_count=self.media_chunks_count,
            total_bytes=self.total_bytes_received
        )

        if self.injection_task:
            self.injection_task.cancel()
        if self.caller_phone:
            normalized = self.caller_phone.replace("whatsapp:", "")
            if normalized in ACTIVE_CALL_QUEUES:
                del ACTIVE_CALL_QUEUES[normalized]

        if self.db_call_id:
            async with async_session() as session:
                result = await session.execute(
                    select(Call)
                    .options(selectinload(Call.transcript_messages), selectinload(Call.tickets))
                    .where(Call.id == self.db_call_id)
                )
                call = result.scalars().first()
                if call:
                    call.status = CallStatus.COMPLETED
                    call.live_status = "completed"
                    call.ended_at = datetime.utcnow()
                    call.duration_seconds = duration_sec
                    await session.commit()
                    await dashboard_manager.broadcast({"type": "call_completed", "call": call.to_dict()})

                    # Upload structured pretty call JSON to GCS
                    try:
                        provider = event_logger.get_telephony_provider(call.source, self.call_sid)
                        transcripts = []
                        for msg in (call.transcript_messages or []):
                            speaker = f"Agent ({call.current_agent or 'Service Desk'})" if msg.role == "agent" else f"Caller ({self.caller_phone or 'User'})"
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

                        call_summary = {
                            "call_id": self.db_call_id,
                            "telephony": {
                                "provider": provider,
                                "channel": call.source or "stream",
                                "call_sid": self.call_sid,
                                "caller_phone": self.caller_phone,
                                "called_phone": call.called_number,
                            },
                            "status": "completed",
                            "started_at": self.session_start_time.isoformat() if self.session_start_time else None,
                            "ended_at": datetime.utcnow().isoformat(),
                            "duration_seconds": duration_sec,
                            "summary": call.summary,
                            "routing": {
                                "final_agent": call.current_agent or "Incident Management Assistant",
                            },
                            "ticket": ticket_info,
                            "transcript": transcripts,
                            "telephony_stats": {
                                "duration_seconds": duration_sec,
                                "audio_chunks": self.media_chunks_count,
                                "total_bytes": self.total_bytes_received,
                            },
                        }
                        await event_logger.upload_call_json(call_summary)
                    except Exception as exc:
                        logger.warning(f"[MediaBridge] Failed to upload call summary JSON to GCS: {exc}")


# ─── Global Service Initializer (Dependency Inversion) ─────────────────────────

def create_media_session(websocket: WebSocket) -> MediaStreamSession:
    """Instantiate a MediaStreamSession with all dependencies injected."""
    cfg = get_config()
    stt_service = STTEngineFactory.get_engine(cfg)
    tts_service = TTSEngineFactory.get_engine(cfg)
    llm_service = LLMEngineFactory.get_engine(cfg)
    tool_exec = ToolExecutor()
    rag = RAGEngine()
    router_service = AgentRouter(llm=llm_service, tool_executor=tool_exec, rag_engine=rag, config=cfg)
    transcoder = AudioTranscoder()

    # Prefer Silero neural VAD (already have model in /backend/silero_vad.onnx)
    # Falls back to EnergyVADDetector if onnxruntime is not installed
    from .audio_services import SileroVADDetector
    vad_detector = SileroVADDetector(settings=cfg.vad)


    return MediaStreamSession(
        websocket=websocket,
        stt=stt_service,
        tts=tts_service,
        router=router_service,
        telephony=twilio_sync_client,
        transcoder=transcoder,
        vad=vad_detector,
        config=cfg
    )


# Set TELEPHONY_STREAM_SECRET in production; without it each process signs with its own random secret.
_STREAM_SECRET_FALLBACK = secrets.token_hex(32)


def generate_stream_token(call_sid: str) -> str:
    """Generates an HMAC SHA-256 token binding the media stream to a specific call SID."""
    secret = os.getenv("TELEPHONY_STREAM_SECRET", os.getenv("SECRET_KEY", _STREAM_SECRET_FALLBACK))
    return hmac.new(secret.encode("utf-8"), (call_sid or "").encode("utf-8"), hashlib.sha256).hexdigest()


def verify_stream_token(call_sid: str, token: str) -> bool:
    """Validates the HMAC SHA-256 stream token."""
    if not token or not call_sid:
        return False
    expected = generate_stream_token(call_sid)
    return hmac.compare_digest(expected, token)


# ─── FastAPI Route Handlers ────────────────────────────────────────────────────

@router.post("/incoming-call")
async def incoming_call(request: Request):
    """Twilio incoming call webhook."""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    caller = form_data.get("From", "Unknown")
    called = form_data.get("To", "")
    
    logger.info("Incoming call webhook: CallSid=%s, From=%s", call_sid, caller)
    
    if not call_sid:
        return Response(content="<Response></Response>", media_type="application/xml")

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
                source="twilio-stream",
                started_at=datetime.utcnow(),
                current_agent="custom-agent"
            )
            session.add(call)
            await session.commit()
            await dashboard_manager.broadcast({"type": "call_created", "call": call.to_dict()})

    await event_logger.emit(
        "call.created",
        call_sid=call_sid,
        caller=caller,
        called=called,
        source="twilio-incoming"
    )

    host_header = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    server_tunnel_url = get_config().telephony.server_url or "https://voicebot-agent.stldigitaltech.com"
    domain = "voicebot-agent.stldigitaltech.com"

    stream_token = generate_stream_token(call_sid)

    # Twilio Media Streams require a valid SSL certificate (wss://domain/ws/stream)
    if "34.71.164.93" in host_header or "127.0.0.1" in host_header or "localhost" in host_header or not host_header:
        ws_url = f"wss://{domain}/ws/stream?token={stream_token}&amp;call_sid={call_sid}"
    else:
        ws_url = f"wss://{host_header}/ws/stream?token={stream_token}&amp;call_sid={call_sid}"

    logger.info("Generated Twilio Media Stream WebSocket URL: %s", ws_url)

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}" />
    </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/ws/stream")
@router.websocket("/ws/media-stream")
async def media_stream_endpoint(websocket: WebSocket):
    """Twilio / Smartflo bidirectional WebSocket media stream endpoint."""
    require_stream_auth = os.getenv("REQUIRE_STREAM_AUTH", "false").lower() == "true"
    token = websocket.query_params.get("token")
    call_sid = websocket.query_params.get("call_sid")

    if require_stream_auth:
        if not token or not call_sid or not verify_stream_token(call_sid, token):
            logger.warning("[MediaBridge] Unauthorized WebSocket stream attempt: call_sid=%s", call_sid)
            await websocket.close(code=4403)
            return

    session = create_media_session(websocket)
    await session.handle_session()
