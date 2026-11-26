"""An agent's own Gemini Live choices become the session's setup message: the model, the thinking level the extended model
insists on, and how patiently the caller's turn is judged."""
import pytest

from org_env import env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, world  # noqa: F401
from app.gemini_live import EXTENDED_MODEL, GeminiLiveSession, live_settings
from app.media_bridge import PipecatMediaSession
from types import SimpleNamespace

pytestmark = pytest.mark.asyncio


def _setup(**kw):
    return GeminiLiveSession(key="not-used", system_instruction="Hi", modalities=["AUDIO"], voice="Aoede", **kw).setup_message()


def test_with_no_choices_the_server_defaults_apply(monkeypatch):
    monkeypatch.delenv("GEMINI_LIVE_START_SENS", raising=False)
    monkeypatch.delenv("GEMINI_LIVE_END_SENS", raising=False)
    detection = _setup()["realtimeInputConfig"]["automaticActivityDetection"]
    assert detection == {"startOfSpeechSensitivity": "START_SENSITIVITY_LOW", "endOfSpeechSensitivity": "END_SENSITIVITY_HIGH"}
    assert "thinkingConfig" not in _setup()["generationConfig"]


def test_a_patient_agent_waits_longer_before_deciding_the_caller_has_finished():
    chosen = live_settings({"turn_end": "patient"})
    detection = _setup(vad=chosen["vad"])["realtimeInputConfig"]["automaticActivityDetection"]
    assert detection["endOfSpeechSensitivity"] == "END_SENSITIVITY_LOW" and detection["silenceDurationMs"] == 1000
    assert detection["startOfSpeechSensitivity"] == "START_SENSITIVITY_LOW"          # untouched: still the server default


def test_a_sensitive_start_keeps_a_quiet_first_word():
    detection = _setup(vad=live_settings({"turn_start": "sensitive"})["vad"])["realtimeInputConfig"]["automaticActivityDetection"]
    assert detection["startOfSpeechSensitivity"] == "START_SENSITIVITY_HIGH" and detection["prefixPaddingMs"] == 500


def test_the_extended_model_always_gets_a_thinking_level_it_accepts():
    chosen = live_settings({"model": EXTENDED_MODEL})
    assert chosen["model"] == EXTENDED_MODEL and chosen["thinking_level"] == "LOW"
    assert _setup(model=chosen["model"], thinking_level=chosen["thinking_level"])["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert live_settings({"model": EXTENDED_MODEL, "thinking_level": "HIGH"})["thinking_level"] == "HIGH"
    assert live_settings({"model": EXTENDED_MODEL, "thinking_level": "MINIMAL"})["thinking_level"] == "LOW"     # not accepted by the model


def test_the_standard_model_never_gets_a_thinking_level():
    assert live_settings({"model": "models/gemini-3.8-live", "thinking_level": "HIGH"})["thinking_level"] is None


@pytest.mark.parametrize("junk", [None, "x", 5, [], {"turn_end": "warp", "turn_start": 3, "model": "gpt-4", "thinking_level": "MAX"}])
def test_nonsense_settings_change_nothing(junk):
    assert live_settings(junk) == {"model": None, "vad": {}, "thinking_level": None, "transcript_languages": []}


def test_the_bridge_passes_an_agents_choices_to_the_session():
    session = SimpleNamespace(_gemini_overrides={"turn_end": "patient", "model": EXTENDED_MODEL})
    out = PipecatMediaSession._gemini_session_choices(session)
    assert out["model"] == EXTENDED_MODEL and out["thinking_level"] == "LOW" and out["vad"]["end_sensitivity"] == "END_SENSITIVITY_LOW"
    assert PipecatMediaSession._gemini_session_choices(SimpleNamespace(_gemini_overrides={})) == {}
    assert PipecatMediaSession._gemini_session_choices(SimpleNamespace()) == {}


async def test_the_builder_offers_both_models_and_the_levels(world, monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_LIVE_API_KEY", "test-key")
    live = (await world.get("/api/workflows/options", user=ACME_BOSS)).json()["gemini_live"]
    assert [m["id"] for m in live["models"]] == ["", EXTENDED_MODEL] and live["thinking_levels"] == ["LOW", "MEDIUM", "HIGH"]


async def test_an_agents_gemini_live_choices_are_saved_with_it(world):
    payload = {"name": "Choices", "is_active": False, "settings": {"gemini_live": {"turn_end": "patient"}},
               "nodes": [{"id": "s", "type": "startNode", "position": {"x": 0, "y": 0}, "data": {"name": "Start"}},
                         {"id": "a", "type": "agentNode", "position": {"x": 300, "y": 0}, "data": {"name": "Helper", "system_prompt": "You help callers.", "voice": "", "use_rag": False}},
                         {"id": "e", "type": "endNode", "position": {"x": 600, "y": 0}, "data": {}}],
               "edges": [{"id": "e1", "source": "s", "target": "a", "label": "start", "condition": ""},
                         {"id": "e2", "source": "a", "target": "e", "label": "done", "condition": ""}]}
    res = await world.post("/api/workflows/", user=ACME_BOSS, json=payload)
    assert res.status_code == 200, res.text
    saved = res.json()
    read = (await world.get(f"/api/workflows/{saved['id']}", user=ACME_BOSS)).json()
    assert read["settings"]["gemini_live"] == {"turn_end": "patient"}


async def test_made_up_gemini_live_values_are_not_saved(world):
    payload = {"name": "Junk", "is_active": False,
               "settings": {"gemini_live": {"model": "models/evil", "turn_end": "warp", "turn_start": "steady", "thinking_level": "HIGH"}},
               "nodes": [{"id": "a", "type": "agentNode", "position": {"x": 0, "y": 0}, "data": {"name": "H", "system_prompt": "x", "voice": ""}}], "edges": []}
    saved = (await world.post("/api/workflows/", user=ACME_BOSS, json=payload)).json()
    read = (await world.get(f"/api/workflows/{saved['id']}", user=ACME_BOSS)).json()
    assert read["settings"]["gemini_live"] == {"turn_start": "steady"}


def test_a_language_hint_for_the_callers_transcript_is_sent_and_made_up_ones_are_not():
    from app.gemini_live import GeminiLiveSession, clean_languages
    chosen = live_settings({"transcript_languages": ["en-US", "ar-XA", "not a code", 7, "en_US"]})
    assert chosen["transcript_languages"] == ["en-US", "ar-XA"]
    sent = GeminiLiveSession(key="k", transcript_languages=chosen["transcript_languages"]).setup_message()
    assert sent["inputAudioTranscription"] == {"languageCodes": ["en-US", "ar-XA"]}
    assert GeminiLiveSession(key="k").setup_message()["inputAudioTranscription"] == {}          # no hint: as before
    assert clean_languages("en-US") == [] and len(clean_languages(["en-US"] * 9)) == 4
