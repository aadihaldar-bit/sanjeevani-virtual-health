"""Prescription ingestion: end-date computation, task creation + end-date grouping, idempotency."""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import FollowUpTask, Patient, Prescription, PrescriptionItem
from app.prescriptions_api import (
    ItemIn,
    PatientIn,
    PrescriptionIn,
    PrescriptionPushRequest,
    ingest_prescription,
)

pytestmark = pytest.mark.asyncio

T = 1  # a tenant id (no Tenant row needed; timezone falls back to the default)
DAY0 = datetime(2026, 1, 1, 10, 0, 0)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


def _push(ext="RX-1", items=None, phone="+971500000001"):
    return PrescriptionPushRequest(
        patient=PatientIn(external_id="P-1", first_name="Aarav", phone_e164=phone,
                          identity_dob="2015-05-20", preferred_language="en"),
        prescription=PrescriptionIn(external_id=ext, doctor_name="Dr Khan", facility="Sanjeevani"),
        items=items or [],
    )


async def test_end_date_is_start_plus_duration_else_null(session):
    push = _push(items=[
        ItemIn(drug_name="Amoxicillin", start_date=DAY0, duration_days=5),
        ItemIn(drug_name="Vitamin D", start_date=DAY0),            # no duration -> no end_date
        ItemIn(drug_name="Syrup", duration_days=7),                # no start -> the course starts when the prescription was issued
    ])
    res = await ingest_prescription(session, T, push)
    assert res["status"] == "created" and res["items_created"] == 3

    items = (await session.execute(select(PrescriptionItem).order_by(PrescriptionItem.id))).scalars().all()
    assert items[0].end_date == DAY0 + timedelta(days=5)
    assert items[1].end_date is None
    assert items[2].end_date is not None and abs((items[2].end_date - datetime.utcnow()).days - 7) <= 1


async def test_one_explain_and_one_feedback_per_distinct_end_date(session):
    push = _push(items=[
        ItemIn(drug_name="A", start_date=DAY0, duration_days=5),    # ends day 5
        ItemIn(drug_name="B", start_date=DAY0, duration_days=5),    # ends day 5 (same -> grouped)
        ItemIn(drug_name="C", start_date=DAY0, duration_days=10),   # ends day 10
        ItemIn(drug_name="D", start_date=DAY0),                     # no end_date -> no feedback call
    ])
    res = await ingest_prescription(session, T, push)
    tasks = (await session.execute(select(FollowUpTask))).scalars().all()
    kinds = sorted(t.kind for t in tasks)
    assert kinds == ["explain", "feedback", "feedback"]            # 1 explain + 2 distinct end dates
    assert res["feedback_tasks"] == 2


async def test_ingestion_is_idempotent_on_external_id(session):
    push = _push(ext="RX-SAME", items=[ItemIn(drug_name="A", start_date=DAY0, duration_days=3)])
    first = await ingest_prescription(session, T, push)
    assert first["status"] == "created" and first["tasks_created"] == 2

    again = await ingest_prescription(session, T, _push(ext="RX-SAME",
                                      items=[ItemIn(drug_name="A", start_date=DAY0, duration_days=3)]))
    assert again["status"] == "exists" and again["tasks_created"] == 0

    assert len((await session.execute(select(Prescription))).scalars().all()) == 1
    assert len((await session.execute(select(FollowUpTask))).scalars().all()) == 2   # not doubled


async def test_patient_is_upserted_not_duplicated(session):
    await ingest_prescription(session, T, _push(ext="RX-1"))
    await ingest_prescription(session, T, _push(ext="RX-2"))
    patients = (await session.execute(select(Patient))).scalars().all()
    assert len(patients) == 1 and patients[0].first_name == "Aarav"
