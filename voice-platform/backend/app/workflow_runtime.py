"""Apply a workflow's saved settings (LLM / STT / TTS) on top of the environment configuration."""
import dataclasses
import logging
from typing import Any, Dict, List, Optional, Tuple

from .config import AppConfig, get_config
from .provider_catalog import build_options, default_model, provider_available
from .provider_health import known_unhealthy

logger = logging.getLogger(__name__)

_MODEL_FIELD = {"groq": "groq_model", "openai": "openai_model", "gemini": "gemini_model", "custom": "custom_model"}


def apply_settings(cfg: AppConfig, settings: Optional[Dict[str, Any]]) -> AppConfig:
    """Return cfg with the workflow's choices applied. A choice whose provider has no configured
    key is ignored (with a warning) so a stale setting can never take a call down."""
    if not settings:
        return cfg

    llm, stt, tts = cfg.llm, cfg.stt, cfg.tts

    spec = settings.get("llm") or {}
    provider = (spec.get("provider") or "").lower()
    if provider == "custom":
        endpoint = spec.get("_endpoint")
        if endpoint:
            llm = dataclasses.replace(
                llm, provider="custom", custom_base_url=endpoint["base_url"], custom_api_key=endpoint.get("api_key", ""),
                custom_model=endpoint["model"], custom_headers=tuple(sorted((endpoint.get("headers") or {}).items())))
        else:
            logger.warning(f"[Workflow] custom endpoint is not usable; using {cfg.llm.provider}")
    elif provider in _MODEL_FIELD:
        issue = known_unhealthy(provider, cfg)
        if not provider_available(cfg, "llm", provider):
            logger.warning(f"[Workflow] LLM provider '{provider}' has no API key; using {cfg.llm.provider}")
        elif issue and provider != cfg.llm.provider:
            logger.warning(f"[Workflow] LLM provider '{provider}' is not working ({issue}); using {cfg.llm.provider}")
        else:
            changes: Dict[str, Any] = {"provider": provider}
            model = str(spec.get("model") or "").strip()
            if not model and provider != cfg.llm.provider:
                model = default_model(cfg, provider)
            if model:
                changes[_MODEL_FIELD[provider]] = model
            llm = dataclasses.replace(llm, **changes)

    spec = settings.get("stt") or {}
    changes = {}
    provider = (spec.get("provider") or "").lower()
    if provider:
        if provider_available(cfg, "stt", provider):
            changes["provider"] = provider
        else:
            logger.warning(f"[Workflow] STT provider '{provider}' has no API key; using {cfg.stt.provider}")
    if spec.get("language"):
        changes["language_code"] = str(spec["language"])
    if changes:
        stt = dataclasses.replace(stt, **changes)

    spec = settings.get("tts") or {}
    provider = (spec.get("provider") or "").lower()
    if provider:
        if provider_available(cfg, "tts", provider):
            tts = dataclasses.replace(tts, provider=provider)
        else:
            logger.warning(f"[Workflow] TTS provider '{provider}' has no API key; using {cfg.tts.provider}")

    return dataclasses.replace(cfg, llm=llm, stt=stt, tts=tts)


async def load_workflow_settings(workflow_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    return (await load_workflow_context(workflow_id))[0]


async def load_workflow_context(workflow_id: Optional[int] = None) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """(saved settings, owning organization id) of the given workflow, or of the active one."""
    from sqlalchemy import select
    from .database import async_session
    from .models import Workflow

    async with async_session() as db:
        from .tenancy import system_tenant_id_cached
        if workflow_id:
            q = select(Workflow).where(Workflow.id == workflow_id)
        else:        # each tenant has its own active agent; a call with no assigned number uses the platform's (STL's)
            q = select(Workflow).where(Workflow.is_active.is_(True)).order_by(
                (Workflow.tenant_id == system_tenant_id_cached()).desc(), Workflow.id)
        wf = (await db.execute(q)).scalars().first()
        return ((wf.settings_json or None), wf.tenant_id) if wf else (None, None)


async def _resolve_custom_endpoint(settings: Optional[Dict[str, Any]], tenant_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """If the workflow chose provider "custom:<id>", return settings with that endpoint resolved (decrypted) inside."""
    spec = (settings or {}).get("llm") or {}
    provider = str(spec.get("provider") or "")
    if not provider.startswith("custom:"):
        return settings
    from .database import async_session
    from .external_models import resolve_endpoint
    try:
        endpoint_id = int(provider.split(":", 1)[1])
    except ValueError:
        endpoint_id = -1
    async with async_session() as db:
        endpoint = await resolve_endpoint(db, endpoint_id, tenant_id)
    resolved = dict(settings)
    resolved["llm"] = {**spec, "provider": "custom", "model": (endpoint or {}).get("model", ""), "_endpoint": endpoint}
    return resolved


async def effective_config(workflow_id: Optional[int] = None, base: Optional[AppConfig] = None) -> AppConfig:
    """Environment config + the (active or given) workflow's settings. Never raises."""
    base = base or get_config()
    try:
        from .provider_health import refresh_in_background
        refresh_in_background(base)
        settings, tenant_id = await load_workflow_context(workflow_id)
        if tenant_id is not None:                      # an organization's own provider keys replace the platform's for its calls
            from .database import async_session
            from .external_models import apply_org_credentials, load_org_credentials
            async with async_session() as db:
                base = apply_org_credentials(base, await load_org_credentials(db, tenant_id))
        return apply_settings(base, await _resolve_custom_endpoint(settings, tenant_id))
    except Exception as exc:
        logger.warning(f"[Workflow] could not load workflow settings, using defaults: {exc}")
        return base


_SETTING_FIELDS = {"llm": ("provider", "model"), "stt": ("provider", "language"), "tts": ("provider",)}


def sanitize_settings(cfg: AppConfig, settings: Optional[Dict[str, Any]],
                      extra_llm: Optional[List[Dict[str, Any]]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Keep only known fields with short string values; drop unknown providers; warn on unusable ones."""
    clean: Dict[str, Any] = {}
    warnings: List[str] = []
    if not isinstance(settings, dict):
        return clean, warnings
    opts = build_options(cfg)
    label = {"llm": "language model", "stt": "speech-to-text", "tts": "text-to-speech"}
    for kind, fields in _SETTING_FIELDS.items():
        spec = settings.get(kind)
        if not isinstance(spec, dict):
            continue
        out = {f: str(spec[f]).strip()[:100] for f in fields if isinstance(spec.get(f), (str, int, float)) and str(spec[f]).strip()}
        provider = out.get("provider", "").lower()
        if provider:
            known = {p["id"]: p for p in opts[kind]}
            if kind == "llm":
                known.update({p["id"]: p for p in (extra_llm or [])})      # custom endpoints this organization may use
            if provider not in known:
                warnings.append(f"'{out['provider']}' is not a supported {label[kind]} provider and was ignored.")
                out.pop("provider")
            else:
                out["provider"] = provider
                if not known[provider]["available"]:
                    warnings.append(f"{known[provider]['label']} is not configured on this server, so calls will use "
                                    f"{opts['defaults'][kind]['provider']} instead.")
                elif kind == "llm" and known_unhealthy(provider, cfg):
                    warnings.append(f"{known[provider]['label']} is not working right now ({known_unhealthy(provider, cfg)}), "
                                    f"so calls will use {opts['defaults'][kind]['provider']} instead.")
        if out:
            clean[kind] = out
    # Gemini Live choices: only known words, so a made-up value can never reach the model's setup message.
    gl = settings.get("gemini_live")
    if isinstance(gl, dict):
        from .gemini_live import EXTENDED_MODEL, THINKING_LEVELS, TURN_END, TURN_START, clean_languages
        keep: Dict[str, Any] = {}
        if gl.get("model") == EXTENDED_MODEL:
            keep["model"] = EXTENDED_MODEL
            if gl.get("thinking_level") in THINKING_LEVELS:
                keep["thinking_level"] = gl["thinking_level"]
        if gl.get("turn_end") in TURN_END:
            keep["turn_end"] = gl["turn_end"]
        if gl.get("turn_start") in TURN_START:
            keep["turn_start"] = gl["turn_start"]
        if clean_languages(gl.get("transcript_languages")):
            keep["transcript_languages"] = clean_languages(gl["transcript_languages"])
        if keep:
            clean["gemini_live"] = keep
    return clean, warnings


def node_setting_warnings(cfg: AppConfig, nodes: List[Any], settings: Optional[Dict[str, Any]]) -> List[str]:
    """Per-agent warnings: LLM provider unavailable, or voice not offered by the TTS provider."""
    opts = build_options(cfg)
    llm_by_id = {p["id"]: p for p in opts["llm"]}
    tts_provider = ((settings or {}).get("tts") or {}).get("provider") or cfg.tts.provider
    tts = next((p for p in opts["tts"] if p["id"] == tts_provider), None)
    voice_ids = {v["id"].lower() for v in (tts["voices"] if tts else []) if v["id"]}
    out: List[str] = []
    for n in nodes:
        if n.type != "agentNode":
            continue
        data = n.data or {}
        name = str(data.get("name") or n.id)
        p = str(data.get("llm") or "").lower()
        if p and p in llm_by_id and not llm_by_id[p]["available"]:
            out.append(f"Agent '{name}' uses {llm_by_id[p]['label']}, which is not configured, so it will use the default model.")
        elif p and p in llm_by_id and known_unhealthy(p, cfg):
            out.append(f"Agent '{name}' uses {llm_by_id[p]['label']}, which is not working right now "
                       f"({known_unhealthy(p, cfg)}), so it will use the default model.")
        voice = str(data.get("voice") or "").strip()
        if voice and voice_ids and voice.lower() not in voice_ids:
            out.append(f"Agent '{name}' uses voice '{voice}', which {tts['label'].split(' (')[0]} does not offer; "
                       f"it will fall back to that provider's default voice.")
    return out
