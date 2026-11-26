"""What the workflow builder can offer: LLM / STT / TTS providers, models, voices, languages, tools.

Availability is derived from the keys that are actually configured, so the UI can never offer
something that cannot run. Voice lists come from the same maps the TTS engines use.
"""
import os
from typing import Any, Dict, List

from .sanjeevani_tools import agent_tool_options
from .ai_services import DEEPGRAM_VOICE_MAP, MURF_VOICE_MAP, OPENAI_VOICE_MAP, SARVAM_SPEAKERS
from .config import AppConfig

_GENDER = {"aarohi": "female", "radhika": "female", "hope": "female", "shweta": "female", "nalini": "female",
           "anisha": "female", "isha": "female", "khyati": "female", "hazel": "female",
           "deepak": "male", "gaurav": "male", "hemraj": "male", "samar": "male", "nikhil": "male"}

GNANI_VOICES = [
    ("Aarohi", "Female, en/hi/mr-IN"), ("Radhika", "Female, en/hi-IN"), ("Shweta", "Female, en/hi-IN"),
    ("Nalini", "Female, ta/te-IN"), ("Deepak", "Male, en/hi-IN"), ("Gaurav", "Male, en/hi-IN"),
    ("Hemraj", "Male, hi/mr-IN"),
]

LANGUAGES = [
    ("en-IN", "English (India)"), ("hi-IN", "Hindi"), ("mr-IN", "Marathi"), ("ta-IN", "Tamil"),
    ("te-IN", "Telugu"), ("kn-IN", "Kannada"), ("ml-IN", "Malayalam"), ("bn-IN", "Bengali"),
    ("gu-IN", "Gujarati"), ("en-US", "English (US)"),
    # Gulf/UAE (Sanjeevani). The primary Sanjeevani call path runs on Gemini 3.8 Live, which handles
    # Arabic natively speech-to-speech (one model, no per-language STT/TTS). These catalog entries
    # are only for the Groq STT->LLM->TTS pipeline fallback and the workflow builder's language dropdown.
    ("ar-AE", "Arabic (Gulf)"), ("en-AE", "English (UAE)"), ("en-GB", "English (UK)"),
]

TOOLS = [
    {"id": "transfer_to_human", "label": "Transfer to a human agent", "fields": ["transfer_number"],
     "help": "Hands the caller to a person. Optionally set the number to dial."},
    {"id": "create_symphony_ticket", "label": "Create an incident ticket", "fields": [],
     "help": "Logs a ticket from the conversation and reads the ticket number back."},
]


def _persona(name: str) -> Dict[str, str]:
    g = _GENDER.get(name.lower())
    return {"id": name.title(), "label": f"{name.title()} ({g})" if g else name.title()}


def _key_present(cfg: AppConfig, kind: str, provider: str) -> bool:
    if kind == "llm":
        return bool({"groq": cfg.llm.groq_api_key, "openai": cfg.llm.openai_api_key,
                     "gemini": cfg.llm.gemini_api_key, "custom": cfg.llm.custom_base_url}.get(provider))
    if kind == "stt":
        return bool({"cartesia": cfg.stt.cartesia_api_key, "deepgram": cfg.stt.deepgram_api_key,
                     "gnani": cfg.stt.gnani_api_key, "sarvam": cfg.stt.sarvam_api_key,
                     "groq": cfg.llm.groq_api_key}.get(provider))
    if kind == "tts":
        return bool({"deepgram": cfg.tts.deepgram_api_key, "cartesia": cfg.tts.cartesia_api_key,
                     "sarvam": cfg.tts.sarvam_api_key, "gnani": cfg.tts.gnani_api_key,
                     "murf": cfg.tts.murf_api_key, "azure": cfg.tts.azure_speech_key,
                     "openai": cfg.llm.openai_api_key or os.getenv("OPENAI_API_KEY", ""),
                     "edge": "free"}.get(provider))
    return False


def provider_available(cfg: AppConfig, kind: str, provider: str) -> bool:
    return _key_present(cfg, kind, (provider or "").lower())


def _uniq(items: List[str]) -> List[str]:
    return list(dict.fromkeys(i for i in items if i))


def llm_models(cfg: AppConfig) -> Dict[str, List[str]]:
    """Models offered per LLM provider; the first one is the default when a provider is picked."""
    return {
        "groq": _uniq([cfg.llm.groq_model, "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]),
        # gpt-4o is the model the production OpenAI project can use; the env default (gpt-4o-mini) is not enabled there.
        "openai": _uniq(["gpt-4o", "gpt-4o-mini", cfg.llm.openai_model]),
        "gemini": _uniq([cfg.llm.gemini_model, "gemini-3.8-flash"]),
    }


def default_model(cfg: AppConfig, provider: str) -> str:
    models = llm_models(cfg).get((provider or "").lower()) or [""]
    return models[0]


def build_options(cfg: AppConfig) -> Dict[str, Any]:
    models = llm_models(cfg)
    llm = [
        {"id": "groq", "label": "Groq (fast)", "models": models["groq"]},
        {"id": "openai", "label": "OpenAI", "models": models["openai"]},
        {"id": "gemini", "label": "Google Gemini", "models": models["gemini"]},
    ]
    for p in llm:
        p["available"] = provider_available(cfg, "llm", p["id"])
        p["default_model"] = p["models"][0]

    stt = [("cartesia", "Cartesia"), ("deepgram", "Deepgram"), ("gnani", "Gnani"), ("sarvam", "Sarvam"),
           ("groq", "Whisper (Groq)")]
    stt_out = [{"id": i, "label": l, "available": provider_available(cfg, "stt", i)} for i, l in stt]

    tts_voices = {
        "deepgram": [_persona(n) for n in DEEPGRAM_VOICE_MAP],
        "openai": [_persona(n) for n in OPENAI_VOICE_MAP],
        "murf": [_persona(n) for n in MURF_VOICE_MAP],
        "sarvam": [{"id": n, "label": n.title()} for n in sorted(SARVAM_SPEAKERS)],
        "gnani": [{"id": n, "label": f"{n} ({d})"} for n, d in GNANI_VOICES],
        "azure": [{"id": v, "label": v} for v in _uniq([cfg.tts.azure_tts_voice, "en-IN-NeerjaNeural", "en-IN-PrabhatNeural",
                                                       "hi-IN-SwaraNeural", "hi-IN-MadhurNeural"])],
        "cartesia": [{"id": "", "label": "Default voice"}],
        "edge": [{"id": "", "label": "Default voice"}],
    }
    tts = [("deepgram", "Deepgram Aura (lowest latency)"), ("cartesia", "Cartesia"), ("sarvam", "Sarvam (Indian languages)"),
           ("gnani", "Gnani"), ("murf", "Murf"), ("azure", "Azure Speech"), ("openai", "OpenAI"), ("edge", "Edge (free)")]
    tts_out = [{"id": i, "label": l, "available": provider_available(cfg, "tts", i), "voices": tts_voices[i]}
               for i, l in tts]

    return {
        "llm": llm,
        "stt": stt_out,
        "tts": tts_out,
        "languages": [{"id": i, "label": l} for i, l in LANGUAGES],
        "tools": TOOLS,
        "agent_tools": agent_tool_options(),     # tools an agent step can be given by name (patient care)
        "defaults": {
            "llm": {"provider": cfg.llm.provider},
            "stt": {"provider": cfg.stt.provider, "language": cfg.stt.language_code},
            "tts": {"provider": cfg.tts.provider},
        },
    }
