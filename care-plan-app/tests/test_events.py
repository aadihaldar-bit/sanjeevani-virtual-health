"""The call-ended webhook and the events feed: a secret in the URL, a small event, and nothing the patient said."""
import pytest
from starlette.requests import Request

import access_gate
import events
import store
import voicebot
from conftest import PLAN

SUMMARY = {"call_id": 582, "summary": "She said she has a rash and chest tightness", "transcript": [{"from": "patient", "text": "my rash"}],
           "telephony": {"provider": "twilio"}, "telephony_stats": {"duration_seconds": 119}, "appointment": {"doctor": "Dr X"}}


@pytest.fixture
def app_client(client, monkeypatch):
    import fastapi
    from fastapi.testclient import TestClient
    app = fastapi.FastAPI()
    app.include_router(events.router)
    monkeypatch.setenv("CALLBACK_SECRET", "s3cret")
    return TestClient(app)


def test_the_webhook_is_off_without_a_secret(app_client, monkeypatch):
    monkeypatch.delenv("CALLBACK_SECRET")
    assert app_client.post("/api/v1/voice/callback/anything", json=SUMMARY).status_code == 404


def test_a_wrong_secret_looks_like_a_missing_page(app_client):
    r = app_client.post("/api/v1/voice/callback/nope", json=SUMMARY)
    assert r.status_code == 404 and store.last_event_id() == 0


def test_a_call_summary_becomes_a_small_event(app_client):
    assert app_client.post("/api/v1/voice/callback/s3cret", json=SUMMARY).json() == {"ok": True}
    (e,) = store.list_events(0)
    assert e["kind"] == "call_ended" and e["ref"] == "582"
    assert e["detail"] == {"has_appointment": True, "seconds": 119, "provider": "twilio"}


def test_nothing_the_patient_said_is_kept(app_client):
    app_client.post("/api/v1/voice/callback/s3cret", json=SUMMARY)
    everything = str(store.list_events(0)) + str(store.list_audit())
    assert "rash" not in everything and "chest" not in everything


def test_bad_bodies_are_refused(app_client):
    assert app_client.post("/api/v1/voice/callback/s3cret", content=b"not json", headers={"content-type": "application/json"}).status_code == 422
    assert app_client.post("/api/v1/voice/callback/s3cret", json=["a list"]).status_code == 422


def test_the_feed_starts_without_a_flood_and_then_returns_only_new_events(app_client):
    app_client.post("/api/v1/voice/callback/s3cret", json=SUMMARY)
    first = app_client.get("/api/v1/events?since=-1").json()
    assert first["events"] == [] and first["last"] == 1
    app_client.post("/api/v1/voice/callback/s3cret", json={**SUMMARY, "call_id": 583})
    new = app_client.get(f"/api/v1/events?since={first['last']}").json()
    assert [e["ref"] for e in new["events"]] == ["583"]


def test_old_events_are_pruned(monkeypatch):
    monkeypatch.setattr(store, "KEEP_EVENTS", 3)
    for i in range(6):
        store.put_event("call_ended", str(i), None)
    assert [e["ref"] for e in store.list_events(0)] == ["3", "4", "5"]


# ---------------------------------------------------------------- the access gate and the callback URL
def req(path):
    return Request({"type": "http", "path": path, "headers": [], "query_string": b""})


def test_the_voicebot_can_reach_the_webhook_through_the_password_gate(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", "pw")
    assert access_gate._allowed(req("/api/v1/voice/callback/s3cret"))
    assert not access_gate._allowed(req("/api/v1/events"))           # the feed is for signed-in staff only
    assert not access_gate._allowed(req("/api/v1/audit"))


def test_callback_url_needs_both_settings(monkeypatch):
    assert events.callback_url() is None
    monkeypatch.setenv("APP_PUBLIC_URL", "https://app.example/")
    assert events.callback_url() is None
    monkeypatch.setenv("CALLBACK_SECRET", "s3cret")
    assert events.callback_url() == "https://app.example/api/v1/voice/callback/s3cret"


class Capture:
    posted = None

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def post(self, url, json=None, headers=None):
        Capture.posted = json
        return type("R", (), {"status_code": 200, "json": lambda self: {"call_id": 9, "message": "ok"}})()


def test_calls_placed_by_the_app_ask_for_the_webhook(client, monkeypatch):
    store.put_plan("RX-1", PLAN)
    monkeypatch.setenv("VOICEBOT_URL", "https://vb.example")
    monkeypatch.setenv("VOICEBOT_API_KEY", "k")
    monkeypatch.setattr(voicebot.httpx, "AsyncClient", Capture)
    client.post("/api/v1/voice/call-me", json={"prescription_id": "RX-1", "phone_number": "+10000000000"})
    assert "callback_url" not in Capture.posted
    monkeypatch.setenv("APP_PUBLIC_URL", "https://app.example")
    monkeypatch.setenv("CALLBACK_SECRET", "s3cret")
    client.post("/api/v1/voice/call-me", json={"prescription_id": "RX-1", "phone_number": "+10000000000"})
    assert Capture.posted["callback_url"] == "https://app.example/api/v1/voice/callback/s3cret"
