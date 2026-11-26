"""The audit log records actions without ever blocking them. These tests hold whatever redaction policy redact() implements."""
import audit
import chat
import store
from conftest import PLAN


def test_an_entry_is_written_with_who_what_and_when(client):
    audit.log("patient.viewed", "SNJ-1")
    (e,) = store.list_audit()
    assert e["action"] == "patient.viewed" and e["subject"] == "SNJ-1" and e["actor"] == "system" and e["at"].endswith("Z")


def test_only_what_redact_returns_is_stored(monkeypatch):
    monkeypatch.setattr(audit, "redact", lambda action, detail: {"kept": detail["keep"]})
    audit.log("x.y", "S", {"keep": 1, "question": "a private question", "phone": "+971501234567"})
    assert store.list_audit()[0]["detail"] == {"kept": 1}


def test_the_raw_detail_is_never_stored_by_the_logger_itself(monkeypatch):
    monkeypatch.setattr(audit, "redact", lambda action, detail: {})
    audit.log("x.y", "S", {"question": "a private question"})
    assert store.list_audit()[0]["detail"] is None


def test_a_failing_write_never_stops_the_action(monkeypatch):
    monkeypatch.setattr(store, "put_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    audit.log("x.y", "S")            # must not raise


def test_a_failing_redact_never_stops_the_action(monkeypatch):
    monkeypatch.setattr(audit, "redact", lambda *a: (_ for _ in ()).throw(ValueError("bad rule")))
    audit.log("x.y", "S", {"a": 1})  # must not raise


def test_two_callers_are_told_apart_without_storing_the_address(client):
    from starlette.requests import Request

    def req(ip):
        return Request({"type": "http", "headers": [(b"cf-connecting-ip", ip.encode())], "client": ("1.1.1.1", 1)})
    a, b = audit._actor(req("203.0.113.5")), audit._actor(req("203.0.113.6"))
    assert a != b and a.startswith("staff:") and "203" not in a


def test_chat_and_alert_actions_leave_an_entry(client, monkeypatch):
    store.put_plan("RX-1", PLAN)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    client.post("/api/v1/chat", json={"prescription_id": "RX-1", "messages": [{"role": "user", "text": "his lips are swollen"}]})
    a = client.post("/api/v1/alerts/vitals", json={"patient_id": "SNJ-1", "phone": "+1", "name": "T", "hr": 140, "hr_confidence": "good"}).json()["alert"]
    client.post(f"/api/v1/alerts/{a['id']}/dismiss")
    actions = [e["action"] for e in store.list_audit()]
    assert "chat.message" in actions and "alert.dismissed" in actions


def test_the_log_can_be_read_back_newest_first(client):
    for i in range(3):
        audit.log("n.test", f"S{i}")
    import fastapi
    from fastapi.testclient import TestClient
    app = fastapi.FastAPI()
    app.include_router(audit.router)
    rows = TestClient(app).get("/api/v1/audit?action=n.test").json()["entries"]
    assert [r["subject"] for r in rows] == ["S2", "S1", "S0"]


# ---------------------------------------------------------------- the redaction policy
def test_phone_numbers_keep_only_the_last_three_digits():
    out = audit.redact("alert.call_placed", {"phone": "+971501234567"})
    assert out == {"phone": "+971******567"} and "5012" not in out["phone"]


def test_a_patient_id_is_not_masked_but_a_searched_number_is():
    assert audit.redact("patient.viewed", {"query": "SNJ-98214"}) == {"query": "SNJ-98214"}
    assert audit.redact("patient.viewed", {"query": "+919380361678"})["query"].endswith("678")
    assert "9380" not in audit.redact("patient.viewed", {"query": "+919380361678"})["query"]


def test_what_was_said_is_never_stored_only_how_much():
    out = audit.redact("chat.message", {"question": "he has a rash on his arms", "reply": "Please see your doctor.", "emergency": False, "doctor": True})
    assert out == {"question_chars": 25, "reply_chars": 23, "emergency": False, "doctor": True}
    assert "rash" not in str(out) and "doctor." not in str(out)


def test_unknown_fields_are_dropped_by_default():
    assert audit.redact("x.y", {"diagnosis": "asthma", "address": "Al Reem", "alert_id": "ab12"}) == {"alert_id": "ab12"}


def test_a_long_reason_is_cut_short():
    assert len(audit.redact("alert.call_blocked", {"reason": "x" * 500})["reason"]) == 120


def test_the_log_for_a_real_chat_holds_no_patient_words(client, monkeypatch):
    store.put_plan("RX-1", PLAN)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    client.post("/api/v1/chat", json={"prescription_id": "RX-1", "messages": [{"role": "user", "text": "his lips are swollen"}]})
    entry = [e for e in store.list_audit() if e["action"] == "chat.message"][0]
    assert entry["detail"]["emergency"] is True and "swollen" not in str(entry)
