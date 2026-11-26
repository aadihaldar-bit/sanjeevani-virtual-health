"""
Centralized Configuration System
Provides typed, validated configuration settings for all subsystems.
Adheres to Single Responsibility Principle and Dependency Inversion Principle.
"""

import os
from typing import Optional
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load environment variables once
load_dotenv()


@dataclass(frozen=True)
class TelephonySettings:
    account_sid: str = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", "").strip())
    auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", "").strip(), repr=False)
    phone_number: str = field(default_factory=lambda: os.getenv("TWILIO_PHONE_NUMBER", "").strip())
    server_url: str = field(default_factory=lambda: os.getenv("SERVER_URL", "https://localhost:8055").strip())
    supervisor_transfer_number: str = field(default_factory=lambda: os.getenv("HUMAN_AGENT_NUMBER", "+919764367506").strip())


@dataclass(frozen=True)
class LLMSettings:
    provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "groq").lower().strip())
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", "").strip(), repr=False)
    groq_model: str = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip(), repr=False)
    openai_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini").strip())
    openai_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip())
    
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", "").strip(), repr=False)
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip())

    # What Architect thinks with (its chat, and drawing an agent from a description). Nobody is on a phone line waiting, so these
    # are the strongest thinking models, tried in order. "gemini-pro-latest" follows Google's newest Pro; pin a name to stop that.
    architect_models: tuple = field(default_factory=lambda: tuple(
        m.strip() for m in os.getenv("ARCHITECT_MODELS", "gemini-pro-latest,gemini-3.8-flash").split(",") if m.strip()))
    architect_thinking: str = field(default_factory=lambda: os.getenv("ARCHITECT_THINKING", "HIGH").upper().strip() or "HIGH")
    # nginx in front of the API gives up on a request after 60 seconds, and a chat turn or the drawing of an agent is one request:
    # so one model gets this long, and every model together get the total. Raise both only together with nginx's proxy_read_timeout.
    architect_attempt_secs: float = field(default_factory=lambda: float(os.getenv("ARCHITECT_ATTEMPT_SECS", "30") or 30))
    architect_total_secs: float = field(default_factory=lambda: float(os.getenv("ARCHITECT_TOTAL_SECS", "50") or 50))

    # A custom OpenAI-compatible endpoint chosen by a workflow (filled in per call from the database, never from env)
    custom_base_url: str = ""
    custom_api_key: str = field(default="", repr=False)
    custom_model: str = ""
    custom_headers: tuple = field(default=(), repr=False)       # ((name, value), ...) keeps the settings hashable
    
    temperature: float = 0.2
    max_tokens: int = 600
    # Groq gpt-oss reasoning models: cap hidden reasoning to cut latency ("low"|"medium"|"high"|"" to disable)
    reasoning_effort: str = field(default_factory=lambda: os.getenv("GROQ_REASONING_EFFORT", "low").strip())


@dataclass(frozen=True)
class STTSettings:
    provider: str = field(default_factory=lambda: os.getenv("STT_PROVIDER", "cartesia").lower().strip())
    cartesia_api_key: str = field(default_factory=lambda: os.getenv("CARTESIA_API_KEY", "").strip(), repr=False)
    gnani_api_key: str = field(default_factory=lambda: os.getenv("GNANI_API_KEY", "").strip(), repr=False)
    sarvam_api_key: str = field(default_factory=lambda: os.getenv("SARVAM_API_KEY", "").strip(), repr=False)
    deepgram_api_key: str = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY", "").strip(), repr=False)
    # Deepgram model: nova-2 (default) or nova-3 (more accurate, incl. Indian English). Env-tunable for safe rollout.
    deepgram_model: str = field(default_factory=lambda: os.getenv("DEEPGRAM_MODEL", "nova-2").strip())
    language_code: str = field(default_factory=lambda: os.getenv("VOICE_LANGUAGE_CODE", "en-IN").strip())
    translate_to_english: bool = field(default_factory=lambda: os.getenv("STT_TRANSLATE_TO_ENGLISH", "true").lower() == "true")
    sample_rate: int = 16000


@dataclass(frozen=True)
class TTSSettings:
    provider: str = field(default_factory=lambda: os.getenv("TTS_PROVIDER", "cartesia").lower().strip())
    cartesia_api_key: str = field(default_factory=lambda: os.getenv("CARTESIA_API_KEY", "").strip(), repr=False)
    cartesia_model_id: str = "sonic-3.5"
    cartesia_voice_id: str = "79a125e8-cd45-4c13-8a67-188112f4dd22"
    
    gnani_api_key: str = field(default_factory=lambda: os.getenv("GNANI_API_KEY", "").strip(), repr=False)
    gnani_voice: str = field(default_factory=lambda: os.getenv("GNANI_VOICE", "Aarohi").strip())
    gnani_model_id: str = field(default_factory=lambda: os.getenv("GNANI_MODEL_ID", "timbre-v2.5").strip())
    
    sarvam_api_key: str = field(default_factory=lambda: os.getenv("SARVAM_API_KEY", "").strip(), repr=False)
    sarvam_model_id: str = "bulbul:v3"
    sarvam_speaker: str = "ritu"
    
    deepgram_api_key: str = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY", "").strip(), repr=False)
    
    murf_api_key: str = field(default_factory=lambda: os.getenv("MURF_API_KEY", "").strip(), repr=False)
    murf_voice_id: str = field(default_factory=lambda: os.getenv("MURF_VOICE_ID", "en-IN-arohi").strip())
    murf_model_id: str = field(default_factory=lambda: os.getenv("MURF_MODEL_ID", "falcon-2").strip())

    # Azure Cognitive Services Speech
    azure_speech_key: str = field(default_factory=lambda: os.getenv("AZURE_SPEECH_KEY", "").strip(), repr=False)
    azure_speech_region: str = field(default_factory=lambda: os.getenv("AZURE_SPEECH_REGION", "eastus").strip())
    azure_tts_voice: str = field(default_factory=lambda: os.getenv("AZURE_TTS_VOICE", "en-IN-NeerjaNeural").strip())

    sample_rate: int = 8000
    format: str = "pcm"



@dataclass(frozen=True)
class VADSettings:
    energy_threshold: int = 750
    speech_frames_for_barge_in: int = 3
    silence_frames_for_endpointing: int = 60  # ~1.2s of silence (60 * 20ms)
    # Silero endpointing (live telephony). stop_secs is the silence-to-end-of-turn wait.
    # Lower = snappier turns, but too low clips slow speakers. 0.35 balances latency vs. cutoffs.
    stop_secs: float = field(default_factory=lambda: float(os.getenv("VAD_STOP_SECS", "0.35")))
    start_secs: float = field(default_factory=lambda: float(os.getenv("VAD_START_SECS", "0.15")))
    confidence: float = field(default_factory=lambda: float(os.getenv("VAD_CONFIDENCE", "0.5")))
    min_volume: float = field(default_factory=lambda: float(os.getenv("VAD_MIN_VOLUME", "0.08")))


@dataclass(frozen=True)
class VectorDBSettings:
    provider: str = field(default_factory=lambda: os.getenv("VECTOR_DB_PROVIDER", "qdrant").lower().strip())
    qdrant_url: str = field(default_factory=lambda: os.getenv("QDRANT_URL", "").strip())
    qdrant_api_key: str = field(default_factory=lambda: os.getenv("QDRANT_API_KEY", "").strip(), repr=False)
    collection_name: str = field(default_factory=lambda: os.getenv("QDRANT_COLLECTION", "voice_agent_knowledge").strip())
    embedding_dimension: int = field(default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "768")))


@dataclass(frozen=True)
class LangfuseSettings:
    enabled: bool = field(default_factory=lambda: os.getenv("LANGFUSE_ENABLED", "true").lower() == "true")
    public_key: str = field(default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(), repr=False)
    secret_key: str = field(default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY", "").strip(), repr=False)
    host: str = field(default_factory=lambda: os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "https://internalagent.stldigitaltech.com")).strip())
    release: str = field(default_factory=lambda: os.getenv("LANGFUSE_RELEASE", "v1.0.0").strip())


@dataclass(frozen=True)
class JevSettings:
    """TypeSafe AI Jev (System 1 Decision Engine) settings."""
    typesafe_api_key: str = field(default_factory=lambda: os.getenv("TYPESAFE_API_KEY", "").strip(), repr=False)
    jev_model: str = field(default_factory=lambda: os.getenv("JEV_MODEL", "jev-1.13.0").strip())
    enabled: bool = field(default_factory=lambda: bool(os.getenv("TYPESAFE_API_KEY", "").strip()))


@dataclass(frozen=True)
class InfrastructureSettings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./voice_agent.db").strip())
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0").strip())
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0").strip())
    port: int = field(default_factory=lambda: int(os.getenv("BACKEND_PORT", os.getenv("PORT", "8055"))))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper().strip())
    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "False").lower() == "true")
    # Blocking per-turn WAV write to disk/ (off by default — keep the audio path off the event loop)
    save_recordings: bool = field(default_factory=lambda: os.getenv("SAVE_TURN_RECORDINGS", "false").lower() == "true")


@dataclass(frozen=True)
class AppConfig:
    telephony: TelephonySettings = field(default_factory=TelephonySettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    stt: STTSettings = field(default_factory=STTSettings)
    tts: TTSSettings = field(default_factory=TTSSettings)
    vad: VADSettings = field(default_factory=VADSettings)
    vector_db: VectorDBSettings = field(default_factory=VectorDBSettings)
    infra: InfrastructureSettings = field(default_factory=InfrastructureSettings)
    langfuse: LangfuseSettings = field(default_factory=LangfuseSettings)
    jev: JevSettings = field(default_factory=JevSettings)


# Global singleton
_config_instance: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Retrieve the global AppConfig instance."""
    global _config_instance
    if _config_instance is None:
        _config_instance = AppConfig()
    return _config_instance


def reload_config() -> AppConfig:
    """Reload environment variables and refresh the global AppConfig instance."""
    global _config_instance
    load_dotenv(override=True)
    _config_instance = AppConfig()
    return _config_instance
