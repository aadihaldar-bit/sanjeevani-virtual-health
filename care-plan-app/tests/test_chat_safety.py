"""The text assistant's safety rules. These must keep holding, whatever the model says."""
import pytest

import chat
import store
from conftest import PLAN


@pytest.mark.parametrize("text", [
    "he is not breathing properly and his lips look swollen", "My chest feels tight", "pain in his chest", "she fainted",
    "he is gasping", "his lips are turning blue", "very drowsy and limp", "I think he took too much", "عنده ضيق في التنفس",
])
def test_emergency_wording_is_caught(text):
    assert chat.RED_FLAGS.search(text)


@pytest.mark.parametrize("text", ["Can I give it with milk?", "What if I miss a dose?", "He has a rash", "How long do I take it?"])
def test_ordinary_questions_are_not_emergencies(text):
    assert not chat.RED_FLAGS.search(text)


@pytest.mark.parametrize("reply", [
    "Please stop the medication and contact Dr. Rao.", "You can double the dose if needed.", "Skip the evening dose.",
    "I am sorry. Please stop giving it and call your doctor.",
])
def test_advice_to_stop_or_change_a_medicine_is_removed(reply):
    out, changed = chat._guard(reply)
    assert changed
    assert "stop" not in out.lower().replace("before changing", "") and "double" not in out.lower()
    assert chat.SAFE_TAIL in out


def test_harmless_replies_pass_through_untouched():
    text = "Give it with water. Drink plenty of fluids."
    assert chat._guard(text) == (text, False)


def test_dr_abbreviation_does_not_split_a_sentence():
    out, _ = chat._guard("Please stop the medicine and ask Dr. Rao.")
    assert not out.startswith("Rao")


def test_emergency_never_reaches_the_model(client, monkeypatch):
    store.put_plan("RX-1", PLAN)
    monkeypatch.setattr(chat, "_ask", lambda *a, **k: pytest.fail("the model was asked about an emergency"))
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    r = client.post("/api/v1/chat", json={"prescription_id": "RX-1", "messages": [{"role": "user", "text": "his lips are swollen"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["emergency"] and body["doctor"] and "998" in body["reply"]


def test_a_stop_instruction_from_the_model_is_cleaned(client, monkeypatch):
    store.put_plan("RX-1", PLAN)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(chat, "_ask", lambda *a, **k: "I am sorry about the rash. Please stop the syrup. [[DOCTOR]]")
    r = client.post("/api/v1/chat", json={"prescription_id": "RX-1", "messages": [{"role": "user", "text": "he has a rash"}]})
    body = r.json()
    assert "stop the syrup" not in body["reply"] and chat.SAFE_TAIL in body["reply"] and body["doctor"]


def test_an_unknown_or_expired_plan_is_a_404(client):
    r = client.post("/api/v1/chat", json={"prescription_id": "RX-NOPE", "messages": [{"role": "user", "text": "hi"}]})
    assert r.status_code == 404


def test_the_last_message_must_be_from_the_user(client):
    store.put_plan("RX-1", PLAN)
    r = client.post("/api/v1/chat", json={"prescription_id": "RX-1", "messages": [{"role": "assistant", "text": "hello"}]})
    assert r.status_code == 422
