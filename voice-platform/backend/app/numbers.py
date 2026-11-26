"""Phone number inventory: validation, allocation to an organization, assignment to an agent, and inbound routing.

Rules
  * a number belongs to at most one organization, and inside it to at most one agent (workflow);
  * only platform admins move numbers between organizations; organization admins assign their own numbers to their
    own agents (a workflow shared by every organization, tenant_id NULL, may be used by anyone);
  * moving a number to another organization clears its agent assignment;
  * every change is written to the allocation history.
"""
import csv
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import NumberAllocationEvent, Tenant, PhoneNumber, Workflow

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
CARRIERS = ("smartflo", "twilio", "other")
DIRECTIONS = ("inbound", "outbound", "both")


class NumberError(ValueError):
    """A rule was broken; the message is safe to show to the operator."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalize_number(raw: str, default_country: Optional[str] = None) -> str:
    """'080 6987 9488', '+91 80698 79488' and '918069879488' all become E.164 when they can be made unambiguous."""
    text = (raw or "").strip()
    if not text:
        raise NumberError("A phone number is required")
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if text.startswith("00"):
        plus, digits = True, digits[2:]
    country = (default_country if default_country is not None else os.getenv("DEFAULT_COUNTRY_CODE", "91")).strip().lstrip("+")
    if not plus:
        if digits.startswith("0") and country:
            digits = country + digits.lstrip("0")
        elif country and len(digits) == 10:
            digits = country + digits
    number = "+" + digits
    if not E164.match(number):
        raise NumberError(f"'{raw}' is not a valid phone number (use international format, for example +918069879488)")
    return number


async def _record(db: AsyncSession, number: PhoneNumber, action: str, actor: str, *,
                  from_org: Optional[int] = None, to_org: Optional[int] = None,
                  from_wf: Optional[int] = None, to_wf: Optional[int] = None) -> None:
    db.add(NumberAllocationEvent(number_id=number.id, action=action, from_tenant_id=from_org, to_tenant_id=to_org,
                                 from_workflow_id=from_wf, to_workflow_id=to_wf, actor=actor, created_at=datetime.utcnow()))


async def add_number(db: AsyncSession, actor: str, raw: str, carrier: str = "smartflo", direction: str = "both",
                     label: Optional[str] = None) -> PhoneNumber:
    number = normalize_number(raw)
    carrier, direction = (carrier or "smartflo").lower(), (direction or "both").lower()
    if carrier not in CARRIERS:
        raise NumberError(f"Carrier must be one of: {', '.join(CARRIERS)}")
    if direction not in DIRECTIONS:
        raise NumberError(f"Direction must be one of: {', '.join(DIRECTIONS)}")
    if (await db.execute(select(PhoneNumber).where(PhoneNumber.number == number))).scalars().first():
        raise NumberError(f"{number} is already in the inventory", 409)
    row = PhoneNumber(number=number, carrier=carrier, direction=direction, label=(label or "").strip()[:255] or None,
                      created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(row)
    await db.flush()
    await _record(db, row, "added", actor)
    return row


async def import_csv(db: AsyncSession, actor: str, text: str) -> Dict[str, List]:
    """Columns: number[,carrier[,direction[,label]]]. A header row is optional. Bad rows are reported, not fatal."""
    accepted, rejected = [], []
    rows = list(csv.reader(io.StringIO(text.strip())))
    if rows and rows[0] and rows[0][0].strip().lower() in ("number", "phone", "phone_number"):
        rows = rows[1:]
    for line_no, row in enumerate(rows, start=1):
        if not row or not any(c.strip() for c in row):
            continue
        cells = [c.strip() for c in row] + [""] * 4
        try:
            async with db.begin_nested():
                created = await add_number(db, actor, cells[0], cells[1] or "smartflo", cells[2] or "both", cells[3] or None)
            accepted.append(created.number)
        except NumberError as exc:
            rejected.append({"row": line_no, "value": cells[0], "reason": str(exc)})
    return {"accepted": accepted, "rejected": rejected}


async def allocate_to_org(db: AsyncSession, actor: str, number: PhoneNumber, tenant_id: int) -> PhoneNumber:
    org = await db.get(Tenant, tenant_id)
    if not org:
        raise NumberError("Tenant not found", 404)
    if org.status != "active":
        raise NumberError("That organization is suspended")
    if number.tenant_id == tenant_id:
        return number
    previous_org, previous_wf = number.tenant_id, number.workflow_id
    number.tenant_id, number.workflow_id = tenant_id, None
    await _record(db, number, "allocated", actor, from_org=previous_org, to_org=tenant_id, from_wf=previous_wf)
    return number


async def release(db: AsyncSession, actor: str, number: PhoneNumber) -> PhoneNumber:
    if number.tenant_id is None:
        return number
    await _record(db, number, "released", actor, from_org=number.tenant_id, from_wf=number.workflow_id)
    number.tenant_id, number.workflow_id = None, None
    return number


async def assign_to_agent(db: AsyncSession, actor: str, number: PhoneNumber, workflow_id: Optional[int]) -> PhoneNumber:
    if number.tenant_id is None:
        raise NumberError("Allocate the number to an organization before assigning it to an agent")
    if workflow_id is None:
        if number.workflow_id is not None:
            await _record(db, number, "unassigned", actor, to_org=number.tenant_id, from_wf=number.workflow_id)
            number.workflow_id = None
        return number
    workflow = await db.get(Workflow, workflow_id)
    if not workflow:
        raise NumberError("Agent (workflow) not found", 404)
    if workflow.tenant_id is not None and workflow.tenant_id != number.tenant_id:
        raise NumberError("That agent belongs to a different organization")
    if number.workflow_id != workflow_id:
        await _record(db, number, "assigned", actor, to_org=number.tenant_id, from_wf=number.workflow_id, to_wf=workflow_id)
        number.workflow_id = workflow_id
    return number


@dataclass
class Route:
    found: bool
    number: Optional[str] = None
    tenant_id: Optional[int] = None
    workflow_id: Optional[int] = None
    org_suspended: bool = False


async def resolve_inbound(db: AsyncSession, dialled: Optional[str]) -> Route:
    """Which organization and agent should answer a call to this number? Unknown numbers return found=False so the
    caller keeps today's behaviour (the active workflow). A number given an agent reaches that agent, live or not; a number
    with none is answered by its organization's live agent."""
    if not dialled:
        return Route(found=False)
    try:
        number = normalize_number(dialled)
    except NumberError:
        return Route(found=False)
    row = (await db.execute(select(PhoneNumber).where(PhoneNumber.number == number))).scalars().first()
    if not row or row.direction == "outbound":
        return Route(found=False)
    suspended = False
    if row.tenant_id is not None:
        org = await db.get(Tenant, row.tenant_id)
        suspended = bool(org and org.status != "active")
    workflow_id = row.workflow_id
    if workflow_id is None and row.tenant_id is not None and not suspended:
        # left to its organization's default: its own live agent, never the platform's or another organization's
        workflow_id = (await db.execute(select(Workflow.id).where(Workflow.tenant_id == row.tenant_id, Workflow.is_active.is_(True))
                                        .order_by(Workflow.id))).scalars().first()
    return Route(found=True, number=row.number, tenant_id=row.tenant_id, workflow_id=workflow_id, org_suspended=suspended)


async def pick_caller_id(db: AsyncSession, tenant_id: Optional[int], workflow_id: Optional[int] = None) -> Optional[str]:
    """The caller ID for an outbound call: the agent's own number first, then any outbound-capable number of the
    organization. None means the organization has no outbound number."""
    if tenant_id is None:
        return None
    q = select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id, PhoneNumber.direction.in_(("outbound", "both")))
    rows = (await db.execute(q.order_by(PhoneNumber.id))).scalars().all()
    for row in rows:
        if workflow_id is not None and row.workflow_id == workflow_id:
            return row.number
    return rows[0].number if rows else None
