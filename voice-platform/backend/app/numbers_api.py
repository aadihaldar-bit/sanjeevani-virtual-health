"""Phone number inventory API."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import numbers as nums
from .access import User, audit, current_user, require_tenant_admin, require_super_admin
from .database import get_db
from .models import NumberAllocationEvent, PhoneNumber, Workflow

router = APIRouter(prefix="/api", tags=["numbers"])


class NumberCreate(BaseModel):
    number: str = Field(max_length=40)
    carrier: str = "smartflo"
    direction: str = "both"
    label: Optional[str] = Field(default=None, max_length=255)
    tenant_id: Optional[int] = None


class CsvImport(BaseModel):
    csv: str = Field(max_length=200_000)


class AllocateBody(BaseModel):
    tenant_id: int


class AssignBody(BaseModel):
    workflow_id: Optional[int] = None


class WorkflowOrgBody(BaseModel):
    tenant_id: Optional[int] = None


def _fail(exc: nums.NumberError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


async def _get(db: AsyncSession, number_id: int, user: User) -> PhoneNumber:
    row = await db.get(PhoneNumber, number_id)
    if not row or (not user.is_super_admin and row.tenant_id != user.tenant_id):
        raise HTTPException(status_code=404, detail="Number not found.")      # other organizations' numbers do not exist for you
    return row


@router.get("/numbers")
async def list_numbers(tenant_id: Optional[int] = Query(default=None), unallocated: bool = False,
                       user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    q = select(PhoneNumber).order_by(PhoneNumber.number)
    if user.is_super_admin:
        if tenant_id is not None:
            q = q.where(PhoneNumber.tenant_id == tenant_id)
        if unallocated:
            q = q.where(PhoneNumber.tenant_id.is_(None))
    else:
        if user.tenant_id is None:
            return {"numbers": []}
        q = q.where(PhoneNumber.tenant_id == user.tenant_id)
    return {"numbers": [n.to_dict() for n in (await db.execute(q)).scalars().all()]}


@router.post("/numbers", status_code=201)
async def add_number(body: NumberCreate, user: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)):
    try:
        row = await nums.add_number(db, user.email, body.number, body.carrier, body.direction, body.label)
        if body.tenant_id is not None:
            await nums.allocate_to_org(db, user.email, row, body.tenant_id)
    except nums.NumberError as exc:
        await db.rollback()
        _fail(exc)
    await audit(db, user.email, "number.add", tenant_id=row.tenant_id, target=row.number)
    await db.commit()
    return row.to_dict()


@router.post("/numbers/import")
async def import_numbers(body: CsvImport, user: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)):
    result = await nums.import_csv(db, user.email, body.csv)
    await audit(db, user.email, "number.import", detail={"accepted": len(result["accepted"]), "rejected": len(result["rejected"])})
    await db.commit()
    return result


@router.post("/numbers/{number_id}/allocate")
async def allocate(number_id: int, body: AllocateBody, user: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)):
    row = await _get(db, number_id, user)
    try:
        await nums.allocate_to_org(db, user.email, row, body.tenant_id)
    except nums.NumberError as exc:
        await db.rollback()
        _fail(exc)
    await audit(db, user.email, "number.allocate", tenant_id=body.tenant_id, target=row.number)
    await db.commit()
    return row.to_dict()


@router.post("/numbers/{number_id}/release")
async def release(number_id: int, user: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)):
    row = await _get(db, number_id, user)
    previous = row.tenant_id
    await nums.release(db, user.email, row)
    await audit(db, user.email, "number.release", tenant_id=previous, target=row.number)
    await db.commit()
    return row.to_dict()


@router.post("/numbers/{number_id}/assign")
async def assign(number_id: int, body: AssignBody, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    row = await _get(db, number_id, user)
    try:
        await nums.assign_to_agent(db, user.email, row, body.workflow_id)
    except nums.NumberError as exc:
        await db.rollback()
        _fail(exc)
    await audit(db, user.email, "number.assign", tenant_id=row.tenant_id, target=row.number, detail={"workflow_id": body.workflow_id})
    await db.commit()
    return row.to_dict()


@router.get("/numbers/{number_id}/history")
async def history(number_id: int, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    row = await _get(db, number_id, user)
    events = (await db.execute(select(NumberAllocationEvent).where(NumberAllocationEvent.number_id == row.id)
                               .order_by(NumberAllocationEvent.id.desc()))).scalars().all()
    return {"number": row.number, "events": [e.to_dict() for e in events]}


@router.get("/tenant-workflows")
async def workflows_for_assignment(tenant_id: Optional[int] = Query(default=None), user: User = Depends(require_tenant_admin),
                                   db: AsyncSession = Depends(get_db)):
    """Agents a number in this organization may be assigned to: the organization's own plus shared ones."""
    target = tenant_id if user.is_super_admin else user.tenant_id
    q = select(Workflow).order_by(Workflow.name)
    if target is not None:
        q = q.where((Workflow.tenant_id == target) | (Workflow.tenant_id.is_(None)))
    return {"workflows": [{"id": w.id, "name": w.name, "tenant_id": w.tenant_id} for w in (await db.execute(q)).scalars().all()]}


@router.put("/workflows/{workflow_id}/tenant")
async def set_workflow_org(workflow_id: int, body: WorkflowOrgBody, user: User = Depends(require_super_admin),
                           db: AsyncSession = Depends(get_db)):
    """Give an agent to one organization, or share it with everyone (tenant_id null)."""
    workflow = await db.get(Workflow, workflow_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    clashing = []
    if body.tenant_id is not None:                      # numbers of other organizations must not point at an agent that is now private
        clashing = (await db.execute(select(PhoneNumber).where(
            PhoneNumber.workflow_id == workflow_id, PhoneNumber.tenant_id != body.tenant_id))).scalars().all()
    if clashing:
        raise HTTPException(status_code=409, detail="Numbers of other organizations are assigned to this agent; unassign them first.")
    workflow.tenant_id = body.tenant_id
    await audit(db, user.email, "workflow.set_org", tenant_id=body.tenant_id, target=str(workflow_id))
    await db.commit()
    return {"id": workflow.id, "tenant_id": workflow.tenant_id}
