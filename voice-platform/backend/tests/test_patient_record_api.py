"""Tests for tenant-scoped patient directory endpoints:
GET /api/v1/patients (list patients)
POST /api/v1/patients/upsert (upsert patient)
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Ensure backend dir is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.main import app
from app.models import Patient
from app.patient_record_api import (
    PatientUpsertIn,
    list_company_patients,
    upsert_company_patient,
)

pytestmark = pytest.mark.asyncio

TENANT_A = 6
TENANT_B = 7


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        # Seed patients for tenant A and tenant B
        p_a1 = Patient(
            tenant_id=TENANT_A,
            external_id="SNJ-001",
            first_name="Sonika",
            last_name="Patel",
            guardian_name=None,
            phone_e164="+919380361678",
            identity_dob="1995-03-24",
            preferred_language="en",
            consent_calls=True,
            do_not_call=False,
        )
        p_a2 = Patient(
            tenant_id=TENANT_A,
            external_id="SNJ-002",
            first_name="Ahmed",
            last_name="Sami",
            guardian_name=None,
            phone_e164="+971501112233",
            identity_dob="1988-07-15",
            preferred_language="ar",
            consent_calls=True,
            do_not_call=False,
        )
        p_b = Patient(
            tenant_id=TENANT_B,
            external_id="OTH-001",
            first_name="Zara",
            last_name="Khan",
            guardian_name=None,
            phone_e164="+971509998877",
            identity_dob="1992-11-03",
            preferred_language="en",
            consent_calls=True,
            do_not_call=False,
        )
        s.add_all([p_a1, p_a2, p_b])
        await s.commit()
        yield s
    await engine.dispose()


async def test_list_patients_requires_tenant(db):
    client = SimpleNamespace(tenant_id=None)
    with pytest.raises(HTTPException) as exc_info:
        await list_company_patients(client=client, db=db)
    assert exc_info.value.status_code == 403


async def test_list_patients_tenant_scoped_and_ordered(db):
    client = SimpleNamespace(tenant_id=TENANT_A)
    res = await list_company_patients(client=client, db=db)

    assert "patients" in res
    assert "count" in res
    assert res["count"] == 2
    patients = res["patients"]
    # Ordered by first_name, last_name -> Ahmed Sami, then Sonika Patel
    assert patients[0]["first_name"] == "Ahmed"
    assert patients[0]["last_name"] == "Sami"
    assert patients[0]["name"] == "Ahmed Sami"
    assert patients[0]["external_id"] == "SNJ-002"
    assert patients[0]["preferred_language"] == "ar"

    assert patients[1]["first_name"] == "Sonika"
    assert patients[1]["last_name"] == "Patel"
    assert patients[1]["name"] == "Sonika Patel"
    assert patients[1]["phone_e164"] == "+919380361678"

    # Tenant B patient must not be present
    b_ids = [p["external_id"] for p in patients]
    assert "OTH-001" not in b_ids


async def test_upsert_patient_requires_tenant(db):
    client = SimpleNamespace(tenant_id=None)
    body = PatientUpsertIn(
        external_id="SNJ-100",
        phone_e164="+918888888888",
        first_name="NoTenant",
    )
    with pytest.raises(HTTPException) as exc_info:
        await upsert_company_patient(body=body, client=client, db=db)
    assert exc_info.value.status_code == 403


async def test_upsert_patient_creates_new(db):
    client = SimpleNamespace(tenant_id=TENANT_A)
    body = PatientUpsertIn(
        external_id="SNJ-003",
        phone_e164="+919000000001",
        first_name="Aadi",
        last_name="Haldar",
        identity_dob="2000-01-01",
        preferred_language="en",
        consent_calls=True,
    )
    res = await upsert_company_patient(body=body, client=client, db=db)

    assert res["status"] == "success"
    p = res["patient"]
    assert p["id"] is not None
    assert p["external_id"] == "SNJ-003"
    assert p["name"] == "Aadi Haldar"
    assert p["phone_e164"] == "+919000000001"
    assert p["identity_dob"] == "2000-01-01"

    # Verify patient now in list
    listed = await list_company_patients(client=client, db=db)
    assert listed["count"] == 3
    ext_ids = [x["external_id"] for x in listed["patients"]]
    assert "SNJ-003" in ext_ids


async def test_upsert_patient_updates_existing(db):
    client = SimpleNamespace(tenant_id=TENANT_A)
    # Update Sonika's phone and dob
    body = PatientUpsertIn(
        external_id="SNJ-001",
        phone_e164="+919380361999",
        first_name="Sonika",
        last_name="Patel-Updated",
        identity_dob="1995-03-25",
        consent_calls=False,
    )
    res = await upsert_company_patient(body=body, client=client, db=db)

    assert res["status"] == "success"
    p = res["patient"]
    assert p["name"] == "Sonika Patel-Updated"
    assert p["phone_e164"] == "+919380361999"
    assert p["identity_dob"] == "1995-03-25"

    # Verify no duplicate was created
    listed = await list_company_patients(client=client, db=db)
    assert listed["count"] == 2
    sonika = next(x for x in listed["patients"] if x["external_id"] == "SNJ-001")
    assert sonika["last_name"] == "Patel-Updated"
    assert sonika["phone_e164"] == "+919380361999"
    assert sonika["identity_dob"] == "1995-03-25"
    assert sonika["consent_calls"] is False


async def test_upsert_patient_tenant_isolation(db):
    # Upsert with same phone under tenant B creates/updates tenant B's patient, not tenant A's
    client_b = SimpleNamespace(tenant_id=TENANT_B)
    body = PatientUpsertIn(
        external_id="SNJ-001",  # Same MRN as tenant A
        phone_e164="+919380361678",
        first_name="TenantB",
        last_name="Copy",
    )
    res = await upsert_company_patient(body=body, client=client_b, db=db)
    assert res["status"] == "success"

    # Verify tenant A still has Sonika
    res_a = await list_company_patients(client=SimpleNamespace(tenant_id=TENANT_A), db=db)
    sonika_a = next(x for x in res_a["patients"] if x["external_id"] == "SNJ-001")
    assert sonika_a["first_name"] == "Sonika"


async def test_http_endpoints_registered_and_require_auth():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        r_list = await ac.get("/api/v1/patients")
        assert r_list.status_code in (401, 403)

        r_upsert = await ac.post("/api/v1/patients/upsert", json={
            "phone_e164": "+919380361678",
            "first_name": "Test"
        })
        assert r_upsert.status_code in (401, 403)
