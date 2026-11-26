"""One-time links the agent gives a caller during a call, and the public pages they open.

The caller hears a short code; the link itself is shown in the live transcript on the console (and later can go by SMS). To use the
link the person opens it, types the code, and either uploads their prescription and ID photo or confirms a payment. A link is a long
random token, expires after 20 minutes, works once and locks after five wrong codes.

The payment step is a DEMO: it records the order as paid and takes no money. A real payment provider replaces `pay` before any real use.
"""
import asyncio
import logging
import os
import secrets
import tempfile
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import sanjeevani_care as care
from .database import get_db
from .models import CareLink, MedicineOrder

logger = logging.getLogger("care.links")

LINK_TTL_MINUTES = 20
MAX_WRONG_CODES = 5
# Prescription and ID photos land in the server's temporary folder unless CARE_UPLOAD_DIR points somewhere kept. For the demo that is
# deliberate: ID photos are not retained beyond the container's life.
UPLOAD_DIR = os.getenv("CARE_UPLOAD_DIR", os.path.join(tempfile.gettempdir(), "care_uploads"))

public_router = APIRouter(prefix="/api/care", tags=["care-links"])


def public_base() -> str:
    explicit = (os.getenv("CARE_PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    kyc = (os.getenv("KYC_PUBLIC_URL") or "https://voicebot-agent.stldigitaltech.com/kyc").strip().rstrip("/")
    return (kyc[:-4] if kyc.endswith("/kyc") else kyc) + "/care"


def link_url(token: str) -> str:
    return f"{public_base()}?t={token}"


def spoken_code(code: str) -> str:
    return " ".join(code)


async def create_link(db, *, tenant_id, call_id, order_id, kind: str, now: Optional[datetime] = None) -> CareLink:
    """A fresh link for an order. Any earlier pending link of the same kind for the order is retired, so only the latest works."""
    now = now or datetime.utcnow()
    for old in (await db.execute(select(CareLink).where(
            CareLink.order_id == order_id, CareLink.kind == kind, CareLink.status == "pending"))).scalars().all():
        old.status = "expired"
    link = CareLink(tenant_id=tenant_id, call_id=call_id, order_id=order_id, kind=kind, token=secrets.token_urlsafe(16),
                    code=f"{secrets.randbelow(10 ** 6):06d}", status="pending", expires_at=now + timedelta(minutes=LINK_TTL_MINUTES))
    db.add(link)
    await db.flush()
    return link


async def find_link(db, token: str) -> Optional[CareLink]:
    if not token or len(token) > 64:
        return None
    return (await db.execute(select(CareLink).where(CareLink.token == token))).scalars().first()


def check_code(link: CareLink, code: str, now: Optional[datetime] = None) -> Optional[str]:
    """None when the code opens the link; otherwise what to tell the person. A wrong code counts toward the lock."""
    now = now or datetime.utcnow()
    if link.status == "done":
        return "This link has already been used."
    if link.status == "locked":
        return "Too many wrong codes. Ask the agent for a new link."
    if link.status == "expired" or now > link.expires_at:
        return "This link has expired. Ask the agent for a new one."
    if not secrets.compare_digest((code or "").strip(), link.code):
        link.attempts = (link.attempts or 0) + 1
        if link.attempts >= MAX_WRONG_CODES:
            link.status = "locked"
            return "Too many wrong codes. Ask the agent for a new link."
        return f"That code is not right. {MAX_WRONG_CODES - link.attempts} tries left."
    return None


def _order_public(order: MedicineOrder) -> dict:
    t = care.order_total(order.items_json or [])
    return {"reference": order.reference, "status": order.status, "amount": order.amount if order.amount is not None else t["total"],
            "currency": care.pharmacy()["currency"], "medicines": len(order.items_json or [])}


@public_router.get("/link")
async def link_info(t: str, db: AsyncSession = Depends(get_db)):
    """What the page shows before the code is typed: what kind of link it is and whether it still works. Nothing personal."""
    link = await find_link(db, t)
    if link is None:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    live = link.status == "pending" and datetime.utcnow() <= link.expires_at
    order = await db.get(MedicineOrder, link.order_id) if link.order_id else None
    return {"kind": link.kind, "status": link.status if live or link.status != "pending" else "expired",
            "expires_in": max(0, int((link.expires_at - datetime.utcnow()).total_seconds())),
            "order": _order_public(order) if (order is not None and link.kind == "payment") else None}


async def _announce(order: MedicineOrder, what: str) -> None:
    try:
        from .websocket_manager import dashboard_manager
        await dashboard_manager.broadcast({"type": "order_updated", "order_id": order.id, "what": what, "status": order.status,
                                           "call_id": order.call_id})
    except Exception:
        pass


@public_router.post("/prescription")
async def upload_prescription(t: str = Form(...), code: str = Form(...), prescription: UploadFile = File(...),
                              id_document: Optional[UploadFile] = File(None), db: AsyncSession = Depends(get_db)):
    link = await find_link(db, t)
    if link is None or link.kind != "prescription":
        raise HTTPException(status_code=404, detail="This link is not valid.")
    problem = check_code(link, code)
    if problem:
        await db.commit()                                   # keep the wrong-code count
        raise HTTPException(status_code=403, detail=problem)
    order = await db.get(MedicineOrder, link.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    data = await prescription.read()
    from .prescription_pdf import PdfParseError, extract_prescription_from_file
    try:
        read = await asyncio.to_thread(extract_prescription_from_file, data, (prescription.content_type or "").lower())
    except PdfParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    items = []
    for it in read.get("items") or []:
        entry = care.catalogue_entry(it.get("drug_name"), it.get("form"), it.get("strength"))
        items.append({**it, "price": entry["price"] if entry else None, "about": entry["about"] if entry else None,
                      "catalogue_name": entry["name"] if entry else None, "quantity": 1})
    if not items:
        raise HTTPException(status_code=422, detail="No medicines could be read. Please send a clearer, well-lit photo of the whole prescription.")
    folder = os.path.join(UPLOAD_DIR, str(order.id))
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, f"prescription-{uuid.uuid4().hex[:8]}"), "wb") as f:
        f.write(data)
    if id_document is not None and id_document.filename:
        idata = await id_document.read()
        if idata:
            with open(os.path.join(folder, f"id-{uuid.uuid4().hex[:8]}"), "wb") as f:
                f.write(idata)
            order.id_document_received = True
    order.items_json = items
    order.status = "prescription_received"
    order.amount = care.order_total(items)["total"]
    link.status, link.used_at, link.result_json = "done", datetime.utcnow(), {"medicines": len(items)}
    await db.commit()
    await _announce(order, "prescription")
    return {"ok": True, "medicines": len(items), "id_received": bool(order.id_document_received)}


class PayBody(BaseModel):
    t: str
    code: str


@public_router.post("/pay")
async def pay(body: PayBody, db: AsyncSession = Depends(get_db)):
    """DEMO payment: marks the order paid. No card is charged."""
    link = await find_link(db, body.t)
    if link is None or link.kind != "payment":
        raise HTTPException(status_code=404, detail="This link is not valid.")
    problem = check_code(link, body.code)
    if problem:
        await db.commit()
        raise HTTPException(status_code=403, detail=problem)
    order = await db.get(MedicineOrder, link.order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    order.payment_status, order.status = "paid", "paid"
    link.status, link.used_at = "done", datetime.utcnow()
    await db.commit()
    await _announce(order, "payment")
    return {"ok": True, "reference": order.reference}
