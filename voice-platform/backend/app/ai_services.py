import re
"""
AI Services Segment (STT, TTS, LLM)
Implements all speech and language model services conforming to Base Interfaces.
Adheres to Open/Closed Principle (OCP) and Liskov Substitution Principle (LSP).
"""

import os
import io
import asyncio
import base64
import logging
from typing import Optional, Dict, Any, List
import httpx
from openai import AsyncOpenAI
import google.generativeai as genai

try:
    from .interfaces import BaseSTTService, BaseTTSService, BaseLLMService
    from .config import AppConfig, LLMSettings, STTSettings, TTSSettings, get_config
    from .langfuse_tracer import record_llm_usage
except Exception:
    try:
        from app.interfaces import BaseSTTService, BaseTTSService, BaseLLMService
        from app.config import AppConfig, LLMSettings, STTSettings, TTSSettings, get_config
        from app.langfuse_tracer import record_llm_usage
    except Exception:
        try:
            from interfaces import BaseSTTService, BaseTTSService, BaseLLMService
            from config import AppConfig, LLMSettings, STTSettings, TTSSettings, get_config
            from langfuse_tracer import record_llm_usage
        except Exception:
            def record_llm_usage(*args, **kwargs):
                pass

logger = logging.getLogger(__name__)


OPENAI_VOICE_MAP = {
    "aarohi": "nova",
    "radhika": "shimmer",
    "hope": "fable",
    "shweta": "nova",
    "nalini": "shimmer",
    "deepak": "onyx",
    "gaurav": "echo",
    "alloy": "alloy",
    "hemraj": "onyx",
}

SARVAM_SPEAKERS = {
    "aditya",
    "ritu",
    "ashutosh",
    "priya",
    "neha",
    "rahul",
    "pooja",
    "rohan",
    "simran",
    "kavya",
    "amit",
    "dev",
    "ishita",
    "shreya",
    "ratan",
    "varun",
    "manan",
    "sumit",
    "roopa",
    "kabir",
    "aayan",
    "shubh",
    "advait",
    "anand",
    "tanya",
    "tarun",
    "sunny",
    "mani",
    "gokul",
    "vijay",
    "shruti",
    "suhani",
    "mohit",
    "kavitha",
    "rehan",
    "soham",
    "rupali",
}



# ─── Speech-to-Text (STT) Services ─────────────────────────────────────────────

class CartesiaSTT(BaseSTTService):
    """Cartesia Ink Speech-to-Text Integration."""

    def __init__(self, api_key: str = "", settings: Optional[STTSettings] = None):
        self.settings = settings or get_config().stt
        self.api_key = (api_key or self.settings.cartesia_api_key).strip()
        self.url = "https://api.cartesia.ai/stt/listen"

    async def transcribe_bytes(self, audio_bytes: bytes, format: str = "wav", language_code: Optional[str] = None) -> str:
        if not self.api_key:
            logger.warning("[CartesiaSTT] Missing API key.")
            return ""
        try:
            lang = language_code or self.settings.language_code
            headers = {
                "X-API-Key": self.api_key,
                "Cartesia-Version": "2024-06-10"
            }
            files = {"file": (f"audio.{format}", audio_bytes, f"audio/{format}")}
            data = {"language": lang}
            async with httpx.AsyncClient() as client:
                response = await client.post(self.url, headers=headers, files=files, data=data, timeout=15.0)
                if response.status_code == 200:
                    res_json = response.json()
                    transcript = res_json.get("text", "").strip()
                    logger.debug(f"[CartesiaSTT] Transcribed: '{transcript}'")
                    return transcript
                else:
                    logger.error(f"[CartesiaSTT] Status {response.status_code}: {response.text}")
                    return ""
        except Exception as e:
            logger.error(f"[CartesiaSTT] Error transcribing: {e}")
            return ""


class SarvamSTT(BaseSTTService):
    """Sarvam AI Speech-to-Text (Saarika v2.5) supporting Marathi, Hindi, & English."""

    def __init__(self, api_key: str = "", settings: Optional[STTSettings] = None):
        self.settings = settings or get_config().stt
        self.api_key = (api_key or self.settings.sarvam_api_key).strip()
        self.url = "https://api.sarvam.ai/speech-to-text"

    async def transcribe_bytes(self, audio_bytes: bytes, format: str = "wav", language_code: Optional[str] = None) -> str:
        if not self.api_key:
            logger.warning("[SarvamSTT] Missing API key.")
            return ""
        try:
            target_lang = language_code or self.settings.language_code
            headers = {"api-subscription-key": self.api_key}
            files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
            data = {
                "model": "saarika:v2.5",
                "language_code": target_lang,
                "with_timestamps": "false"
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(self.url, headers=headers, files=files, data=data, timeout=15.0)
                if response.status_code == 200:
                    res_json = response.json()
                    transcript = res_json.get("transcript", "").strip()
                    logger.debug(f"[SarvamSTT] ({target_lang}) Transcribed: '{transcript}'")
                    return transcript
        except Exception as e:
            logger.error(f"[SarvamSTT] Error transcribing: {e}")
            return ""


class GnaniSTT(BaseSTTService):
    """Gnani.ai Vach Prisma v2.5 Speech-to-Text Integration."""

    def __init__(self, api_key: str = "", settings: Optional[STTSettings] = None):
        self.settings = settings or get_config().stt
        self.api_key = (api_key or self.settings.gnani_api_key).strip()
        self.url = "https://api.vachana.ai/stt/v3"
        self.fallback_stt = CartesiaSTT(settings=self.settings) if self.settings.cartesia_api_key else None

    async def transcribe_bytes(self, audio_bytes: bytes, format: str = "wav", language_code: Optional[str] = None) -> str:
        if self.api_key:
            try:
                target_lang = language_code or self.settings.language_code
                if target_lang in ["en", "en-IN"]:
                    target_lang = "en-IN"
                elif target_lang in ["hi", "hi-IN"]:
                    target_lang = "hi-IN"
                elif target_lang in ["mr", "mr-IN"]:
                    target_lang = "mr-IN"

                headers = {"X-API-Key-ID": self.api_key}
                files = {"audio_file": (f"audio.{format}", audio_bytes, f"audio/{format}")}
                data = {"language_code": target_lang}

                async with httpx.AsyncClient() as client:
                    response = await client.post(self.url, headers=headers, files=files, data=data, timeout=10.0)
                    if response.status_code == 200:
                        res_json = response.json()
                        transcript = res_json.get("transcript", "").strip()
                        if transcript:
                            logger.info(f"[GnaniSTT] ({target_lang}) Transcribed: '{transcript}'")
                            return transcript
                    else:
                        logger.warning(f"[GnaniSTT] Status {response.status_code}: {response.text}")
            except Exception as e:
                logger.error(f"[GnaniSTT] Error transcribing: {e}")

        # Fallback to Groq Whisper STT
        try:
            groq_stt = GroqWhisperSTT(settings=self.settings)
            res = await groq_stt.transcribe_bytes(audio_bytes, format=format, language_code=language_code)
            if res:
                logger.info(f"[GnaniSTT] Groq Whisper fallback transcribed: '{res}'")
                return res
        except Exception as e:
            logger.error(f"[GnaniSTT] Groq Whisper fallback error: {e}")

        if self.fallback_stt:
            logger.info("[GnaniSTT] Falling back to Cartesia STT...")
            return await self.fallback_stt.transcribe_bytes(audio_bytes, format=format, language_code=language_code)
        return ""


class DeepgramSTT(BaseSTTService):
    """Deepgram Nova-2 en-IN Speech-to-Text — optimised for Indian English callers."""

    def __init__(self, api_key: str = "", settings: Optional[STTSettings] = None):
        self.settings = settings or get_config().stt
        self.api_key = (api_key or self.settings.deepgram_api_key).strip()
        # Model + locale are env-tunable (DEEPGRAM_MODEL, VOICE_LANGUAGE_CODE).
        self._model = getattr(self.settings, "deepgram_model", "nova-2") or "nova-2"
        self._language = getattr(self.settings, "language_code", "en-IN") or "en-IN"
        self._base_url = (
            "https://api.deepgram.com/v1/listen"
            f"?model={self._model}"
            f"&language={self._language}"
            "&smart_format=true"
            "&punctuate=true"
            "&filler_words=false"
        )
        # NOTE: Do NOT add encoding/sample_rate/channels here.
        # run_stt() sends a full WAV file (RIFF header + PCM data).
        # Deepgram auto-detects all audio params from the WAV container header.
        # Adding encoding=linear16 would tell Deepgram to expect raw PCM (no header),
        # causing it to misparse the RIFF bytes as audio → empty transcripts.

    async def transcribe_bytes(self, audio_bytes: bytes, format: str = "wav", language_code: Optional[str] = None) -> str:
        if not self.api_key:
            logger.warning("[DeepgramSTT] Missing API key — set DEEPGRAM_API_KEY env var.")
            return ""
        try:
            url = self._base_url
            if language_code and language_code != "en-IN":
                # Allow per-call language override (e.g. "hi" for Hindi-only callers)
                url = url.replace("language=en-IN", f"language={language_code}")

            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": f"audio/{format}"
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, content=audio_bytes, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                transcript = (
                    data.get("results", {})
                        .get("channels", [{}])[0]
                        .get("alternatives", [{}])[0]
                        .get("transcript", "")
                )
                logger.debug(f"[DeepgramSTT] Transcribed: '{transcript}'")
                return transcript.strip()
        except Exception as e:
            logger.error(f"[DeepgramSTT] Error transcribing: {e}")
            return ""



class GroqWhisperSTT(BaseSTTService):
    """Groq Whisper Large v3 Turbo ultra-fast STT."""

    def __init__(self, api_key: str = "", model_name: str = "whisper-large-v3-turbo", settings: Optional[STTSettings] = None):
        self.settings = settings or get_config().stt
        self.api_key = (api_key or os.getenv("GROQ_API_KEY", "")).strip()
        self.model_name = model_name
        self.url = "https://api.groq.com/openai/v1/audio/transcriptions"

    async def transcribe_bytes(self, audio_bytes: bytes, format: str = "wav", language_code: Optional[str] = None) -> str:
        if not self.api_key:
            logger.warning("[GroqWhisperSTT] Missing Groq API key.")
            return ""

        # Normalize incoming audio to 16kHz mono WAV using ffmpeg if not already 16kHz WAV
        is_16k_wav = False
        if audio_bytes.startswith(b"RIFF") and len(audio_bytes) > 44:
            try:
                import struct as _struct
                sr = _struct.unpack("<I", audio_bytes[24:28])[0]
                if sr == 16000:
                    is_16k_wav = True
            except Exception:
                pass

        if not is_16k_wav or format != "wav":
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-err_detect", "ignore_err", "-i", "pipe:0",
                    "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                wav_data, _ = await proc.communicate(input=audio_bytes)
                if wav_data and len(wav_data) > 44 and wav_data.startswith(b"RIFF"):
                    audio_bytes = wav_data
                    format = "wav"
            except Exception as ffmpeg_err:
                logger.debug(f"[GroqWhisperSTT] FFmpeg normalization notice: {ffmpeg_err}")

        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            files = {"file": (f"audio.{format}", audio_bytes, f"audio/{format}")}
            data = {
                "model": self.model_name,
                "response_format": "json",
                "temperature": 0.0,
                "prompt": "IT service desk, technical issue, printer problem, password reset, laptop issue, ticket number."
            }
            if language_code and language_code not in ["unknown", "en-IN", "hi-IN", "mr-IN"]:
                data["language"] = language_code.split("-")[0]

            async with httpx.AsyncClient() as client:
                response = await client.post(self.url, headers=headers, files=files, data=data, timeout=12.0)
                if response.status_code == 200:
                    res_json = response.json()
                    transcript = res_json.get("text", "").strip()
                    logger.info(f"[GroqWhisperSTT] Transcribed ({self.model_name}): '{transcript}'")
                    return transcript
                else:
                    logger.error(f"[GroqWhisperSTT] Status {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"[GroqWhisperSTT] Error: {e}")

        # Fallback to Sarvam STT if Groq returns status != 200 or errors
        if self.settings.sarvam_api_key or os.getenv("SARVAM_API_KEY"):
            try:
                logger.info("[GroqWhisperSTT] Attempting graceful fallback to Sarvam STT...")
                sarvam = SarvamSTT(settings=self.settings)
                return await sarvam.transcribe_bytes(audio_bytes, format=format, language_code=language_code)
            except Exception as fb_err:
                logger.error(f"[GroqWhisperSTT] Sarvam fallback failed: {fb_err}")

        return ""


class LocalSTT(BaseSTTService):
    """Local STT microservice fallback."""

    def __init__(self, base_url: str = "http://localhost:8001"):
        self.url = f"{base_url}/transcribe"

    async def transcribe_bytes(self, audio_bytes: bytes, format: str = "wav", language_code: Optional[str] = None) -> str:
        try:
            headers = {"Content-Type": f"audio/{format}"}
            async with httpx.AsyncClient() as client:
                response = await client.post(self.url, headers=headers, content=audio_bytes, timeout=10.0)
                response.raise_for_status()
                return response.json().get("text", "").strip()
        except Exception as e:
            logger.error(f"[LocalSTT] Error transcribing: {e}")
            return ""


# ─── Text-to-Speech (TTS) Services ─────────────────────────────────────────────

class CartesiaTTS(BaseTTSService):
    """Cartesia Sonic 3.5 Text-to-Speech Integration."""
    _class_quota_exhausted: bool = False

    def __init__(self, api_key: str = "", settings: Optional[TTSSettings] = None):
        self.settings = settings or get_config().tts
        self.api_key = (api_key or self.settings.cartesia_api_key).strip()
        self.sample_rate = self.settings.sample_rate
        self.url = "https://api.cartesia.ai/tts/bytes"
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=10.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=60.0),
            )
        return self._client

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        if not self.api_key or CartesiaTTS._class_quota_exhausted:
            return b""
        try:
            voice_id = self.settings.cartesia_voice_id
            if speaker and len(speaker) == 36 and "-" in speaker:
                voice_id = speaker

            headers = {
                "X-API-Key": self.api_key,
                "Cartesia-Version": "2024-06-10",
                "Content-Type": "application/json"
            }
            payload = {
                "model_id": self.settings.cartesia_model_id,
                "transcript": text,
                "voice": {
                    "mode": "id",
                    "id": voice_id
                },
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": self.sample_rate
                }
            }
            client = self._get_client()
            response = await client.post(self.url, headers=headers, json=payload)
            if response.status_code == 200:
                return response.content
            elif response.status_code == 402:
                logger.warning("[CartesiaTTS] Credit limit reached (402). Marking quota exhausted and bypassing for fallback.")
                CartesiaTTS._class_quota_exhausted = True
                return b""
            else:
                logger.error(f"[CartesiaTTS] Status {response.status_code}: {response.text}")
                return b""
        except Exception as e:
            logger.error(f"[CartesiaTTS] Error synthesizing: {e}")
            return b""


class FallbackTTS(BaseTTSService):
    """Primary TTS with automatic graceful fallback to a secondary TTS provider."""

    def __init__(self, primary: BaseTTSService, fallback: BaseTTSService):
        self.primary = primary
        self.fallback = fallback
        self.sample_rate = getattr(primary, "sample_rate", 8000)

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        try:
            audio = await self.primary.synthesize_bytes(text, format=format, language_code=language_code, speaker=speaker)
            if audio:
                return audio
            logger.warning(f"[FallbackTTS] Primary TTS ({type(self.primary).__name__}) returned empty audio. Falling back...")
        except Exception as e:
            logger.warning(f"[FallbackTTS] Primary TTS ({type(self.primary).__name__}) failed: {e}. Falling back...")

        try:
            logger.info(f"[FallbackTTS] Engaging fallback TTS ({type(self.fallback).__name__})...")
            return await self.fallback.synthesize_bytes(text, format=format, language_code=language_code, speaker=speaker)
        except Exception as e:
            logger.error(f"[FallbackTTS] Fallback TTS ({type(self.fallback).__name__}) failed: {e}")
            return b""


class SarvamTTS(BaseTTSService):
    """Sarvam AI Bulbul v3 Text-to-Speech Integration for Indian Languages."""

    def __init__(self, api_key: str = "", settings: Optional[TTSSettings] = None):
        self.settings = settings or get_config().tts
        self.api_key = (api_key or self.settings.sarvam_api_key).strip()
        self.sample_rate = self.settings.sample_rate
        self.url = "https://api.sarvam.ai/text-to-speech"

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        if not self.api_key:
            logger.warning("[SarvamTTS] Missing API key.")
            return b""
        try:
            target_lang = language_code or get_config().stt.language_code
            if target_lang in ["en", "en-US", "en-IN"]:
                target_lang = "en-IN"
            elif target_lang in ["hi", "hi-IN"]:
                target_lang = "hi-IN"
            elif target_lang in ["mr", "mr-IN"]:
                target_lang = "mr-IN"

            target_speaker = (speaker or self.settings.sarvam_speaker or "ritu").strip().lower()
            valid_bulbul_v3 = SARVAM_SPEAKERS
            if target_speaker not in valid_bulbul_v3:
                target_speaker = "ritu"

            headers = {
                "api-subscription-key": self.api_key,
                "Content-Type": "application/json"
            }
            payload = {
                "inputs": [text],
                "target_language_code": target_lang,
                "speaker": target_speaker,
                "model": "bulbul:v3"
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(self.url, headers=headers, json=payload, timeout=15.0)
                if response.status_code == 200:
                    data = response.json()
                    audios = data.get("audios", [])
                    if audios:
                        wav_bytes = base64.b64decode(audios[0])
                        if format in ["wav", "mp3"]:
                            return wav_bytes

                        # Auto-parse WAV sample rate (Sarvam Bulbul v3 synthesizes at 22,050Hz)
                        # Resample to 8,000Hz PCM16 for telephony & stream pipelines
                        import audioop, struct
                        header_sr = 22050
                        pcm_raw = wav_bytes
                        if wav_bytes.startswith(b'RIFF') and len(wav_bytes) > 44:
                            try:
                                header_sr = struct.unpack('<I', wav_bytes[24:28])[0]
                            except Exception:
                                pass
                            data_idx = wav_bytes.find(b'data')
                            pcm_raw = wav_bytes[data_idx + 8:] if data_idx != -1 else wav_bytes[44:]

                        if header_sr != 8000 and len(pcm_raw) > 4:
                            pcm_8k, _ = audioop.ratecv(pcm_raw, 2, 1, header_sr, 8000, None)
                            return pcm_8k
                        return pcm_raw
                else:
                    logger.warning(f"[SarvamTTS] Status {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"[SarvamTTS] Error synthesizing: {e}")
        return b""


class GnaniTTS(BaseTTSService):
    """Gnani.ai Timbre v2.5 Text-to-Speech Integration."""

    def __init__(self, api_key: str = "", settings: Optional[TTSSettings] = None):
        self.settings = settings or get_config().tts
        self.api_key = (api_key or self.settings.gnani_api_key).strip()
        self.sample_rate = 8000
        self.output_format = "mulaw"  # Gnani returns native 8kHz mulaw
        self.url = "https://api.vachana.ai/api/v1/tts/inference"
        if self.settings.sarvam_api_key:
            self.fallback_tts = SarvamTTS(settings=self.settings)
        elif self.settings.cartesia_api_key:
            self.fallback_tts = CartesiaTTS(settings=self.settings)
        else:
            self.fallback_tts = None

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        if self.api_key:
            try:
                target_lang = language_code or get_config().stt.language_code
                lang_short = target_lang.split("-")[0] if "-" in target_lang else target_lang
                target_voice = speaker or getattr(self.settings, 'gnani_voice', 'Aarohi')
                model_id = getattr(self.settings, 'gnani_model_id', 'timbre-v2.5')

                headers = {
                    "X-API-Key-ID": self.api_key,
                    "Content-Type": "application/json"
                }
                payload = {
                    "text": text,
                    "language": lang_short,
                    "voice": target_voice,
                    "model": model_id,
                    "audio_config": {
                        "container": "mulaw",
                        "sample_rate": 8000,
                        "num_channels": 1
                    }
                }

                async with httpx.AsyncClient() as client:
                    response = await client.post(self.url, headers=headers, json=payload, timeout=6.0)
                    if response.status_code == 200:
                        raw_content = response.content
                        if raw_content:
                            logger.info(f"[GnaniTTS] Synthesized {len(raw_content)} bytes mulaw ({len(raw_content)/8000:.1f}s)")
                            # Gnani returns raw 8kHz mulaw bytes — convert to PCM16 for the pipeline
                            try:
                                import audioop
                            except ImportError:
                                import audioop_lts as audioop
                            pcm_bytes = audioop.ulaw2lin(raw_content, 2)
                            return pcm_bytes
                    else:
                        logger.warning(f"[GnaniTTS] Status {response.status_code}: {response.text}")
            except Exception as e:
                logger.warning(f"[GnaniTTS] Error synthesizing: {e}")

        # Fallback to OpenAI TTS
        try:
            openai_tts = OpenAITTS(settings=self.settings)
            res = await openai_tts.synthesize_bytes(text, format=format, language_code=language_code, speaker=speaker)
            if res:
                logger.info(f"[GnaniTTS] OpenAI TTS fallback synthesized {len(res)} bytes PCM")
                return res
        except Exception as e:
            logger.error(f"[GnaniTTS] OpenAI TTS fallback error: {e}")

        if self.fallback_tts:
            res = await self.fallback_tts.synthesize_bytes(text, format=format, language_code=language_code, speaker=speaker)
            if res:
                return res

        logger.info("[GnaniTTS] Falling back to EdgeTTS zero-key neural voice...")
        fallback_edge = EdgeTTS()
        return await fallback_edge.synthesize_bytes(text, format=format, language_code=language_code, speaker=speaker)


class OpenAITTS(BaseTTSService):
    """OpenAI tts-1 Text-to-Speech Integration."""

    def __init__(self, api_key: str = "", settings: Optional[TTSSettings] = None):
        self.settings = settings or get_config().tts
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.sample_rate = 8000
        self.url = "https://api.openai.com/v1/audio/speech"

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        if not self.api_key:
            return b""
        try:
            spk_clean = (speaker or "aarohi").strip().lower()
            target_voice = OPENAI_VOICE_MAP.get(spk_clean, "alloy")
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "tts-1",
                "input": text,
                "voice": target_voice,
                "response_format": "pcm"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.url, headers=headers, json=payload)
                if response.status_code == 200:
                    raw_pcm = response.content
                    try:
                        import audioop
                    except ImportError:
                        import audioop_lts as audioop
                    resampled, _ = audioop.ratecv(raw_pcm, 2, 1, 24000, 8000, None)
                    return resampled
        except Exception as e:
            logger.error(f"[OpenAITTS] Error synthesizing: {e}")
        return b""


DEEPGRAM_VOICE_MAP = {
    # Female Personas
    "aarohi": "aura-asteria-en",
    "radhika": "aura-luna-en",
    "hope": "aura-stella-en",
    "shweta": "aura-hera-en",
    "nalini": "aura-athena-en",
    # Male Personas
    "deepak": "aura-orion-en",
    "gaurav": "aura-arcas-en",
    "alloy": "aura-orpheus-en",
    "hemraj": "aura-zeus-en",
}


class DeepgramTTS(BaseTTSService):
    """Deepgram Aura TTS Integration with multi-persona voice routing."""

    def __init__(self, api_key: str = "", settings: Optional[TTSSettings] = None):
        self.settings = settings or get_config().tts
        self.api_key = (api_key or self.settings.deepgram_api_key).strip()

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        if not self.api_key:
            logger.warning("[DeepgramTTS] Missing API key.")
            return b""
        try:
            spk_clean = (speaker or "aarohi").strip().lower()
            if spk_clean.startswith("aura-"):
                model = spk_clean
            else:
                model = DEEPGRAM_VOICE_MAP.get(spk_clean, "aura-asteria-en")

            url = f"https://api.deepgram.com/v1/speak?model={model}&encoding=linear16&sample_rate=8000"
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json"
            }
            payload = {"text": text}
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.content
        except Exception as e:
            logger.error(f"[DeepgramTTS] Error synthesizing ({speaker} -> {model if 'model' in locals() else 'default'}): {e}")
            return b""


MURF_VOICE_MAP = {
    "aarohi": "en-IN-arohi",
    "isha": "en-IN-isha",
    "anisha": "en-IN-anisha",
    "samar": "en-IN-samar",
    "nikhil": "en-IN-nikhil",
    "khyati": "hi-IN-khyati",
    "radhika": "en-IN-arohi",
    "hope": "en-IN-isha",
    "shweta": "en-IN-arohi",
    "deepak": "en-IN-samar",
    "gaurav": "en-IN-nikhil",
    "alloy": "en-US-cooper",
    "hazel": "en-UK-hazel",
}


class MurfTTS(BaseTTSService):
    """
    Murf AI Falcon-2 Ultra-Low Latency Streaming Text-to-Speech Integration.
    Uses https://global.api.murf.ai/v1/speech/stream for fast streaming synthesis.
    """

    def __init__(self, api_key: str = "", settings: Optional[TTSSettings] = None):
        self.settings = settings or get_config().tts
        self.api_key = (api_key or getattr(self.settings, "murf_api_key", "") or os.getenv("MURF_API_KEY", "")).strip()
        self.sample_rate = getattr(self.settings, "sample_rate", 8000)
        self.model = getattr(self.settings, "murf_model_id", "falcon-2")
        self.url = "https://global.api.murf.ai/v1/speech/stream"
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=10.0,
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=60.0),
            )
        return self._client

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        if not self.api_key or not text or not text.strip():
            return b""
        try:
            spk_clean = (speaker or "aarohi").strip().lower()
            if spk_clean in MURF_VOICE_MAP:
                voice_id = MURF_VOICE_MAP[spk_clean]
            elif "-" in spk_clean and len(spk_clean) > 4:
                voice_id = speaker
            else:
                voice_id = getattr(self.settings, "murf_voice_id", "en-IN-arohi")

            headers = {
                "api-key": self.api_key,
                "Content-Type": "application/json"
            }
            payload = {
                "text": text.strip(),
                "voiceId": voice_id,
                "model": self.model,
                "sample_rate": self.sample_rate
            }

            client = self._get_client()
            response = await client.post(self.url, headers=headers, json=payload)
            if response.status_code == 200:
                wav_bytes = response.content
                if format == "pcm" and wav_bytes.startswith(b'RIFF'):
                    data_idx = wav_bytes.find(b'data')
                    if data_idx != -1:
                        return wav_bytes[data_idx + 8:]
                return wav_bytes
            else:
                logger.error(f"[MurfTTS] Error {response.status_code}: {response.text}")
                return b""
        except Exception as e:
            logger.error(f"[MurfTTS] Synthesis error: {e}")
            return b""


class EdgeTTS(BaseTTSService):
    """Microsoft Edge Neural TTS (Free, high-fidelity neural voice engine, zero API keys required)."""

    def __init__(self, voice_name: str = "en-IN-NeerjaNeural"):
        self.voice_name = voice_name
        self.sample_rate = 24000

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        if not text or not text.strip():
            return b""
        try:
            import edge_tts
            voice = speaker or self.voice_name
            if not voice or len(voice) < 5 or voice in ["Aarohi", "Aarti", "Default"]:
                voice = "en-IN-NeerjaNeural"
            
            communicate = edge_tts.Communicate(text.strip(), voice)
            mp3_bytes = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_bytes += chunk["data"]

            if not mp3_bytes:
                return b""

            if format == "mp3":
                return mp3_bytes

            import subprocess
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "8000", "pipe:1",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate(input=mp3_bytes)
                if stdout:
                    return stdout
            except Exception:
                pass

            return mp3_bytes
        except Exception as e:
            logger.error(f"[EdgeTTS] Error synthesizing: {e}")
            return b""


class LocalTTS(BaseTTSService):
    """Local TTS microservice fallback."""

    def __init__(self, base_url: str = "http://localhost:8002"):
        self.url = f"{base_url}/synthesize"
        self.fallback = EdgeTTS()

    async def synthesize_bytes(self, text: str, format: str = "pcm", language_code: Optional[str] = None, speaker: Optional[str] = None) -> bytes:
        try:
            headers = {"Content-Type": "application/json"}
            payload = {"text": text}
            async with httpx.AsyncClient() as client:
                response = await client.post(self.url, headers=headers, json=payload, timeout=5.0)
                if response.status_code == 200:
                    wav_bytes = response.content
                    if wav_bytes.startswith(b'RIFF'):
                        data_idx = wav_bytes.find(b'data')
                        if data_idx != -1:
                            return wav_bytes[data_idx + 8:]
                    return wav_bytes
        except Exception as e:
            logger.debug(f"[LocalTTS] Fallback to EdgeTTS: {e}")

        return await self.fallback.synthesize_bytes(text, format=format, language_code=language_code, speaker=speaker)


# ─── Large Language Model (LLM) Services ───────────────────────────────────────

class OpenAILLM(BaseLLMService):
    """Universal OpenAI-compatible LLM client (Groq LPU, OpenAI, Local vLLM)."""

    def __init__(self, base_url: str, api_key: str, model_name: str, settings: Optional[LLMSettings] = None,
                 extra_headers: Optional[Dict[str, str]] = None):
        self.settings = settings or get_config().llm
        # Real-time telephony: strictly 0 retries and a 3.5s timeout so the caller is never left in silence
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "local-placeholder",
            max_retries=0,
            timeout=3.5,
            default_headers=extra_headers or None,
        )
        self.model_name = model_name or "openai/gpt-oss-20b"

    # FallbackLLM asks for strict=True so a failure raises instead of becoming a canned sentence.
    supports_strict = True
    supports_raw = True        # raw=True skips the speech-text cleaning (needed for JSON / identifiers)

    def _groq_reasoning_effort(self, model: str) -> Optional[str]:
        """Hidden-reasoning cap per Groq model family: gpt-oss uses the configured level, qwen is fastest with none."""
        if model.startswith("openai/gpt-oss"):
            return (getattr(self.settings, "reasoning_effort", "") or "").strip() or None
        if model.startswith("qwen/"):
            return "none"
        return None

    async def generate(self, prompt: str, history: List[Dict[str, str]], system_prompt: str, tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 250, tool_choice: Any = "auto", strict: bool = False, raw: bool = False) -> Any:
        try:
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": prompt})

            formatted_tools = None
            if tools:
                formatted_tools = []
                for t in tools:
                    formatted_tools.append({
                        "type": "function",
                        "function": {
                            "name": t.get("name"),
                            "description": t.get("description", ""),
                            "parameters": t.get("parameters", {})
                        }
                    })

            selected_model = self.model_name
            if "groq.com" in str(self.client.base_url):
                logger.info(f"[Groq LLM Request] Model: {selected_model} | Prompt: '{prompt[:120]}...'")

            kwargs = {
                "model": selected_model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": max_tokens
            }
            # Cap hidden reasoning tokens on Groq gpt-oss (reasoning model) to cut TTFT.
            _reasoning = self._groq_reasoning_effort(selected_model) if "groq.com" in str(self.client.base_url) else None
            if _reasoning:
                kwargs["extra_body"] = {"reasoning_effort": _reasoning}
            if formatted_tools and "localhost" not in str(self.client.base_url):
                kwargs["tools"] = formatted_tools
                kwargs["tool_choice"] = tool_choice or "auto"

            try:
                response = await self.client.chat.completions.create(**kwargs)
            except Exception as tool_err:
                if strict:
                    raise
                err_str = str(tool_err).lower()
                if "429" in err_str or "rate limit" in err_str:
                    logger.warning(f"[OpenAILLM] 429 on {kwargs.get('model')}, retrying on openai/gpt-oss-20b...")
                    kwargs["model"] = "openai/gpt-oss-20b"
                    response = await self.client.chat.completions.create(**kwargs)
                else:
                    logger.warning(f"[OpenAILLM] Tool generation failed ({tool_err}), retrying without tools on openai/gpt-oss-20b...")
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                    kwargs["model"] = "openai/gpt-oss-20b"
                    response = await self.client.chat.completions.create(**kwargs)

            # Record token usage for observability & cost tracking in Langfuse
            if hasattr(response, "usage") and response.usage:
                p_tok = getattr(response.usage, "prompt_tokens", 0) or 0
                c_tok = getattr(response.usage, "completion_tokens", 0) or 0
                t_tok = getattr(response.usage, "total_tokens", 0) or (p_tok + c_tok)
                record_llm_usage(p_tok, c_tok, t_tok, model=selected_model)

            message = response.choices[0].message
            
            if hasattr(message, "tool_calls") and message.tool_calls:
                fc = message.tool_calls[0].function
                import json
                try:
                    args = json.loads(fc.arguments)
                except Exception:
                    args = {}
                logger.info(f"[Groq LLM Tool Call] Function: '{fc.name}' | Args: {args}")
                return {
                    "tool_call": True,
                    "name": fc.name,
                    "args": args
                }
            
            content = (message.content or "").strip()
            if not content and hasattr(message, "reasoning_content") and message.reasoning_content:
                content = str(message.reasoning_content).strip()

            # Clean and sanitize speech text (strip think tokens, markdown, unicode dashes/quotes)
            if content:
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if content and not raw:
                content = re.sub(r'[*#_`|]', '', content).strip()
                # sanitize unicode punctuation for windows/tts
                content = content.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
                content = content.replace('–', '-').replace('—', '-').replace('‑', '-').replace(' ', ' ')
                
            if (not content or len(content.strip()) < 3) and strict:
                raise RuntimeError(f"{selected_model} returned an empty answer")
            if not content or len(content.strip()) < 3:
                content = "I understand your request. Let me check our records and assist you with that right away."
                
            if isinstance(content, str):
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                if "</think>" in content:
                    content = content.split("</think>")[-1].strip()
            
            logger.info(f"[Groq LLM Response] Model: {selected_model} | Output: '{content[:120]}...'")
            return content
        except Exception as e:
            if strict:
                raise
            logger.warning(f"[OpenAILLM/{self.model_name}] Primary call failed ({e}), attempting secondary model...")
            if "groq.com" in str(self.client.base_url):
                try:
                    kwargs_fallback = {
                        "model": "openai/gpt-oss-20b",
                        "messages": messages,
                        "temperature": 0.2,
                        "max_tokens": 150
                    }
                    fb_resp = await self.client.chat.completions.create(**kwargs_fallback)
                    if hasattr(fb_resp, "usage") and fb_resp.usage:
                        p_tok = getattr(fb_resp.usage, "prompt_tokens", 0) or 0
                        c_tok = getattr(fb_resp.usage, "completion_tokens", 0) or 0
                        t_tok = getattr(fb_resp.usage, "total_tokens", 0) or (p_tok + c_tok)
                        record_llm_usage(p_tok, c_tok, t_tok, model="openai/gpt-oss-20b")
                    msg_fb = fb_resp.choices[0].message
                    content_fb = (msg_fb.content or "").strip()
                    if content_fb:
                        content_clean = re.sub(r'<think>.*?</think>', '', content_fb, flags=re.DOTALL).strip()
                        return re.sub(r'[*#_`|]', '', content_clean).strip()
                except Exception as fb_err:
                    logger.error(f"[OpenAILLM Groq Secondary Failed]: {fb_err}")
            return "I understand your issue and I am checking our support system right away. Let me guide you with the next step."

    async def generate_stream(
        self,
        prompt: str,
        history: List[Dict[str, str]],
        system_prompt: str
    ):
        """
        Stream LLM tokens as an async generator.

        Yields text chunks as they arrive from the LLM, enabling
        sentence-level TTS pipelining — TTS can start synthesizing
        the first sentence while the LLM is still generating the rest.

        Falls back to a single non-streamed generate() call if streaming
        fails (e.g. rate limit, network error).
        """
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        selected_model = self.model_name
        if "groq.com" in str(self.client.base_url):
            selected_model = "openai/gpt-oss-20b"

        try:
            _stream_kwargs = {
                "model": selected_model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 250,
                "stream": True,
            }
            _reasoning = getattr(self.settings, "reasoning_effort", "").strip()
            if _reasoning and "groq.com" in str(self.client.base_url):
                _stream_kwargs["extra_body"] = {"reasoning_effort": _reasoning}
            stream = await self.client.chat.completions.create(**_stream_kwargs)
            in_think_block = False
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta:
                    continue
                token = delta.content or ""
                if not token:
                    continue

                # Strip <think>...</think> tokens inline during streaming
                if "<think>" in token:
                    in_think_block = True
                if in_think_block:
                    if "</think>" in token:
                        in_think_block = False
                        token = token.split("</think>", 1)[-1]
                    else:
                        continue

                # Strip markdown symbols that TTS would read aloud
                token = re.sub(r'[*#_`|]', '', token)
                # Normalize unicode dashes/quotes
                token = (token
                         .replace('\u2018', "'").replace('\u2019', "'")
                         .replace('\u201c', '"').replace('\u201d', '"')
                         .replace('\u2013', '-').replace('\u2014', '-'))
                if token:
                    yield token

        except Exception as e:
            logger.warning(f"[OpenAILLM] Streaming failed ({e}), falling back to generate()")
            # Fallback: yield the complete non-streamed response as one chunk
            try:
                result = await self.generate(prompt, history, system_prompt)
                if isinstance(result, str):
                    yield result
                elif isinstance(result, dict) and "content" in result:
                    yield result["content"]
            except Exception as fallback_err:
                logger.error(f"[OpenAILLM] Fallback generate() also failed: {fallback_err}")
                yield "I understand your request. Let me assist you with the next step."




class GeminiLLM(BaseLLMService):
    """Google Gemini Flash / Pro Multimodal LLM integration."""

    def __init__(self, api_key: str, model_name: str = "gemini-flash-latest"):
        self.api_key = api_key
        self.model_name = model_name or "gemini-flash-latest"
        genai.configure(api_key=self.api_key)

    async def generate(self, prompt: str, history: List[Dict[str, str]], system_prompt: str, tools: Optional[List[Dict[str, Any]]] = None) -> Any:
        try:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt,
                tools=tools
            )
            formatted_history = []
            for msg in history:
                role = "model" if msg["role"] == "assistant" else "user"
                formatted_history.append({"role": role, "parts": [msg["content"]]})
                
            def _run():
                chat = model.start_chat(history=formatted_history)
                response = chat.send_message(prompt)
                
                # Record token usage for observability & cost tracking in Langfuse
                usage_meta = getattr(response, "usage_metadata", None)
                if usage_meta:
                    p_tok = getattr(usage_meta, "prompt_token_count", 0) or 0
                    c_tok = getattr(usage_meta, "candidates_token_count", 0) or 0
                    t_tok = getattr(usage_meta, "total_token_count", 0) or (p_tok + c_tok)
                    record_llm_usage(p_tok, c_tok, t_tok, model=self.model_name)

                if response.parts and hasattr(response.parts[0], 'function_call') and response.parts[0].function_call:
                    fc = response.parts[0].function_call
                    return {
                        "tool_call": True,
                        "name": fc.name,
                        "args": {k: v for k, v in fc.args.items()}
                    }
                return response.text.strip()
                
            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.error(f"[GeminiLLM] Error generating: {e}")
            return "I understand your issue and I am processing your support request right away. Let me assist you with the next steps."


# ─── Service Factories (Open/Closed Principle & Dependency Inversion) ─────────

class LLMEngineFactory:
    """Factory to instantiate BaseLLMService based on centralized configuration."""

    @staticmethod
    def get_engine(config: Optional[AppConfig] = None) -> BaseLLMService:
        cfg = config or get_config()
        provider = cfg.llm.provider
        
        if provider == "groq":
            return OpenAILLM(
                base_url=cfg.llm.groq_base_url,
                api_key=cfg.llm.groq_api_key,
                model_name=cfg.llm.groq_model,
                settings=cfg.llm
            )
        elif provider == "custom" and cfg.llm.custom_base_url:
            return OpenAILLM(
                base_url=cfg.llm.custom_base_url,
                api_key=cfg.llm.custom_api_key,
                model_name=cfg.llm.custom_model,
                settings=cfg.llm,
                extra_headers=dict(cfg.llm.custom_headers),
            )
        elif provider == "gemini":
            return GeminiLLM(
                api_key=cfg.llm.gemini_api_key,
                model_name=cfg.llm.gemini_model
            )
        elif provider in ["openai", "local"]:
            return OpenAILLM(
                base_url=cfg.llm.openai_base_url,
                api_key=cfg.llm.openai_api_key,
                model_name=cfg.llm.openai_model,
                settings=cfg.llm
            )
        else:
            return OpenAILLM(
                base_url=cfg.llm.groq_base_url,
                api_key=cfg.llm.groq_api_key,
                model_name=cfg.llm.groq_model,
                settings=cfg.llm
            )


async def generate_raw(engine: Any, prompt: str, history: List[Dict[str, str]], system_prompt: str, max_tokens: int) -> Any:
    """Ask a model for a reply that a program reads (JSON, ids) rather than a caller hears. generate() defaults to a short
    spoken answer (250 tokens) and tidies the text for speech, which cuts a JSON document off and strips the underscores out of
    its keys. So ask for room to finish and raw=True, and strict=True so a failure raises instead of coming back as a canned
    customer-service sentence that looks like an answer. Engines differ in what they accept: ask for less when one refuses."""
    base = dict(prompt=prompt, history=history, system_prompt=system_prompt)
    for extra in ({"max_tokens": max_tokens, "raw": True, "strict": True}, {"max_tokens": max_tokens, "raw": True},
                  {"max_tokens": max_tokens}, {}):
        try:
            return await engine.generate(**base, **extra)
        except TypeError:
            continue
    return await engine.generate(**base)


class STTEngineFactory:
    """Factory to instantiate BaseSTTService based on centralized configuration."""

    @staticmethod
    def get_engine(config: Optional[AppConfig] = None) -> BaseSTTService:
        cfg = config or get_config()
        provider = cfg.stt.provider

        if provider == "gnani" and cfg.stt.gnani_api_key:
            return GnaniSTT(settings=cfg.stt)
        elif (provider in ["groq", "whisper"]) and cfg.llm.groq_api_key:
            return GroqWhisperSTT(api_key=cfg.llm.groq_api_key, settings=cfg.stt)
        elif provider == "sarvam" and cfg.stt.sarvam_api_key:
            return SarvamSTT(settings=cfg.stt)
        elif provider == "cartesia" and cfg.stt.cartesia_api_key:
            return CartesiaSTT(settings=cfg.stt)
        elif provider == "deepgram" and cfg.stt.deepgram_api_key:
            return DeepgramSTT(settings=cfg.stt)
        elif cfg.stt.gnani_api_key:
            return GnaniSTT(settings=cfg.stt)
        elif cfg.llm.groq_api_key:
            return GroqWhisperSTT(api_key=cfg.llm.groq_api_key, settings=cfg.stt)
        elif cfg.stt.sarvam_api_key:
            return SarvamSTT(settings=cfg.stt)
        elif cfg.stt.cartesia_api_key:
            return CartesiaSTT(settings=cfg.stt)
        else:
            return LocalSTT()


class AzureTTS(BaseTTSService):
    """
    Microsoft Azure Cognitive Services Neural TTS.
    Uses en-IN-NeerjaNeural by default — natural Indian English female voice.
    Returns 8kHz PCM16 bytes (header-stripped) to match pipeline expectations.
    TTFB: ~200-300ms — significantly faster than Sarvam.
    """

    # Voice map: language_code → Azure neural voice name
    _VOICE_MAP = {
        "en-IN": "en-IN-NeerjaNeural",
        "en":    "en-IN-NeerjaNeural",
        "hi-IN": "hi-IN-SwaraNeural",
        "hi":    "hi-IN-SwaraNeural",
        "ta-IN": "ta-IN-PallaviNeural",
        "ta":    "ta-IN-PallaviNeural",
        "te-IN": "te-IN-ShrutiNeural",
        "te":    "te-IN-ShrutiNeural",
        "kn-IN": "kn-IN-SapnaNeural",
        "kn":    "kn-IN-SapnaNeural",
        "ml-IN": "ml-IN-SobhanaNeural",
        "ml":    "ml-IN-SobhanaNeural",
        "bn-IN": "bn-IN-TanishaaNeural",
        "bn":    "bn-IN-TanishaaNeural",
        "mr-IN": "mr-IN-AarohiNeural",
        "mr":    "mr-IN-AarohiNeural",
        "gu-IN": "gu-IN-DhwaniNeural",
        "gu":    "gu-IN-DhwaniNeural",
    }

    def __init__(self, settings: Optional[TTSSettings] = None):
        self.settings = settings or get_config().tts
        self.api_key = self.settings.azure_speech_key.strip()
        self.region = self.settings.azure_speech_region.strip() or "eastus"
        self.default_voice = self.settings.azure_tts_voice.strip() or "en-IN-NeerjaNeural"
        # Azure TTS REST endpoint — returns audio synchronously, no SDK needed
        self.url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        # Pipeline expects 8kHz PCM16 from synthesize_bytes(format="pcm")
        self.sample_rate = 8000

    def _build_ssml(self, text: str, voice: str) -> str:
        lang = "-".join(voice.split("-")[:2])  # e.g. "en-IN-NeerjaNeural" → "en-IN"
        # Escape XML special chars in text
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return (
            f'<speak version="1.0" xml:lang="{lang}">'
            f'<voice name="{voice}">'
            f'{safe}'
            f'</voice></speak>'
        )

    async def synthesize_bytes(
        self,
        text: str,
        format: str = "pcm",
        language_code: Optional[str] = None,
        speaker: Optional[str] = None,
    ) -> bytes:
        if not self.api_key:
            logger.warning("[AzureTTS] Missing AZURE_SPEECH_KEY — set env var.")
            return b""
        if not text or not text.strip():
            return b""
        try:
            # Resolve voice: explicit speaker override > language_code map > default
            voice = (
                speaker
                or self._VOICE_MAP.get(language_code or "", "")
                or self._VOICE_MAP.get((language_code or "").split("-")[0], "")
                or self.default_voice
            )

            ssml = self._build_ssml(text.strip(), voice)

            headers = {
                "Ocp-Apim-Subscription-Key": self.api_key,
                "Content-Type": "application/ssml+xml",
                # riff-8khz-16bit-mono-pcm → WAV container at 8kHz, perfect for telephony
                "X-Microsoft-OutputFormat": "riff-8khz-16bit-mono-pcm",
                "User-Agent": "STLVoiceBot/1.0",
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.url,
                    headers=headers,
                    content=ssml.encode("utf-8"),
                    timeout=10.0,
                )

            if response.status_code == 200:
                wav_bytes = response.content
                if format in ["wav"]:
                    return wav_bytes

                # Strip RIFF/WAV header → return raw PCM16 at 8kHz
                if wav_bytes.startswith(b"RIFF") and len(wav_bytes) > 44:
                    data_idx = wav_bytes.find(b"data")
                    if data_idx != -1:
                        pcm = wav_bytes[data_idx + 8:]
                    else:
                        pcm = wav_bytes[44:]
                else:
                    pcm = wav_bytes

                logger.debug(f"[AzureTTS] Synthesized {len(pcm)} bytes PCM @8kHz | voice={voice}")
                return pcm
            else:
                logger.warning(f"[AzureTTS] HTTP {response.status_code}: {response.text[:200]}")
                return b""

        except Exception as e:
            logger.error(f"[AzureTTS] Error synthesizing: {e}")
            return b""


class TTSEngineFactory:
    """Factory to instantiate BaseTTSService based on centralized configuration."""

    @staticmethod
    def get_engine(config: Optional[AppConfig] = None) -> BaseTTSService:
        cfg = config or get_config()
        provider = cfg.tts.provider

        if provider == "azure" and cfg.tts.azure_speech_key:
            azure = AzureTTS(settings=cfg.tts)
            # Fallback to Sarvam if key available (keeps Indian accent on Azure failure)
            if cfg.tts.sarvam_api_key:
                return FallbackTTS(primary=azure, fallback=SarvamTTS(settings=cfg.tts))
            return azure
        elif provider == "murf" and cfg.tts.murf_api_key:
            murf = MurfTTS(settings=cfg.tts)
            if cfg.tts.cartesia_api_key:
                return FallbackTTS(primary=murf, fallback=CartesiaTTS(settings=cfg.tts))
            elif cfg.tts.sarvam_api_key:
                return FallbackTTS(primary=murf, fallback=SarvamTTS(settings=cfg.tts))
            return murf
        elif provider == "cartesia" or (cfg.tts.cartesia_api_key and provider not in ["sarvam", "gnani", "murf"]):
            deepgram = DeepgramTTS(settings=cfg.tts) if cfg.tts.deepgram_api_key else None
            cartesia = CartesiaTTS(settings=cfg.tts)
            fallback = cartesia
            if cfg.tts.sarvam_api_key:
                fallback = FallbackTTS(primary=cartesia, fallback=SarvamTTS(settings=cfg.tts))
            if deepgram:
                return FallbackTTS(primary=deepgram, fallback=fallback)
            return fallback
        elif provider == "gnani" and cfg.tts.gnani_api_key:
            return GnaniTTS(settings=cfg.tts)
        elif provider == "sarvam" and cfg.tts.sarvam_api_key:
            return SarvamTTS(settings=cfg.tts)
        elif provider == "deepgram" and cfg.tts.deepgram_api_key:
            return DeepgramTTS(settings=cfg.tts)
        elif provider == "openai" or os.getenv("OPENAI_API_KEY"):
            return OpenAITTS(settings=cfg.tts)
        elif cfg.tts.murf_api_key:
            return MurfTTS(settings=cfg.tts)
        elif cfg.tts.cartesia_api_key:
            cartesia = CartesiaTTS(settings=cfg.tts)
            if cfg.tts.sarvam_api_key:
                return FallbackTTS(primary=cartesia, fallback=SarvamTTS(settings=cfg.tts))
            return cartesia
        elif cfg.tts.sarvam_api_key:
            return SarvamTTS(settings=cfg.tts)
        elif cfg.tts.gnani_api_key:
            return GnaniTTS(settings=cfg.tts)
        else:
            return EdgeTTS()

