"""
Gemini Live (BidiGenerateContent) client — real-time, bidirectional speech-to-speech.

One WebSocket replaces the batch STT -> LLM -> TTS chain: we stream the caller's audio in and the
model streams audio (and/or text) back, with native barge-in and function calling. Endpoint verified
reachable from the VM; the only gate is the Gemini billing cap.

Audio formats (important for telephony):
    INPUT  to Gemini : 16 kHz, 16-bit signed PCM, mono   (mime "audio/pcm;rate=16000")
    OUTPUT from Gemini: 24 kHz, 16-bit signed PCM, mono
Tata Smartflo media is 8 kHz µ-law, so the pipeline must resample 8k µ-law <-> 16k/24k PCM. This
module speaks raw PCM; resampling lives at the media bridge (see `pcm` helpers below for a start).

Config (env):
    GEMINI_LIVE_API_KEY   preferred; falls back to GEMINI_API_KEY
    GEMINI_LIVE_MODEL     default "models/gemini-3.8-live"
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import websockets

log = logging.getLogger("gemini.live")

WS_URL = ("wss://generativelanguage.googleapis.com/ws/"
          "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent")

DEFAULT_MODEL = os.getenv("GEMINI_LIVE_MODEL", "models/gemini-3.8-live")
INPUT_RATE = 16000
OUTPUT_RATE = 24000


def api_key() -> str:
    return (os.getenv("GEMINI_LIVE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()


@dataclass
class LiveEvent:
    """One thing the model sent us."""
    kind: str                       # "text" | "audio" | "tool_call" | "interrupted" | "turn_complete" | "setup" | "error" | "other"
    text: Optional[str] = None
    audio: Optional[bytes] = None   # 24 kHz PCM16
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None


# What an agent's Gemini Live settings (set in the builder) mean for turn-taking. Anything not set keeps the server default.
TURN_END = {                                   # how long a pause the model waits before it decides the caller has finished
    "quick": {"end_sensitivity": "END_SENSITIVITY_HIGH"},
    "balanced": {},
    "patient": {"end_sensitivity": "END_SENSITIVITY_LOW", "silence_ms": 1000},        # for dates, numbers and people who think aloud
}
TURN_START = {                                 # how readily it notices that the caller has started speaking
    "sensitive": {"start_sensitivity": "START_SENSITIVITY_HIGH", "prefix_ms": 500},   # keeps a quiet first word from being clipped (500 ms kept a whole sentence that 200 ms clipped)
    "balanced": {},
    "steady": {"start_sensitivity": "START_SENSITIVITY_LOW"},                         # ignores background noise
}
THINKING_LEVELS = ("LOW", "MEDIUM", "HIGH")      # what the extended-thinking model accepts (it refuses to start with none)
EXTENDED_MODEL = "models/gemini-3.8-live-extended-thinking"

# The voices an agent can speak with, and how Google describes each. The service accepts any name without complaint and quietly
# uses a default for one it does not know, so only names measured to sound different (pitch, 2026-10-29) are offered.
VOICES = (
    ("Aoede", "Breezy"), ("Kore", "Firm"), ("Puck", "Upbeat"), ("Charon", "Informative"), ("Fenrir", "Excitable"),
    ("Leda", "Youthful"), ("Orus", "Firm"), ("Zephyr", "Bright"),
    ("Sulafat", "Warm"), ("Achernar", "Soft"), ("Vindemiatrix", "Gentle"), ("Achird", "Friendly"), ("Gacrux", "Mature"),
)
CARING_VOICES = ("Sulafat", "Achernar", "Vindemiatrix", "Achird")        # for an agent that speaks with people about their health


def clean_languages(value: Any) -> List[str]:
    """Language codes for the caller's transcript: a short list of codes like en-US or ar-XA; anything else is dropped."""
    import re
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v) for v in value if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,4})?", str(v))][:4]


def live_settings(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """An agent's Gemini Live choices -> {model, vad, thinking_level} for GeminiLiveSession. Unknown values are ignored."""
    cfg = cfg if isinstance(cfg, dict) else {}
    vad: Dict[str, Any] = {}
    vad.update(TURN_END.get(str(cfg.get("turn_end") or ""), {}))
    vad.update(TURN_START.get(str(cfg.get("turn_start") or ""), {}))
    langs = clean_languages(cfg.get("transcript_languages"))
    model = cfg.get("model") if isinstance(cfg.get("model"), str) and cfg.get("model", "").startswith("models/gemini-") else None
    level = cfg.get("thinking_level") if cfg.get("thinking_level") in THINKING_LEVELS else None
    if model and "extended" in model and not level:
        level = "LOW"                              # the lowest level it accepts: a phone call cannot wait for long thinking
    return {"model": model, "vad": vad, "thinking_level": level if model and "extended" in model else None, "transcript_languages": langs}


class GeminiLiveSession:
    """A single Live conversation over one WebSocket.

    Usage:
        sess = GeminiLiveSession(system_instruction="You are a clinic receptionist...",
                                 modalities=["AUDIO"], tools=tool_schemas)
        await sess.connect()
        await sess.send_text("Hello")           # or sess.send_audio(pcm16_16k)
        async for ev in sess.events():
            if ev.kind == "audio": play(ev.audio)
            if ev.kind == "text":  print(ev.text)
            if ev.kind == "turn_complete": break
    """

    def __init__(self, *, model: str = DEFAULT_MODEL, system_instruction: str = "",
                 modalities: Optional[List[str]] = None, tools: Optional[List[Dict[str, Any]]] = None,
                 voice: Optional[str] = None, key: Optional[str] = None,
                 vad: Optional[Dict[str, Any]] = None, thinking_level: Optional[str] = None,
                 transcript_languages: Optional[List[str]] = None):
        self.model = model if model.startswith("models/") else f"models/{model}"
        self.vad = vad or {}                    # per-agent turn-taking: start_sensitivity, end_sensitivity, silence_ms, prefix_ms
        self.thinking_level = thinking_level    # the extended-thinking model refuses to start without one
        self.transcript_languages = transcript_languages or []     # hint for the written transcript of the caller, e.g. ["en-US", "ar-XA"]
        self.system_instruction = system_instruction
        self.modalities = modalities or ["AUDIO"]        # ["AUDIO"] or ["TEXT"]
        self.tools = tools or []
        self.voice = voice
        self.key = key or api_key()
        self._ws: Optional[Any] = None

    def setup_message(self) -> Dict[str, Any]:
        """The first message of the session: model, voice, tools, turn-taking and transcription."""
        gen_cfg: Dict[str, Any] = {"responseModalities": self.modalities}
        if self.voice and "AUDIO" in self.modalities:
            gen_cfg["speechConfig"] = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}}
        if self.thinking_level:
            gen_cfg["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        setup: Dict[str, Any] = {"model": self.model, "generationConfig": gen_cfg}
        if self.system_instruction:
            setup["systemInstruction"] = {"parts": [{"text": self.system_instruction}]}
        if self.tools:
            setup["tools"] = [{"functionDeclarations": self.tools}]
        # India-tuned turn detection: low start-sensitivity (ignore traffic/noise), high end-sensitivity
        # (fast turn-taking). Without this, auto-VAD often never fires on telephony audio.
        detection: Dict[str, Any] = {
            "startOfSpeechSensitivity": self.vad.get("start_sensitivity") or os.getenv("GEMINI_LIVE_START_SENS", "START_SENSITIVITY_LOW"),
            "endOfSpeechSensitivity": self.vad.get("end_sensitivity") or os.getenv("GEMINI_LIVE_END_SENS", "END_SENSITIVITY_HIGH")}
        if self.vad.get("silence_ms"):               # how long a pause counts as "finished speaking"
            detection["silenceDurationMs"] = int(self.vad["silence_ms"])
        if self.vad.get("prefix_ms") is not None:    # audio kept from just before speech is detected, so a first word is not clipped
            detection["prefixPaddingMs"] = int(self.vad["prefix_ms"])
        setup["realtimeInputConfig"] = {"automaticActivityDetection": detection}
        # transcripts of both sides — powers the dashboard transcript and the jailbreak guard
        # Without a hint the written transcript sometimes drifts into another language (German, Spanish) on a short or noisy phrase.
        setup["inputAudioTranscription"] = {"languageCodes": list(self.transcript_languages)} if self.transcript_languages else {}
        setup["outputAudioTranscription"] = {}
        return setup

    async def connect(self, open_timeout: float = 15.0) -> None:
        if not self.key:
            raise RuntimeError("No Gemini key: set GEMINI_LIVE_API_KEY (or GEMINI_API_KEY).")
        self._ws = await websockets.connect(f"{WS_URL}?key={self.key}", open_timeout=open_timeout,
                                            max_size=None, ping_interval=20)
        setup = self.setup_message()
        await self._ws.send(json.dumps({"setup": setup}))
        # first server message should be setupComplete
        first = await asyncio.wait_for(self._ws.recv(), timeout=open_timeout)
        msg = _loads(first)
        if "setupComplete" not in msg:
            raise RuntimeError(f"Gemini Live setup failed: {str(msg)[:300]}")
        log.info("Gemini Live connected: model=%s modalities=%s", self.model, self.modalities)

    async def send_text(self, text: str, end_turn: bool = True) -> None:
        await self._ws.send(json.dumps({"clientContent": {
            "turns": [{"role": "user", "parts": [{"text": text}]}], "turnComplete": end_turn}}))

    async def send_audio(self, pcm16_16k: bytes) -> None:
        """Stream a chunk of the caller's audio (16 kHz PCM16). Call repeatedly as audio arrives.
        Uses realtimeInput.audio (the current field); mediaChunks is deprecated and silently ignored."""
        await self._ws.send(json.dumps({"realtimeInput": {"audio": {
            "mimeType": f"audio/pcm;rate={INPUT_RATE}", "data": base64.b64encode(pcm16_16k).decode()}}}))

    async def send_tool_response(self, responses: List[Dict[str, Any]]) -> None:
        await self._ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))

    async def events(self) -> AsyncIterator[LiveEvent]:
        """Yield everything the model sends until the socket closes."""
        try:
            async for raw in self._ws:
                msg = _loads(raw)
                if "setupComplete" in msg:
                    yield LiveEvent(kind="setup", raw=msg); continue
                sent = False                    # did this message produce an event? if not, say so rather than drop it
                sc = msg.get("serverContent") or {}
                if sc.get("interrupted"):
                    sent = True
                    yield LiveEvent(kind="interrupted", raw=msg)
                it = sc.get("inputTranscription") or {}
                if it.get("text"):
                    sent = True
                    yield LiveEvent(kind="input_transcript", text=it["text"], raw=msg)
                ot = sc.get("outputTranscription") or {}
                if ot.get("text"):
                    sent = True
                    yield LiveEvent(kind="output_transcript", text=ot["text"], raw=msg)
                mt = sc.get("modelTurn") or {}
                for part in mt.get("parts", []) or []:
                    if "text" in part:
                        sent = True
                        yield LiveEvent(kind="text", text=part["text"], raw=msg)
                    inline = part.get("inlineData")
                    if inline and inline.get("data"):
                        sent = True
                        yield LiveEvent(kind="audio", audio=base64.b64decode(inline["data"]), raw=msg)
                tc = msg.get("toolCall") or {}
                if tc.get("functionCalls"):
                    sent = True
                    yield LiveEvent(kind="tool_call", tool_calls=tc["functionCalls"], raw=msg)
                if sc.get("turnComplete") or sc.get("generationComplete"):
                    sent = True
                    yield LiveEvent(kind="turn_complete", raw=msg)
                if not sent:                    # goAway, a voice-activity signal, an error object, anything we do not know yet
                    yield LiveEvent(kind="other", raw=msg, text=None if msg else str(raw)[:160])   # an empty one: keep what it looked like
        except websockets.ConnectionClosed as c:
            yield LiveEvent(kind="error", text=f"closed code={c.code} reason={c.reason}", raw=None)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


def _loads(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ── telephony audio helpers (8 kHz µ-law <-> 16/24 kHz PCM16) ───────────────────
# Minimal, stdlib-only (audioop). The media bridge can use these to bridge Smartflo <-> Gemini Live.
def ulaw8k_to_pcm16k(ulaw: bytes) -> bytes:
    import audioop
    pcm8k = audioop.ulaw2lin(ulaw, 2)                       # 8 kHz PCM16
    pcm16k, _ = audioop.ratecv(pcm8k, 2, 1, 8000, INPUT_RATE, None)
    return pcm16k


def pcm24k_to_ulaw8k(pcm24k: bytes) -> bytes:
    import audioop
    pcm8k, _ = audioop.ratecv(pcm24k, 2, 1, OUTPUT_RATE, 8000, None)
    return audioop.lin2ulaw(pcm8k, 2)
