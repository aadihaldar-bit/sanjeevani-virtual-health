import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Every test gets its own empty database file, and nothing reaches the real voice assistant or Gemini."""
    monkeypatch.setenv("SANJEEVANI_DB", str(tmp_path / "test.db"))
    for k in ("VOICEBOT_URL", "VOICEBOT_API_KEY", "VOICEBOT_ALLOWED_NUMBERS", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import alerts
    import chat
    import voicebot

    app = FastAPI()
    for r in (voicebot.router, alerts.router, chat.router):
        app.include_router(r)
    return TestClient(app)


PLAN = {
    "patient": {"patient_name": "Master Rayan", "mobile_number": "+10000000000", "patient_id": "SNJ-1"},
    "medicines": [{"drug_name": "Syp Crocin DS", "dose_text": "5 ml", "timing": "three times a day", "duration_days": 3}],
    "summary": {"doctor_name": "Dr. Rao", "primary_condition": "Fever"},
    "explanation": {},
}
