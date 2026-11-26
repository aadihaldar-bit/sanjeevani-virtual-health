"""PDF prescription reader + draft/confirm safety.

The rules under test are the safety-critical ones: a scanned/empty/non-PDF is rejected (never
guessed), a parsed prescription is a DRAFT that schedules no calls, a missing or low-confidence dose
comes back flagged for a pharmacist with no guessed value, and a draft cannot be confirmed into calls
while any "confirmed" medicine still has no dose.
"""
import sys
import types
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import FollowUpTask, PrescriptionItem
from app.prescription_pdf import PdfParseError, extract_pdf_text, extract_prescription_fields
from app.prescriptions_api import (
    ItemIn,
    PatientIn,
    PrescriptionIn,
    PrescriptionPushRequest,
    ingest_prescription,
    resolve_confirmed_status,
)

pytestmark = pytest.mark.asyncio

T = 1
DAY0 = datetime(2026, 1, 1, 10, 0, 0)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


# ── extract_pdf_text: only readable digital PDFs get through ──────────────────────────────────
def test_empty_upload_rejected():
    with pytest.raises(PdfParseError):
        extract_pdf_text(b"")


def test_non_pdf_rejected():
    with pytest.raises(PdfParseError):
        extract_pdf_text(b"this is just a text file, not a pdf")


def test_image_only_pdf_rejected(monkeypatch):
    """A scanned PDF has the %PDF- header but no text layer — must be refused, not guessed at."""
    fake = types.ModuleType("pypdf")

    class _Page:
        def extract_text(self):
            return ""

    class _Reader:
        is_encrypted = False

        def __init__(self, *a, **k):
            self.pages = [_Page()]

    fake.PdfReader = _Reader
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    with pytest.raises(PdfParseError):
        extract_pdf_text(b"%PDF-1.4 fake body long enough to pass the size check ...")


# ── extract_prescription_fields: no guessed doses ────────────────────────────────────────────
def _install_fake_gemini(monkeypatch, payload):
    import json as _json
    fake = types.ModuleType("google.generativeai")

    class _Resp:
        text = _json.dumps(payload)

    class _Model:
        def __init__(self, *a, **k):
            pass

        def generate_content(self, *a, **k):
            return _Resp()

    fake.configure = lambda **k: None
    fake.GenerativeModel = _Model
    pkg = types.ModuleType("google")
    pkg.generativeai = fake
    monkeypatch.setitem(sys.modules, "google", pkg)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_low_confidence_dose_is_flagged_not_guessed(monkeypatch):
    _install_fake_gemini(monkeypatch, {
        "patient": {"first_name": "Aarav", "guardian_name": "Rahul", "identity_dob": "2023-03-14"},
        "prescription": {"doctor_name": "Dr. Sonia", "facility": "Sanjeevani"},
        "items": [
            {"drug_name": "Crocin DS", "dose_text": "4 ml", "frequency_per_day": 2,
             "duration_days": 5, "confidence": "high"},
            {"drug_name": "Ephodrex", "dose_text": "3.5 or 4 ml", "confidence": "low",
             "open_question": "two doses on the note"},
            {"drug_name": "Mystery", "dose_text": None, "confidence": "medium"},
        ],
    })
    out = extract_prescription_fields("some prescription text")
    items = out["items"]
    assert items[0]["status"] == "confirmed" and items[0]["dose_text"] == "4 ml"
    # low confidence -> dose dropped, flagged for pharmacist
    assert items[1]["status"] == "needs_clarification" and items[1]["dose_text"] is None and items[1]["open_question"]
    # no dose at all -> flagged, open_question supplied
    assert items[2]["status"] == "needs_clarification" and items[2]["dose_text"] is None and items[2]["open_question"]


def test_arabic_prescription_fields_pass_through(monkeypatch):
    """An Arabic prescription: Arabic drug name/timing kept verbatim, Arabic-Indic digits parsed."""
    _install_fake_gemini(monkeypatch, {
        "patient": {"first_name": "آراف", "guardian_name": "راहول", "identity_dob": "2023-03-14"},
        "prescription": {"doctor_name": "د. سونيا", "facility": "برجيل"},
        "items": [
            {"drug_name": "كروسين شراب", "dose_text": "٤ مل", "frequency_per_day": "٢",
             "timing": "صباحاً ومساءً", "duration_days": "٥", "confidence": "high"},
        ],
    })
    out = extract_prescription_fields("نص الوصفة الطبية")
    assert out["patient"]["first_name"] == "آراف"
    assert out["prescription"]["doctor_name"] == "د. سونيا"
    item = out["items"][0]
    assert item["drug_name"] == "كروسين شراب" and item["timing"] == "صباحاً ومساءً"
    assert item["dose_text"] == "٤ مل" and item["status"] == "confirmed"
    # Arabic-Indic digits normalised to integers
    assert item["frequency_per_day"] == 2 and item["duration_days"] == 5


def test_no_model_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_LIVE_API_KEY", raising=False)
    with pytest.raises(PdfParseError):
        extract_prescription_fields("text")


# ── Draft ingestion schedules nothing ────────────────────────────────────────────────────────
def _push(items):
    return PrescriptionPushRequest(
        patient=PatientIn(external_id="P-1", first_name="Aarav", phone_e164="+971500000001",
                          identity_dob="2023-03-14", preferred_language="en"),
        prescription=PrescriptionIn(external_id="RX-PDF-1", doctor_name="Dr. Sonia"),
        items=items,
    )


async def test_draft_ingestion_creates_no_calls(session):
    res = await ingest_prescription(session, T, _push([
        ItemIn(drug_name="Crocin DS", dose_text="4 ml", start_date=DAY0, duration_days=5),
    ]), schedule=False)
    assert res["status"] == "draft" and res["tasks_created"] == 0

    items = (await session.execute(select(PrescriptionItem))).scalars().all()
    assert all(i.status == "draft" for i in items)
    assert (await session.execute(select(FollowUpTask))).scalars().all() == []


async def test_skip_review_is_visible_but_schedules_no_calls(session):
    """Review-off path: draft=False keeps the prescription visible (status issued) with the parsed item
    statuses, and schedule=False means no calls go out."""
    res = await ingest_prescription(session, T, _push([
        ItemIn(drug_name="Crocin DS", dose_text="4 ml", start_date=DAY0, duration_days=5, status="confirmed"),
    ]), schedule=False, draft=False)
    assert res["status"] == "created" and res["tasks_created"] == 0

    from app.models import Prescription
    presc = (await session.execute(select(Prescription))).scalars().first()
    assert presc.status == "issued"
    items = (await session.execute(select(PrescriptionItem))).scalars().all()
    assert all(i.status == "confirmed" for i in items)        # parsed status kept, not forced to draft
    assert (await session.execute(select(FollowUpTask))).scalars().all() == []   # no calls


async def test_normal_ingestion_still_schedules(session):
    res = await ingest_prescription(session, T, _push([
        ItemIn(drug_name="Crocin DS", dose_text="4 ml", start_date=DAY0, duration_days=5),
    ]), schedule=True)
    assert res["status"] == "created" and res["tasks_created"] >= 1


# ── The confirm safety rule ──────────────────────────────────────────────────────────────────
def test_confirmed_without_dose_downgrades_to_needs_clarification():
    assert resolve_confirmed_status("confirmed", None) == "needs_clarification"
    assert resolve_confirmed_status("confirmed", "   ") == "needs_clarification"
    assert resolve_confirmed_status("confirmed", "4 ml") == "confirmed"
    assert resolve_confirmed_status("needs_clarification", "4 ml") == "needs_clarification"
