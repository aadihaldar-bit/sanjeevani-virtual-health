"""Alerts queue without dialling, calls are gated, and plans and alerts survive a restart."""
import time
from datetime import datetime, timedelta, timezone

import pytest

import alerts
import store
from conftest import PLAN


# ---------------------------------------------------------------- store
def test_a_plan_is_found_again_and_expires(monkeypatch):
    store.put_plan("RX-1", PLAN)
    assert store.get_plan("RX-1")["patient"]["patient_name"] == "Master Rayan"
    monkeypatch.setattr(store, "PLAN_TTL_S", -1)
    assert store.get_plan("RX-1") is None


def test_data_survives_a_restart():
    store.put_plan("RX-1", PLAN)
    store._ready.clear()                      # a new process knows nothing, only the file
    assert store.get_plan("RX-1") is not None


# ---------------------------------------------------------------- vitals rules
def post_vitals(client, **kw):
    body = {"patient_id": "SNJ-1", "phone": "+10000000000", "name": "Test", **kw}
    return client.post("/api/v1/alerts/vitals", json=body).json()["alert"]


def test_only_well_read_values_far_from_usual_raise_an_alert(client):
    assert post_vitals(client, hr=135, hr_confidence="approximate") is None
    assert post_vitals(client, hr=80, hr_confidence="good") is None
    assert post_vitals(client, hr=135, hr_confidence="good")["kind"] == "vitals"


def test_the_same_alert_is_queued_once_a_day(client):
    assert post_vitals(client, hr=140, hr_confidence="good")
    assert post_vitals(client, hr=150, hr_confidence="good") is None


# ---------------------------------------------------------------- refills
def record(days_ago, duration, ordered=False):
    issued = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")
    return {"prescriptions": [{"id": 1, "external_id": "RX-1", "issued_at": issued, "items": [{"drug_name": "Crocin DS", "duration_days": duration}]}],
            "orders": [{"items": [{"drug_name": "Crocin DS"}]}] if ordered else []}


def test_a_course_about_to_end_raises_a_refill_alert():
    out = alerts.refill_alerts_for({"name": "T", "external_id": "SNJ-1", "phone": "+1"}, record(days_ago=8, duration=10))
    assert len(out) == 1 and "Crocin" in out[0]["title"]


@pytest.mark.parametrize("days_ago,duration,ordered", [(1, 30, False), (8, 10, True)])
def test_no_refill_alert_when_far_from_the_end_or_already_ordered(days_ago, duration, ordered):
    assert alerts.refill_alerts_for({"name": "T", "external_id": "SNJ-1", "phone": "+1"}, record(days_ago, duration, ordered)) == []


# ---------------------------------------------------------------- the gates before a call
@pytest.fixture
def queued(client, monkeypatch):
    monkeypatch.setenv("VOICEBOT_URL", "https://vb.example")
    monkeypatch.setenv("VOICEBOT_API_KEY", "k")
    monkeypatch.setattr(alerts, "_quiet_now", lambda: False)
    return post_vitals(client, hr=140, hr_confidence="good")


def flags(consent=True, phone="+10000000000"):
    async def fake(*a, **k):
        return {"found": True, "consent": consent, "phone": phone, "name": "T", "external_id": "SNJ-1"}
    return fake


def call(client, a):
    return client.post(f"/api/v1/alerts/{a['id']}/call")


def test_no_call_without_consent(client, queued, monkeypatch):
    monkeypatch.setattr(alerts, "_patient_flags", flags(consent=False))
    r = call(client, queued)
    assert r.status_code == 409 and "agreed" in r.json()["detail"]
    assert store.get_alert(queued["id"])["status"] == "blocked"


def test_no_call_to_a_number_off_the_allow_list(client, queued, monkeypatch):
    monkeypatch.setattr(alerts, "_patient_flags", flags())
    monkeypatch.setenv("VOICEBOT_ALLOWED_NUMBERS", "+19999999999")
    assert "demo numbers" in call(client, queued).json()["detail"]


def test_no_call_in_quiet_hours(client, queued, monkeypatch):
    monkeypatch.setattr(alerts, "_patient_flags", flags())
    monkeypatch.setattr(alerts, "_quiet_now", lambda: True)
    assert "quiet hours" in call(client, queued).json()["detail"]


def test_a_blocked_alert_never_dialled(client, queued, monkeypatch):
    monkeypatch.setattr(alerts, "_patient_flags", flags(consent=False))
    monkeypatch.setattr(alerts.httpx.AsyncClient, "post", lambda *a, **k: pytest.fail("a blocked alert tried to dial"))
    assert call(client, queued).status_code == 409


def test_quiet_hours_window_wraps_midnight(monkeypatch):
    class Fake(datetime):
        hour_now = 22

        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 3, cls.hour_now, 0, tzinfo=tz)
    monkeypatch.setattr(alerts, "datetime", Fake)
    for hour, quiet in [(22, True), (3, True), (8, False), (12, False), (20, False), (21, True)]:
        Fake.hour_now = hour
        assert alerts._quiet_now() is quiet, hour
