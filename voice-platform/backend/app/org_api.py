"""Organizations, members and the admin audit trail."""
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Rate limit for the storage-bucket API ─────────────────────────────────────────────────────────
# Each bucket op calls the cloud (storage.buckets.create / write check), which is quota-limited and
# costs money, so cap how often it can be invoked. Per-actor sliding window, env-tunable.
_BUCKET_HITS: Dict[str, List[float]] = {}


def _rate_limit(key: str, max_n: int, window_s: int, what: str = "requests") -> None:
    now = time.time()
    arr = [t for t in _BUCKET_HITS.get(key, []) if now - t < window_s]
    if len(arr) >= max_n:
        raise HTTPException(status_code=429,
                            detail=f"Too many {what}. Please wait about a minute and try again.")
    arr.append(now)
    _BUCKET_HITS[key] = arr


def _bucket_rate_limit(actor: str) -> None:
    _rate_limit(f"bucket:{actor or 'unknown'}",
                int(os.getenv("BUCKET_API_RATE_MAX", "5")),
                int(os.getenv("BUCKET_API_RATE_WINDOW_S", "60")),
                what="storage-bucket operations")

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .access import (MEMBER, TENANT_ADMIN, SUPER_ADMIN, User, audit, current_user, require_tenant_admin,
                     require_super_admin, scope_tenant)
from .database import get_db
from .models import AdminAuditEntry, APIClient, Tenant, TenantMembership

router = APIRouter(prefix="/api", tags=["organizations"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MEMBER_ROLES = (TENANT_ADMIN, "manager", MEMBER, "viewer")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "org"


_ROLE_LABELS = {"tenant_admin": "Administrator", "manager": "Manager",
                "operator": "Member", "viewer": "Viewer"}


def _dashboard_url() -> str:
    return os.getenv("DASHBOARD_URL", "https://voicebot-agent.stldigitaltech.com").rstrip("/")


def _invite_email_html(dash: str, org_name: str, role_label: str, invited_by: str, to_email: str) -> str:
    """Branded, email-client-safe invite (inline styles only)."""
    return f"""\
<!DOCTYPE html><html lang="en"><body style="margin:0;padding:0;background:#eef2f7;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7;padding:28px 12px;"><tr><td align="center">
    <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(16,42,90,.10);">
      <tr><td style="background:linear-gradient(135deg,#1b4db3,#00a39c);background-color:#1b4db3;padding:26px 32px;">
        <div style="color:#fff;font-size:20px;font-weight:700;">STL&nbsp;Voice&nbsp;Agent</div>
        <div style="color:#d6e4ff;font-size:12px;margin-top:3px;">Organization invitation</div>
      </td></tr>
      <tr><td style="padding:30px 32px 8px 32px;">
        <h1 style="margin:0 0 10px 0;font-size:20px;color:#0f2a4a;">You've been invited</h1>
        <p style="margin:0 0 20px 0;font-size:14px;line-height:1.6;color:#44556b;">
          <strong>{invited_by}</strong> has invited you to join <strong>{org_name}</strong> on the STL Voice Agent
          console as a <strong>{role_label}</strong>.
        </p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
          <a href="{dash}" target="_blank" style="display:inline-block;background:#1b4db3;color:#fff;text-decoration:none;font-size:16px;font-weight:600;padding:14px 38px;border-radius:9px;">Open the console &rarr;</a>
        </td></tr></table>
        <p style="margin:18px 0 0 0;font-size:13px;line-height:1.6;color:#44556b;text-align:center;">
          Sign in with <strong>Google</strong> using this exact address:<br>
          <span style="color:#1b4db3;font-weight:600;">{to_email}</span>
        </p>
      </td></tr>
      <tr><td style="padding:16px 32px 28px 32px;">
        <p style="margin:0;font-size:11px;line-height:1.6;color:#98a6ba;">
          If you weren't expecting this invitation, you can safely ignore this email.<br>&copy; STL Voice Agent
        </p>
      </td></tr>
    </table>
  </td></tr></table>
</body></html>"""


async def _send_invite_email(to_email: str, org_name: str, role: str, invited_by: str) -> bool:
    """Email a member their invitation. Best-effort — never raises, never blocks the invite."""
    from . import emailer
    if not emailer.email_configured():
        logger.info("[invite] SMTP not configured; no invite email sent to %s", to_email)
        return False
    dash = _dashboard_url()
    role_label = _ROLE_LABELS.get(role, (role or "Member").replace("_", " ").title())
    subject = f"You've been invited to {org_name} on STL Voice Agent"
    body = (f"Hello,\n\n{invited_by} has invited you to join {org_name} on the STL Voice Agent console "
            f"as a {role_label}.\n\n"
            f"To accept, open {dash} and sign in with Google using this email address ({to_email}).\n\n"
            f"If you weren't expecting this, you can ignore this email.\n\n— STL Voice Agent")
    html = _invite_email_html(dash, org_name, role_label, invited_by, to_email)
    return await emailer.send(to_email, subject, body, html=html)


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    plan: Optional[str] = Field(default="standard", max_length=32)


class OrgUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    status: Optional[str] = Field(default=None, pattern="^(active|suspended)$")
    plan: Optional[str] = Field(default=None, max_length=32)


class MemberCreate(BaseModel):
    email: str = Field(max_length=255)
    role: str = Field(default=MEMBER, pattern="^(tenant_admin|manager|operator|viewer)$")


class MemberUpdate(BaseModel):
    role: str = Field(pattern="^(tenant_admin|manager|operator|viewer)$")


@router.get("/me")
async def me(user: User = Depends(current_user)):
    """Who am I and what may I see: the console uses this to decide which screens to show."""
    caps = {
        "manage_organizations": user.is_super_admin,
        "manage_members": user.role in (SUPER_ADMIN, TENANT_ADMIN),
        "manage_numbers": user.role in (SUPER_ADMIN, TENANT_ADMIN),
        "allocate_numbers": user.is_super_admin,
        "manage_external_models": user.role in (SUPER_ADMIN, TENANT_ADMIN),
        "manage_vector_stores": user.role in (SUPER_ADMIN, TENANT_ADMIN),
        "operate_calls": user.is_staff or user.role in (SUPER_ADMIN, TENANT_ADMIN, "manager", "operator"),
        "manage_agents_and_keys": user.is_staff or user.role in (SUPER_ADMIN, TENANT_ADMIN),
    }
    return {**user.to_dict(), "capabilities": caps}


@router.get("/tenants")
async def list_orgs(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    q = select(Tenant).order_by(Tenant.name)
    if not user.is_super_admin:
        if user.tenant_id is None:
            return {"orgs": []}
        q = q.where(Tenant.id == user.tenant_id)
    orgs = (await db.execute(q)).scalars().all()
    counts = dict((await db.execute(select(TenantMembership.tenant_id, func.count(TenantMembership.id)).group_by(TenantMembership.tenant_id))).all())
    return {"orgs": [{**o.to_dict(), "member_count": counts.get(o.id, 0)} for o in orgs]}


@router.post("/tenants", status_code=201)
async def create_org(body: OrgCreate, user: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)):
    name = body.name.strip()
    slug, n = slugify(name), 1
    while (await db.execute(select(Tenant).where((Tenant.slug == slug) | (Tenant.name == name)))).scalars().first():
        existing = (await db.execute(select(Tenant).where(Tenant.name == name))).scalars().first()
        if existing:
            raise HTTPException(status_code=409, detail="An organization with that name already exists.")
        n += 1
        slug = f"{slugify(name)}-{n}"
    org = Tenant(name=name, slug=slug, plan=body.plan or "standard", status="active", created_at=datetime.utcnow())
    db.add(org)
    await db.flush()
    await audit(db, user.email, "org.create", tenant_id=org.id, target=name)
    await db.commit()
    from . import tenant_storage
    tenant_storage.start_provisioning(org.id, actor=user.email)      # the bucket is made in the background
    return org.to_dict()


@router.patch("/tenants/{tenant_id}")
async def update_org(tenant_id: int, body: OrgUpdate, user: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)):
    org = await db.get(Tenant, tenant_id)
    if not org:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    if org.is_system and body.status is not None and body.status != "active":
        raise HTTPException(status_code=409, detail="The platform's own tenant cannot be suspended.")
    changed = {}
    for field in ("name", "status", "plan"):
        value = getattr(body, field)
        if value is not None and value != getattr(org, field):
            changed[field] = value
            setattr(org, field, value.strip() if isinstance(value, str) else value)
    if changed:
        await audit(db, user.email, "org.update", tenant_id=org.id, target=org.name, detail=changed)
    await db.commit()
    return org.to_dict()


class HandoverBody(BaseModel):
    number: Optional[str] = Field(default=None, max_length=32)


@router.put("/tenants/{tenant_id}/handover")
async def set_handover_number(tenant_id: int, body: HandoverBody, user: User = Depends(require_tenant_admin),
                              db: AsyncSession = Depends(get_db)):
    """The number this company's callers are put through to when they ask for a person (empty to clear it)."""
    scope_tenant(user, tenant_id)
    org = await _org_or_404(db, tenant_id)
    from .transfer import clean_number
    try:
        number = clean_number(body.number)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    settings = dict(org.settings_json or {})
    if number:
        settings["handover_number"] = number
    else:
        settings.pop("handover_number", None)
    org.settings_json = settings                                     # a new dict so the change is saved
    await audit(db, user.email, "tenant.handover_number", tenant_id=tenant_id, target=number or "cleared")
    await db.commit()
    return org.to_dict()


# The zones a company can choose for its calling hours and the times its console shows.
TIMEZONES = {
    "Asia/Dubai": "Gulf (UAE), UTC+4",
    "Asia/Muscat": "Oman, UTC+4",
    "Asia/Riyadh": "Saudi Arabia, UTC+3",
    "Asia/Qatar": "Qatar, UTC+3",
    "Asia/Kuwait": "Kuwait, UTC+3",
    "Asia/Bahrain": "Bahrain, UTC+3",
    "Asia/Kolkata": "India, UTC+5:30",
    "UTC": "UTC",
}


class TimezoneBody(BaseModel):
    timezone: str = Field(max_length=64)


class CallingWindowBody(BaseModel):
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


@router.put("/tenants/{tenant_id}/calling-hours")
async def set_calling_hours(tenant_id: int, body: CallingWindowBody, user: User = Depends(require_tenant_admin),
                            db: AsyncSession = Depends(get_db)):
    """The hours (in the company's time zone) when follow-up calls may go out. Applies to calls scheduled from now on."""
    scope_tenant(user, tenant_id)
    org = await _org_or_404(db, tenant_id)
    if body.start >= body.end:
        raise HTTPException(status_code=422, detail="The end time must be after the start time.")
    org.settings_json = {**(org.settings_json or {}), "calling_window": {"start": body.start, "end": body.end}}
    await audit(db, user.email, "tenant.calling_hours", tenant_id=tenant_id, target=f"{body.start}-{body.end}")
    await db.commit()
    return org.to_dict()


@router.get("/timezones")
async def list_timezones():
    return {"timezones": [{"id": k, "label": v} for k, v in TIMEZONES.items()]}


@router.put("/tenants/{tenant_id}/timezone")
async def set_timezone(tenant_id: int, body: TimezoneBody, user: User = Depends(require_tenant_admin),
                       db: AsyncSession = Depends(get_db)):
    """The company's time zone: its follow-up calls only go out inside the calling hours of this zone."""
    scope_tenant(user, tenant_id)
    org = await _org_or_404(db, tenant_id)
    if body.timezone not in TIMEZONES:
        raise HTTPException(status_code=422, detail="Choose one of the listed time zones.")
    org.timezone = body.timezone
    await audit(db, user.email, "tenant.timezone", tenant_id=tenant_id, target=body.timezone)
    await db.commit()
    return org.to_dict()


class BucketBody(BaseModel):
    bucket: str = Field(max_length=63)


class BucketRegionBody(BaseModel):
    region: str = Field(max_length=32)


@router.get("/bucket-regions")
async def list_bucket_regions():
    from . import tenant_storage
    return {"regions": [{"id": k, "label": v} for k, v in tenant_storage.BUCKET_REGIONS.items()]}


@router.put("/tenants/{tenant_id}/bucket-region")
async def set_bucket_region(tenant_id: int, body: BucketRegionBody, user: User = Depends(require_super_admin),
                            db: AsyncSession = Depends(get_db)):
    """Where this company's bucket is created (data residency). Only matters until the bucket exists."""
    from . import tenant_storage
    org = await _org_or_404(db, tenant_id)
    if body.region not in tenant_storage.BUCKET_REGIONS:
        raise HTTPException(status_code=422, detail="Choose one of the listed regions.")
    if org.gcs_bucket:
        raise HTTPException(status_code=409, detail="This company already has a bucket; a bucket cannot be moved to another region.")
    org.settings_json = {**(org.settings_json or {}), "bucket_location": body.region}
    await audit(db, user.email, "tenant.bucket_region", tenant_id=tenant_id, target=body.region)
    await db.commit()
    return org.to_dict()


@router.post("/tenants/{tenant_id}/bucket")
async def create_tenant_bucket(tenant_id: int, user: User = Depends(require_super_admin), db: AsyncSession = Depends(get_db)):
    """Create (or retry creating) the tenant's storage bucket now and report how it went."""
    _bucket_rate_limit(user.email)
    org = await db.get(Tenant, tenant_id)
    if not org:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    from . import tenant_storage
    if org.is_system:
        raise HTTPException(status_code=409, detail="The platform's own tenant uses the shared bucket.")
    if not tenant_storage.auto_create_enabled():
        raise HTTPException(status_code=409, detail="Automatic bucket creation is turned off on this server (AUTO_CREATE_TENANT_BUCKETS).")
    result = await tenant_storage.provision_for_tenant(tenant_id, actor=user.email)
    return {"storage": result}


@router.put("/tenants/{tenant_id}/bucket")
async def register_tenant_bucket(tenant_id: int, body: BucketBody, user: User = Depends(require_super_admin),
                                 db: AsyncSession = Depends(get_db)):
    """Use a bucket that already exists (after checking the server can write to it)."""
    _bucket_rate_limit(user.email)
    org = await db.get(Tenant, tenant_id)
    if not org:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    if org.is_system:
        raise HTTPException(status_code=409, detail="The platform's own tenant uses the shared bucket.")
    from . import tenant_storage
    try:
        return {"storage": await tenant_storage.register_existing_bucket(tenant_id, body.bucket, user.email)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


async def _org_or_404(db: AsyncSession, tenant_id: int) -> Tenant:
    org = await db.get(Tenant, tenant_id)
    if not org:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return org


@router.get("/tenants/{tenant_id}/members")
async def list_members(tenant_id: int, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    scope_tenant(user, tenant_id)
    await _org_or_404(db, tenant_id)
    rows = (await db.execute(select(TenantMembership).where(TenantMembership.tenant_id == tenant_id).order_by(TenantMembership.email))).scalars().all()
    return {"members": [m.to_dict() for m in rows]}


@router.post("/tenants/{tenant_id}/members", status_code=201)
async def add_member(tenant_id: int, body: MemberCreate, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    scope_tenant(user, tenant_id)
    org = await _org_or_404(db, tenant_id)
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    from .access import platform_admin_emails
    if email in platform_admin_emails():
        raise HTTPException(status_code=409, detail="That person is a platform administrator and needs no organization.")
    existing = (await db.execute(select(TenantMembership).where(TenantMembership.email == email))).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="That person already belongs to an organization.")
    member = TenantMembership(tenant_id=tenant_id, email=email, role=body.role, status="invited", invited_by=user.email,
                       created_at=datetime.utcnow())
    db.add(member)
    await db.flush()
    await audit(db, user.email, "member.invite", tenant_id=tenant_id, target=email, detail={"role": body.role})
    await db.commit()
    # Email the invitation (best-effort; the membership is already saved regardless of mail delivery).
    org_name = getattr(org, "name", None) or "your organization"
    emailed = await _send_invite_email(email, org_name, body.role, user.email)
    result = member.to_dict()
    result["email_sent"] = bool(emailed)
    return result


async def _member_in_org(db: AsyncSession, tenant_id: int, member_id: int) -> TenantMembership:
    member = await db.get(TenantMembership, member_id)
    if not member or member.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Member not found.")
    return member


async def _other_admins(db: AsyncSession, tenant_id: int, exclude_id: int) -> int:
    return (await db.execute(select(func.count(TenantMembership.id)).where(
        TenantMembership.tenant_id == tenant_id, TenantMembership.role == TENANT_ADMIN, TenantMembership.id != exclude_id))).scalar() or 0


@router.patch("/tenants/{tenant_id}/members/{member_id}")
async def change_member_role(tenant_id: int, member_id: int, body: MemberUpdate,
                             user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    scope_tenant(user, tenant_id)
    member = await _member_in_org(db, tenant_id, member_id)
    if member.role == SUPER_ADMIN:
        raise HTTPException(status_code=409, detail="Super administrators are managed in the server settings.")
    if member.role == TENANT_ADMIN and body.role != TENANT_ADMIN and await _other_admins(db, tenant_id, member.id) == 0:
        raise HTTPException(status_code=409, detail="An organization must keep at least one administrator.")
    if member.role != body.role:
        await audit(db, user.email, "member.role", tenant_id=tenant_id, target=member.email, detail={"from": member.role, "to": body.role})
        member.role = body.role
    await db.commit()
    return member.to_dict()


@router.delete("/tenants/{tenant_id}/members/{member_id}")
async def remove_member(tenant_id: int, member_id: int, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    scope_tenant(user, tenant_id)
    member = await _member_in_org(db, tenant_id, member_id)
    if member.role == SUPER_ADMIN:
        raise HTTPException(status_code=409, detail="Super administrators are managed in the server settings.")
    if member.role == TENANT_ADMIN and await _other_admins(db, tenant_id, member.id) == 0:
        raise HTTPException(status_code=409, detail="An organization must keep at least one administrator.")
    await audit(db, user.email, "member.remove", tenant_id=tenant_id, target=member.email)
    await db.delete(member)
    await db.commit()
    return {"removed": True}


@router.get("/admin/audit")
async def audit_log(tenant_id: Optional[int] = Query(default=None), limit: int = Query(default=100, ge=1, le=500),
                    user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    q = select(AdminAuditEntry).order_by(AdminAuditEntry.id.desc()).limit(limit)
    if user.is_super_admin:
        if tenant_id is not None:
            q = q.where(AdminAuditEntry.tenant_id == tenant_id)
    else:
        q = q.where(AdminAuditEntry.tenant_id == scope_tenant(user, tenant_id))
    return {"entries": [e.to_dict() for e in (await db.execute(q)).scalars().all()]}


class ClientOrgBody(BaseModel):
    tenant_id: Optional[int] = None


@router.get("/admin/api-clients-by-tenant")
async def api_clients_by_org(tenant_id: Optional[int] = Query(default=None), user: User = Depends(require_tenant_admin),
                             db: AsyncSession = Depends(get_db)):
    """Outbound API clients and the organization each belongs to (platform admins see all; others their own)."""
    q = select(APIClient).order_by(APIClient.client_name)
    q = q.where(APIClient.tenant_id == (tenant_id if user.is_super_admin and tenant_id is not None else scope_tenant(user, tenant_id)))         if not (user.is_super_admin and tenant_id is None) else q
    rows = (await db.execute(q)).scalars().all()
    return {"clients": [{"id": c.id, "name": c.client_name, "company": c.client_company, "tenant_id": c.tenant_id,
                         "active": bool(c.is_active), "plan": c.plan} for c in rows]}


@router.put("/admin/api-clients/{client_id}/tenant")
async def set_client_org(client_id: int, body: ClientOrgBody, user: User = Depends(require_super_admin),
                         db: AsyncSession = Depends(get_db)):
    """Attach an outbound API client to an organization: its calls then go out from that organization's numbers."""
    client = await db.get(APIClient, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="API client not found.")
    if body.tenant_id is not None:
        await _org_or_404(db, body.tenant_id)
    client.tenant_id = body.tenant_id
    await audit(db, user.email, "api_client.set_org", tenant_id=body.tenant_id, target=client.client_name)
    await db.commit()
    return {"id": client.id, "tenant_id": client.tenant_id}
