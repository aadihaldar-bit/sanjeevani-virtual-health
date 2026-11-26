"""Tenant isolation on the six Sanjeevani tables, and role gating (viewer/operator cannot run/cancel)."""
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import tenancy
from app.database import Base
from app.models import (
    DoctorMeetingRequest,
    FollowUpTask,
    MedicineFeedback,
    Patient,
    Prescription,
    PrescriptionItem,
)
from app.tenancy import TenantScope

pytestmark = pytest.mark.asyncio

A, B = 10, 20          # two tenant ids


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        for t in (A, B):
            patient = Patient(tenant_id=t, first_name=f"pat{t}", phone_e164=f"+9715000000{t}")
            db.add(patient)
            await db.flush()
            presc = Prescription(tenant_id=t, patient_id=patient.id, status="issued")
            db.add(presc)
            await db.flush()
            db.add(PrescriptionItem(tenant_id=t, prescription_id=presc.id, drug_name=f"drug{t}"))
            db.add(FollowUpTask(tenant_id=t, patient_id=patient.id, prescription_id=presc.id,
                                kind="explain", due_at=datetime.utcnow()))
            db.add(MedicineFeedback(tenant_id=t, prescription_id=presc.id, helped="yes"))
            db.add(DoctorMeetingRequest(tenant_id=t, patient_id=patient.id, prescription_id=presc.id))
        await db.commit()
        yield db
    await engine.dispose()


SIX = (Patient, Prescription, PrescriptionItem, FollowUpTask, MedicineFeedback, DoctorMeetingRequest)


async def _scoped_rows(db, model, tenant_id):
    token = tenancy.current_scope.set(TenantScope(tenant_id=tenant_id, email="u", role="viewer"))
    try:
        return (await db.execute(select(model))).scalars().all()
    finally:
        tenancy.current_scope.reset(token)


@pytest.mark.parametrize("model", SIX)
async def test_each_tenant_reads_only_its_own_rows(session, model):
    a_rows = await _scoped_rows(session, model, A)
    b_rows = await _scoped_rows(session, model, B)
    assert a_rows and b_rows
    assert all(r.tenant_id == A for r in a_rows)
    assert all(r.tenant_id == B for r in b_rows)


async def test_unscoped_sees_everything(session):
    # No scope set -> the read filter is inert (platform/background work).
    rows = (await session.execute(select(Patient))).scalars().all()
    assert {r.tenant_id for r in rows} == {A, B}


# --- role gating used by the console routes (tenancy.role_denial via tenant_scope) ------------

@pytest.mark.parametrize("path", [
    "/api/sanjeevani/followups/1/run",
    "/api/sanjeevani/followups/1/cancel",
    "/api/sanjeevani/prescriptions/upload",
])
def test_only_admins_may_run_cancel_or_upload(path):
    assert tenancy.role_denial("viewer", "POST", path) is not None
    assert tenancy.role_denial("operator", "POST", path) is not None
    assert tenancy.role_denial("manager", "POST", path) is not None
    assert tenancy.role_denial("tenant_admin", "POST", path) is None
    assert tenancy.role_denial("super_admin", "POST", path) is None


def test_anyone_signed_in_may_read_the_listings():
    for role in ("viewer", "operator", "tenant_admin"):
        assert tenancy.role_denial(role, "GET", "/api/sanjeevani/followups") is None
        assert tenancy.role_denial(role, "GET", "/api/sanjeevani/reports/summary") is None
