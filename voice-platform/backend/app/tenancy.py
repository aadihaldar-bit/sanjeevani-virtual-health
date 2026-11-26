"""
Multi-tenancy foundation.

Additive and backward-compatible: a single built-in "system" tenant (STL) owns all existing data,
and tenant scoping is INERT until MULTI_TENANT_ENABLED is turned on — so single-tenant behaviour is
byte-for-byte unchanged today. This module is the one place that decides, for a signed-in user, which
tenant they act in, whether they are a platform super-admin, and how to scope a query to a tenant.

Roles (TenantMembership.role): super_admin | tenant_admin | manager | operator | viewer.
Super-admins (SUPER_ADMIN_EMAILS) act across ALL tenants regardless of membership.
"""
from __future__ import annotations

import os
import re
from typing import Optional

SYSTEM_TENANT_SLUG = "stl"
SYSTEM_TENANT_NAME = os.getenv("SYSTEM_TENANT_NAME", "STL Digital")

# Master switch. While false, scope() is a no-op and every user resolves to the STL tenant, so the
# platform behaves exactly as it did before multi-tenancy. Flip to true only once per-endpoint
# scoping has been wired and verified.
def multi_tenant_enabled() -> bool:
    """Read on demand so the switch can be flipped (and tested) without re-importing the module."""
    return os.getenv("MULTI_TENANT_ENABLED", "false").strip().lower() == "true"


MULTI_TENANT_ENABLED = multi_tenant_enabled()          # import-time snapshot, kept for existing imports

# Platform owners with cross-tenant access (comma-separated Google sign-in emails). One setting, read on demand so a
# change (or a test) takes effect without re-importing the module.
_DEFAULT_SUPER_ADMINS = "platform.admin@stltech.in,ops.admin@stltech.in,support.admin@stltech.in"


def super_admin_emails() -> set:
    raw = os.getenv("SUPER_ADMIN_EMAILS")
    if raw is None:
        raw = os.getenv("PLATFORM_ADMIN_EMAILS", _DEFAULT_SUPER_ADMINS)      # old name still honoured
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


SUPER_ADMIN_EMAILS = super_admin_emails()          # import-time snapshot, kept for existing imports


_system_tenant: dict = {"id": None}


def set_system_tenant_id(tenant_id: Optional[int]) -> None:
    """Called at startup once the STL tenant exists, for code (the knowledge base) that has no database session."""
    _system_tenant["id"] = tenant_id


def system_tenant_id_cached() -> Optional[int]:
    return _system_tenant["id"]


def is_super_admin(email: Optional[str]) -> bool:
    return (email or "").strip().lower() in super_admin_emails()


async def system_tenant_id() -> Optional[int]:
    """id of the built-in STL tenant (or None before it is seeded)."""
    from sqlalchemy import select
    from .database import async_session
    from .models import Tenant
    async with async_session() as db:
        t = (await db.execute(select(Tenant).where(Tenant.slug == SYSTEM_TENANT_SLUG))).scalars().first()
        return t.id if t else None


async def resolve_tenant_id(email: Optional[str]) -> Optional[int]:
    """Which tenant this user acts in.

    - Single-tenant mode (MULTI_TENANT_ENABLED false) or a super-admin -> the STL tenant.
    - Otherwise -> the tenant from the user's membership, falling back to the STL tenant.
    """
    from sqlalchemy import select
    from .database import async_session
    from .models import Tenant, TenantMembership
    async with async_session() as db:
        sys_t = (await db.execute(select(Tenant).where(Tenant.slug == SYSTEM_TENANT_SLUG))).scalars().first()
        sys_id = sys_t.id if sys_t else None
        if not multi_tenant_enabled() or is_super_admin(email):
            return sys_id
        m = (await db.execute(select(TenantMembership).where(
            TenantMembership.email == (email or "").strip().lower()))).scalars().first()
        return m.tenant_id if m else sys_id


def scope(query, model, tenant_id: Optional[int]):
    """Apply tenant isolation to a SQLAlchemy select.

    INERT while MULTI_TENANT_ENABLED is false (returns the query unchanged), so adopting it in a
    router today changes nothing. Once enabled, it filters to the given tenant for any tenant-owned
    model (one that has a tenant_id column)."""
    if not multi_tenant_enabled() or tenant_id is None:
        return query
    if hasattr(model, "tenant_id"):
        return query.where(model.tenant_id == tenant_id)
    return query


async def tenant_context(user) -> dict:
    """Resolve {email, tenant_id, is_super_admin, role} for a signed-in user. Intended as a FastAPI
    dependency layered on require_auth, e.g.:

        from fastapi import Depends
        from .firebase_auth import require_auth
        async def ctx(u: dict = Depends(require_auth)): return await tenant_context(u)
    """
    email = (user or {}).get("email") if isinstance(user, dict) else None
    tid = await resolve_tenant_id(email)
    role = "super_admin" if is_super_admin(email) else None
    if role is None and tid is not None:
        from sqlalchemy import select
        from .database import async_session
        from .models import TenantMembership
        async with async_session() as db:
            m = (await db.execute(select(TenantMembership).where(
                TenantMembership.tenant_id == tid,
                TenantMembership.email == (email or "").strip().lower()))).scalars().first()
            role = m.role if m else "viewer"
    return {"email": email, "tenant_id": tid, "is_super_admin": is_super_admin(email), "role": role}


# --- request scope -----------------------------------------------------------------------------------------------------
#
# Every data route asks "whose data may this caller see?" through one object. With multi-tenancy off nothing is
# restricted. With it on, a super admin sees everything (or one tenant when they send X-Tenant-Id), and everyone else
# is held to their own tenant. A tenant the caller cannot see answers 404, never 403, so its existence is not revealed.

from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from .firebase_auth import require_auth

NO_TENANT = -1          # a scope that matches no row: used when the platform tenant is missing (fail closed)

# What each role may do on the data routes (calls, tickets, agents, knowledge, API keys ...). Writes are refused by
# default; the lists below are the only exceptions, so a route added later is safe until someone decides otherwise.
#   super_admin / tenant_admin / staff   everything inside their scope
#   manager / operator                   read, plus day-to-day work: end calls, tickets, the simulator
#   viewer                               read only
STAFF = "staff"                           # STL staff with no membership (the allow-list users who existed before tenants)
SUPER_ROLE = "super_admin"
ADMIN_ROLE = "tenant_admin"
PRIVILEGED_ROLES = (SUPER_ROLE, ADMIN_ROLE, STAFF)
OPERATOR_ROLES = ("manager", "operator")
KNOWN_ROLES = PRIVILEGED_ROLES + OPERATOR_ROLES + ("viewer",)

_READ_METHODS = ("GET", "HEAD", "OPTIONS")
_OPERATE_WRITES = re.compile(r"^/api/(calls/(\d+/end|clear-stale)|(api/)?tickets/create|simulator/(chat|voice-turn|end-call)|stt/transcribe|tts/synthesize)/?$")
_READ_ONLY_POSTS = re.compile(r"^/api/knowledge/search/?$")          # a search is a read even though it is a POST
_ADMIN_ONLY_READS = re.compile(r"^/api/admin/api-clients")           # API key listings are for administrators


def role_denial(role: str, method: str, path: str) -> Optional[str]:
    """Why this role may not make this request on a data route, or None when it may."""
    if role in PRIVILEGED_ROLES:
        return None
    if method.upper() in _READ_METHODS:
        return "Only an administrator can do that." if _ADMIN_ONLY_READS.match(path) else None
    if _READ_ONLY_POSTS.match(path):
        return None
    if role in OPERATOR_ROLES and _OPERATE_WRITES.match(path):
        return None
    return "Only an administrator can do that." if role in OPERATOR_ROLES else "Your role is read-only."


@dataclass(frozen=True)
class TenantScope:
    tenant_id: Optional[int] = None          # None = unrestricted
    email: str = ""
    is_super_admin: bool = False
    role: str = STAFF                        # unrestricted work (the platform's own, tests) counts as staff

    @property
    def restricted(self) -> bool:
        return self.tenant_id is not None

    def apply(self, query, model):
        """Limit a select to this scope's tenant (no-op when unrestricted)."""
        if self.tenant_id is None or not hasattr(model, "tenant_id"):
            return query
        return query.where(model.tenant_id == self.tenant_id)

    def owns(self, row) -> bool:
        return self.tenant_id is None or getattr(row, "tenant_id", None) == self.tenant_id

    def require(self, row, what: str = "Not found"):
        """The row if this scope may see it, otherwise a 404 (same answer as a row that does not exist)."""
        if row is None or not self.owns(row):
            raise HTTPException(status_code=404, detail=what)
        return row

    def for_new_row(self) -> Optional[int]:
        """The tenant a newly created row belongs to (None lets the database default apply)."""
        return self.tenant_id


UNRESTRICTED = TenantScope()


async def resolve_scope(auth: dict, db, requested_tenant: Optional[str] = None) -> TenantScope:
    from sqlalchemy import select
    from .models import Tenant, TenantMembership

    email = str((auth or {}).get("email") or "").strip().lower()
    super_admin = bool((auth or {}).get("dev_auth_disabled")) or is_super_admin(email)
    enabled = multi_tenant_enabled()
    if super_admin:
        if enabled and requested_tenant and str(requested_tenant).strip().isdigit():
            tenant = await db.get(Tenant, int(requested_tenant))
            if not tenant:
                raise HTTPException(status_code=404, detail="Tenant not found.")
            return TenantScope(tenant.id, email, True, SUPER_ROLE)
        return TenantScope(None, email, True, SUPER_ROLE)
    row = (await db.execute(
        select(TenantMembership, Tenant).join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.email == email))).first()
    if row:
        member, tenant = row
        if enabled and tenant.status == "suspended":
            raise HTTPException(status_code=403, detail="Your organization is suspended.")
        role = member.role if member.role in KNOWN_ROLES else "viewer"       # an explicit membership always sets the role
        return TenantScope(tenant.id if enabled else None, email, False, role)
    if not enabled:
        return TenantScope(None, email, False, STAFF)
    # Signed in through the staff allow-list without a membership: they are STL staff, so STL's data.
    system = (await db.execute(select(Tenant.id).where(Tenant.slug == SYSTEM_TENANT_SLUG))).scalar()
    return TenantScope(system if system is not None else NO_TENANT, email, False, STAFF)


async def membership_role(email: str) -> Optional[str]:
    """The role an explicit membership gives this person, or None when they have none."""
    from sqlalchemy import select
    from .database import async_session
    from .models import TenantMembership
    async with async_session() as db:
        return (await db.execute(select(TenantMembership.role).where(TenantMembership.email == (email or "").strip().lower()))).scalar()


# The scope of the request being served. Set once by `tenant_scope`; the database filter below reads it, so every
# read of tenant-owned data is limited without each route having to remember to do it. Work that is not a user
# request (the phone-call pipeline, background jobs) has no scope and is not limited: it acts for the platform.
current_scope: ContextVar[Optional[TenantScope]] = ContextVar("current_scope", default=None)


def active_scope() -> Optional[TenantScope]:
    return current_scope.get()


def request_scope() -> TenantScope:
    """The scope `tenant_scope` set for this request; unrestricted outside a request (the platform's own work)."""
    return current_scope.get() or UNRESTRICTED


async def tenant_scope(request: Request, auth: dict = Depends(require_auth)):
    """Router-level dependency for every route that serves tenant data.

    With multi-tenancy off, STL staff are unrestricted and a tenant's own users are refused (they have no business
    reading the platform's whole data set). With it on, everyone is held to their tenant."""
    from .database import async_session
    async with async_session() as db:
        scope_ = await resolve_scope(auth, db, request.headers.get("x-tenant-id"))
    if not multi_tenant_enabled() and not (auth.get("is_staff") or scope_.is_super_admin):
        raise HTTPException(status_code=403, detail="Not available to this account.")
    denial = role_denial(scope_.role, request.method, request.url.path)
    if denial:
        raise HTTPException(status_code=403, detail=denial)
    token = current_scope.set(scope_)
    try:
        yield scope_
    finally:                                      # never let one request's scope outlive it
        try:
            current_scope.reset(token)
        except ValueError:
            current_scope.set(None)


async def require_staff(auth: dict = Depends(require_auth)) -> dict:
    """Platform-level routes (licence, telephony sync, diagnostics, provider tests): STL staff and super admins only."""
    if auth.get("dev_auth_disabled") or is_super_admin(auth.get("email")):
        return auth
    if auth.get("is_staff") and await membership_role(auth.get("email")) is None:     # a membership makes them a tenant user
        return auth
    raise HTTPException(status_code=403, detail="Only platform staff can do that.")


def _tenant_criteria(tenant_id: int):
    """(model, criterion) for each kind of row a tenant owns, directly or through its call."""
    from sqlalchemy import select
    from . import models as m
    call_ids = select(m.Call.id).where(m.Call.tenant_id == tenant_id)
    direct = [(model, model.tenant_id == tenant_id) for model in
              (m.Call, m.Ticket, m.Appointment, m.KycSession, m.APIClient, m.KnowledgeDocument, m.Workflow,
               m.Patient, m.Prescription, m.PrescriptionItem, m.FollowUpTask, m.MedicineFeedback,
               m.DoctorMeetingRequest, m.MedicineOrder, m.CareLink)]
    through_call = [(model, model.call_id.in_(call_ids)) for model in
                    (m.TranscriptMessage, m.Event, m.Escalation) if hasattr(model, "call_id")]
    return direct + through_call


def install_read_filter() -> None:
    """Add the scope's tenant to every ORM read of tenant-owned data made while a restricted scope is active."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session, with_loader_criteria

    @event.listens_for(Session, "do_orm_execute")
    def _limit_reads(state):
        scope_ = current_scope.get()
        if scope_ is None or scope_.tenant_id is None or not state.is_select or state.is_column_load:
            return
        options = [with_loader_criteria(model, criterion, include_aliases=True)
                   for model, criterion in _tenant_criteria(scope_.tenant_id)]
        state.statement = state.statement.options(*options)


# --- every new tenant-owned row gets a tenant --------------------------------------------------------------------------

_OWNED_BY_CALL = ("Ticket", "Appointment", "KycSession", "MedicineOrder", "CareLink")


def _stamp_new_row(mapper, connection, target) -> None:
    """Safety net for the ~25 places that create rows: a row without a tenant takes its call's tenant, else STL's."""
    from sqlalchemy import text
    if getattr(target, "tenant_id", None) is not None:
        return
    tenant_id = None
    scope_ = current_scope.get()
    if scope_ is not None and scope_.tenant_id is not None and scope_.tenant_id > 0:
        tenant_id = scope_.tenant_id                    # created inside a tenant user's request: theirs
    call_id = getattr(target, "call_id", None)
    client_id = getattr(target, "api_client_id", None)
    if tenant_id is None and client_id:                 # a call made for an API client belongs to the client's tenant
        tenant_id = connection.execute(text("SELECT tenant_id FROM api_clients WHERE id = :i"), {"i": client_id}).scalar()
    if tenant_id is None and call_id and type(target).__name__ in _OWNED_BY_CALL:
        tenant_id = connection.execute(text("SELECT tenant_id FROM calls WHERE id = :i"), {"i": call_id}).scalar()
    if tenant_id is None:
        tenant_id = connection.execute(text("SELECT id FROM tenants WHERE slug = :s"), {"s": SYSTEM_TENANT_SLUG}).scalar()
    if tenant_id is not None:
        target.tenant_id = tenant_id


def install_row_stamping() -> None:
    from sqlalchemy import event
    from . import models
    for name in ("Call", "Ticket", "Appointment", "KycSession", "APIClient", "KnowledgeDocument", "Workflow",
                 "Patient", "Prescription", "PrescriptionItem", "FollowUpTask", "MedicineFeedback",
                 "DoctorMeetingRequest", "MedicineOrder", "CareLink"):
        event.listen(getattr(models, name), "before_insert", _stamp_new_row)
