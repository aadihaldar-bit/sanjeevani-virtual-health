"""The Pharmacy Desk: an order from "I need medicine" to a scheduled delivery, with the one-time links in between."""
import re
from datetime import datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import care_links, database, prescription_pdf, tool_executor as te_mod
from app.database import Base, get_db
from app.models import CareLink, Call, CallStatus, MedicineOrder, Patient

pytestmark = pytest.mark.asyncio

TENANT = 9
PHONE = "+971507776666"

READ = {"patient": {}, "prescription": {}, "items": [
    {"drug_name": "Crocin DS Syrup", "strength": None, "form": "syrup", "dose_text": "4 ml", "timing": "morning and evening",
     "duration_days": 5, "status": "confirmed", "open_question": None},
    {"drug_name": "Zorvix Forte", "strength": "250 mg", "form": "tablet", "dose_text": None, "timing": None, "duration_days": None,
     "status": "needs_clarification", "open_question": "dose unclear"}]}


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pharmacy.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)
    monkeypatch.setattr(care_links, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(prescription_pdf, "extract_prescription_from_file", lambda data, mime: READ)
    async with maker() as db:
        call = Call(tenant_id=TENANT, status=CallStatus.ACTIVE, source="smartflo-stream", caller_number=PHONE, metadata_json={})
        db.add(call)
        await db.commit()
        call_id = call.id
    app = FastAPI()
    app.include_router(care_links.public_router)

    async def _db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    yield te_mod.ToolExecutor(), maker, client, call_id
    await client.aclose()
    await engine.dispose()


async def run(te, name, call_id, **args):
    res = await te.execute(name, {**args, "call_id": call_id, "caller_number": PHONE})
    return res


def token_and_code(res):
    shown = res["display"]
    return re.search(r"t=([\w-]+)", shown).group(1), re.search(r"code: (\d{6})", shown).group(1)


async def upload(client, token, code, with_id=True):
    files = {"prescription": ("rx.jpg", b"\xff\xd8fake", "image/jpeg")}
    if with_id:
        files["id_document"] = ("id.jpg", b"\xff\xd8id", "image/jpeg")
    return await client.post("/api/care/prescription", data={"t": token, "code": code}, files=files)


async def test_the_steps_cannot_be_taken_out_of_order(world):
    te, _, _, cid = world
    assert "no order yet" in (await run(te, "send_prescription_link", cid))["result"].lower()
    await run(te, "start_medicine_order", cid, medicines="paracetamol syrup")
    assert "nothing has been uploaded" in (await run(te, "check_prescription_upload", cid))["result"].lower()
    assert "has to be received" in (await run(te, "confirm_order_details", cid, address="Villa 12, Jumeirah 1, Dubai"))["result"]
    assert "after payment" in (await run(te, "schedule_delivery", cid))["result"]
    assert "confirmed before payment" in (await run(te, "send_payment_link", cid))["result"]


async def test_a_full_order_from_request_to_scheduled_delivery(world):
    te, maker, client, cid = world
    started = await run(te, "start_medicine_order", cid, medicines="Crocin syrup for my son")
    assert "ORD-" in started["result"]

    link = await run(te, "send_prescription_link", cid)
    token, code = token_and_code(link)
    assert code not in link["result"].replace(" ".join(code), "")                  # the model is told the code spaced out, to read slowly
    assert " ".join(code) in link["result"]
    assert "?t=" in link["display"] and "One-time code" in link["display"]

    info = (await client.get("/api/care/link", params={"t": token})).json()
    assert info["kind"] == "prescription" and info["status"] == "pending"

    bad = await upload(client, token, "000000" if code != "000000" else "111111")
    assert bad.status_code == 403 and "tries left" in bad.json()["detail"]
    ok = await upload(client, token, code)
    assert ok.status_code == 200 and ok.json() == {"ok": True, "medicines": 2, "id_received": True}

    read = (await run(te, "check_prescription_upload", cid))["result"]
    assert "Crocin DS Syrup" in read and "A paracetamol syrup for fever" in read and "18 dirhams" in read
    assert "Zorvix Forte" in read and "Do not state a dose" in read and "price to be confirmed by the pharmacist" in read
    assert "total 33 dirhams" in read                                                  # 18 + 15 delivery; Zorvix is not priced here
    assert "ID photo: received" in read

    assert "full delivery address" in (await run(te, "confirm_order_details", cid, address="Dubai"))["result"]
    saved = await run(te, "confirm_order_details", cid, address="Villa 12, Street 5, Jumeirah 1, Dubai", contact_phone="+971501234567")
    assert "total is 33 dirhams" in saved["result"]

    pay = await run(te, "send_payment_link", cid)
    ptoken, pcode = token_and_code(pay)
    assert "33 dirhams" in pay["display"] and ptoken != token
    assert "not come through" in (await run(te, "check_payment", cid))["result"]
    paid = await client.post("/api/care/pay", json={"t": ptoken, "code": pcode})
    assert paid.status_code == 200
    assert "Payment received" in (await run(te, "check_payment", cid))["result"]

    options = (await run(te, "schedule_delivery", cid))["result"]
    slot = re.search(r"\[slot=([0-9T:\-]+)\]", options).group(1)
    assert "not one we offer" in (await run(te, "schedule_delivery", cid, slot="2026-01-01T03:00"))["result"]
    done = (await run(te, "schedule_delivery", cid, slot=slot))["result"]
    assert "Delivery scheduled" in done and "Jumeirah 1" in done

    async with maker() as db:
        o = (await db.execute(select(MedicineOrder))).scalars().one()
        assert (o.status, o.payment_status, o.amount, o.id_document_received) == ("scheduled", "paid", 33.0, True)
        assert o.delivery_start > datetime.utcnow() and o.contact_phone == "+971501234567" and o.tenant_id == TENANT


async def test_a_link_works_once_and_a_wrong_code_locks_it(world):
    te, maker, client, cid = world
    await run(te, "start_medicine_order", cid, medicines="syrup")
    token, code = token_and_code(await run(te, "send_prescription_link", cid))
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(5):
        res = await upload(client, token, wrong)
    assert res.status_code == 403 and "Too many wrong codes" in res.json()["detail"]
    assert (await upload(client, token, code)).status_code == 403                    # locked, even with the right code now
    token2, code2 = token_and_code(await run(te, "send_prescription_link", cid))     # a new link
    assert (await upload(client, token2, code2)).status_code == 200
    assert (await upload(client, token2, code2)).status_code == 403                  # used already


async def test_an_expired_link_is_refused(world):
    te, maker, client, cid = world
    await run(te, "start_medicine_order", cid, medicines="syrup")
    token, code = token_and_code(await run(te, "send_prescription_link", cid))
    async with maker() as db:
        link = (await db.execute(select(CareLink).where(CareLink.token == token))).scalars().one()
        link.expires_at = datetime.utcnow() - timedelta(minutes=1)
        await db.commit()
    res = await upload(client, token, code)
    assert res.status_code == 403 and "expired" in res.json()["detail"]
    assert (await client.get("/api/care/link", params={"t": token})).json()["status"] == "expired"


async def test_a_new_link_retires_the_old_one(world):
    te, _, client, cid = world
    await run(te, "start_medicine_order", cid, medicines="syrup")
    old, old_code = token_and_code(await run(te, "send_prescription_link", cid))
    await run(te, "send_prescription_link", cid)
    res = await upload(client, old, old_code)
    assert res.status_code == 403 and "expired" in res.json()["detail"]


async def test_a_photo_with_no_readable_medicines_is_not_accepted(world, monkeypatch):
    te, _, client, cid = world
    monkeypatch.setattr(prescription_pdf, "extract_prescription_from_file", lambda d, m: {"patient": {}, "prescription": {}, "items": []})
    await run(te, "start_medicine_order", cid, medicines="syrup")
    token, code = token_and_code(await run(te, "send_prescription_link", cid))
    res = await upload(client, token, code)
    assert res.status_code == 422 and "clearer" in res.json()["detail"]
    assert (await client.get("/api/care/link", params={"t": token})).json()["status"] == "pending"      # the person can try again


async def test_unknown_tokens_say_nothing(world):
    _, _, client, _ = world
    assert (await client.get("/api/care/link", params={"t": "nope"})).status_code == 404
    assert (await client.post("/api/care/pay", json={"t": "nope", "code": "123456"})).status_code == 404


async def test_a_known_caller_is_attached_to_the_order(world):
    te, maker, _, cid = world
    async with maker() as db:
        db.add(Patient(tenant_id=TENANT, first_name="Aadi", phone_e164=PHONE))
        await db.commit()
    await run(te, "start_medicine_order", cid, medicines="syrup")
    async with maker() as db:
        o = (await db.execute(select(MedicineOrder))).scalars().one()
        assert o.patient_id is not None
