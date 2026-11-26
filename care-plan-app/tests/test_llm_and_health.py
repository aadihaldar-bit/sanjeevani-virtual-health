"""The Gemini wrapper retries a busy model once and then moves on; the voicebot health probe reports each failure mode."""
import pytest

import llm
import voicebot


class Boom(Exception):
    pass


def fake_client(script):
    """A stand-in for google.genai.Client: `script` is a list of results or exceptions, one per call."""
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(model)
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return type("R", (), {"text": item})()

    class Client:
        def __init__(self, *a, **k):
            self.models = Models()

    return Client, calls


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(llm, "load_dotenv", lambda **k: None)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


def test_a_busy_model_is_retried_once(monkeypatch):
    Client, calls = fake_client([Boom("503 UNAVAILABLE"), "hello"])
    monkeypatch.setattr(llm.genai, "Client", Client)
    assert llm.generate("hi", ["m1", "m2"]) == "hello"
    assert calls == ["m1", "m1"]


def test_a_timeout_goes_straight_to_the_next_model(monkeypatch):
    Client, calls = fake_client([Boom("504 DEADLINE_EXCEEDED"), "hello"])
    monkeypatch.setattr(llm.genai, "Client", Client)
    assert llm.generate("hi", ["m1", "m2"]) == "hello"
    assert calls == ["m1", "m2"]


def test_all_models_failing_raises_a_clear_error(monkeypatch):
    Client, _ = fake_client([Boom("500 boom"), Boom("500 boom")])
    monkeypatch.setattr(llm.genai, "Client", Client)
    with pytest.raises(llm.LLMUnavailable):
        llm.generate("hi", ["m1", "m2"])


def test_no_key_means_no_call(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(llm.LLMUnavailable):
        llm.generate("hi", ["m1"])


# ---------------------------------------------------------------- health
class Resp:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body or {}

    def json(self):
        return self._b


def health_client(health, patients):
    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            if isinstance(health, Exception) and url.endswith("/health"):
                raise health
            return health if url.endswith("/health") else patients
    return C


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("VOICEBOT_URL", "https://vb.example")
    monkeypatch.setenv("VOICEBOT_API_KEY", "k")
    voicebot._health_cache.update(at=0.0, body=None)


def test_health_not_configured(client):
    assert client.get("/api/v1/voice/health").json()["state"] == "not_configured"


@pytest.mark.parametrize("health,patients,state", [
    (Resp(200, {"database": "connected"}), Resp(200), "ok"),
    (Boom("down"), Resp(200), "down"),
    (Resp(200, {"database": "connected"}), Resp(401), "degraded"),
    (Resp(200, {"database": "locked"}), Resp(200), "degraded"),
])
def test_health_states(client, configured, monkeypatch, health, patients, state):
    monkeypatch.setattr(voicebot.httpx, "AsyncClient", health_client(health, patients))
    assert client.get("/api/v1/voice/health").json()["state"] == state
