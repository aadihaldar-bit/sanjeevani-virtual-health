"""
STT, TTS, and Gnani credits API endpoints for the Speech Playground.
"""
import os
import time
import logging
import httpx
from fastapi import APIRouter, UploadFile, File, Form, Request
from fastapi.responses import Response

from .ai_services import STTEngineFactory, TTSEngineFactory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stt", tags=["speech"])

# ─── Gnani Credits ─────────────────────────────────────────────────────────────

credits_router = APIRouter(prefix="/api/gnani", tags=["gnani"])

from fastapi import APIRouter, UploadFile, File, Form, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from .database import get_db

@credits_router.get("/credits")
async def get_gnani_credits(db: AsyncSession = Depends(get_db)):
    """Fetch Gnani API credit balance. Calculates real call minutes from DB if API scope restricted."""
    api_key = os.getenv("GNANI_API_KEY", "")
    if api_key:
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(
                    "https://api.vachana.ai/api/v1/credits",
                    headers={"X-API-Key-ID": api_key},
                    timeout=3.0,
                )
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "used": data.get("used", 0),
                        "total": data.get("total", 1000),
                        "source": "gnani_live",
                    }
        except Exception as e:
            logger.warning(f"[Credits] Gnani API: {e}")

    # Query real call audio minutes from database
    try:
        from sqlalchemy import select, func
        from .models import Call
        res = await db.execute(select(func.sum(Call.duration_seconds)))
        total_seconds = res.scalar() or 0
        used_mins = round(total_seconds / 60, 1)
        return {
            "used": int(used_mins) if used_mins == int(used_mins) else used_mins,
            "total": 1000,
            "source": "database_live"
        }
    except Exception as e:
        logger.warning(f"[Credits] DB query error: {e}")
        return {"used": 34, "total": 1000, "source": "fallback"}



@router.get("/config")
async def get_stt_config():
    """Return STT engine configuration and available languages."""
    return {
        "engine": "gnani-prisma-v2.5",
        "sample_rate": 8000,
        "languages": ["en-IN", "hi-IN", "mr-IN", "ta-IN"],
        "status": "active"
    }


@router.post("/transcribe")
async def transcribe_audio(
    audio_file: UploadFile = File(...),
    language_code: str = Form(default="en-IN"),
):
    """Transcribe an uploaded WAV audio file using the configured STT engine (Gnani Prisma v2.5)."""
    audio_bytes = await audio_file.read()
    stt = STTEngineFactory.get_engine()
    t0 = time.time()
    try:
        transcript = await stt.transcribe_bytes(audio_bytes, format="wav", language_code=language_code)
    except Exception as e:
        logger.error(f"[STT API] Transcription error: {e}")
        transcript = ""
    latency_ms = round((time.time() - t0) * 1000)
    return {
        "transcript": transcript,
        "model": "gnani-prisma-v2.5",
        "language_code": language_code,
        "latency_ms": latency_ms,
    }


# ─── TTS router ────────────────────────────────────────────────────────────────

tts_router = APIRouter(prefix="/api/tts", tags=["speech"])


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Convert raw linear16 PCM audio bytes into standard browser-playable RIFF WAV bytes."""
    if not pcm_bytes:
        return b""
    if pcm_bytes.startswith(b"RIFF") or pcm_bytes.startswith(b"wave"):
        return pcm_bytes
    try:
        import wave, io
        wav_io = io.BytesIO()
        with wave.open(wav_io, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
        return wav_io.getvalue()
    except Exception as e:
        logger.warning(f"[TTS API] Error converting PCM to WAV: {e}")
        return pcm_bytes


def mulaw_to_wav(mulaw_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Convert raw mu-law audio bytes into standard browser-playable RIFF WAV bytes."""
    if not mulaw_bytes:
        return b""
    if mulaw_bytes.startswith(b"RIFF") or mulaw_bytes.startswith(b"wave"):
        return mulaw_bytes
    try:
        try:
            import audioop
        except ImportError:
            import audioop_lts as audioop
        pcm_data = audioop.ulaw2lin(mulaw_bytes, 2)
        return pcm_to_wav(pcm_data, sample_rate=sample_rate)
    except Exception as e:
        logger.warning(f"[TTS API] Error converting mulaw to WAV: {e}")
        return mulaw_bytes


LANG_NAME_MAP = {
    "bn-IN": "Bengali", "bn": "Bengali",
    "hi-IN": "Hindi", "hi": "Hindi",
    "mr-IN": "Marathi", "mr": "Marathi",
    "ta-IN": "Tamil", "ta": "Tamil",
    "te-IN": "Telugu", "te": "Telugu",
    "gu-IN": "Gujarati", "gu": "Gujarati",
    "kn-IN": "Kannada", "kn": "Kannada",
    "ml-IN": "Malayalam", "ml": "Malayalam",
    "pa-IN": "Punjabi", "pa": "Punjabi",
}

async def _maybe_translate_for_tts(text: str, language_code: str) -> str:
    """If target language is non-English and text contains Latin script, auto-translate into target script."""
    target_lang = LANG_NAME_MAP.get(language_code)
    if not target_lang or language_code in ("en-IN", "en-US", "en"):
        return text
    
    # Check if text contains Latin letters that need translation
    latin_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    if latin_chars < len(text) * 0.2:
        return text  # Already written in native script
    
    try:
        from .ai_services import LLMEngineFactory
        engine = LLMEngineFactory.get_engine()
        prompt = f"Translate the following sentence into natural, fluent {target_lang} script. Return ONLY the translated sentence in {target_lang} script without any quotes, Romanization, or explanation.\n\nText: {text}"
        translated = await engine.generate(
            prompt=prompt,
            history=[],
            system_prompt=f"You are an expert neural translator into {target_lang}."
        )
        cleaned = translated.strip().strip('"').strip("'")
        if cleaned:
            logger.info(f"[TTS API] Auto-translated '{text}' -> '{cleaned}' ({target_lang})")
            return cleaned
    except Exception as e:
        logger.warning(f"[TTS API] Auto-translation to {target_lang} failed: {e}")
    
    return text


@tts_router.post("/synthesize")
async def synthesize_speech(
    request: Request,
):
    """Synthesize speech from text using the configured TTS engine, supporting both JSON and Form data."""
    text = ""
    voice = "Aarohi"
    language_code = "en-IN"

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            text = body.get("text", "")
            voice = body.get("speaker") or body.get("voice") or "Aarohi"
            language_code = body.get("language_code") or body.get("language") or "en-IN"
        except Exception as e:
            logger.warning(f"[TTS API] JSON parse error: {e}")
    else:
        try:
            form = await request.form()
            text = str(form.get("text", ""))
            voice = str(form.get("voice") or form.get("speaker") or "Aarohi")
            language_code = str(form.get("language_code") or form.get("language") or "en-IN")
        except Exception as e:
            logger.warning(f"[TTS API] Form parse error: {e}")

    if not text.strip():
        return Response(content=b"", media_type="audio/wav")

    tts = TTSEngineFactory.get_engine()
    try:
        final_text = await _maybe_translate_for_tts(text, language_code)
        audio_bytes = await tts.synthesize_bytes(final_text, language_code=language_code, speaker=voice)
        wav_bytes = pcm_to_wav(audio_bytes, sample_rate=8000)
    except Exception as e:
        logger.error(f"[TTS API] Synthesis error: {e}")
        wav_bytes = b""
    return Response(content=wav_bytes, media_type="audio/wav")
