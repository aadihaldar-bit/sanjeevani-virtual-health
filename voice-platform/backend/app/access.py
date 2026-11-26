"""Who is calling, which tenant (customer organization) they belong to and what they may do.

Roles
  super_admin   full access to every tenant and setting. Listed in SUPER_ADMIN_EMAILS (and in dev mode, when sign-in
                is switched off, the local developer is treated as one).
  tenant_admin  manages their own tenant: members, numbers assigned to it, keys, models and knowledge.
  manager / operator / viewer
                signed in and belong to a tenant; no administrative rights.

A signed-in user who passes the legacy allow-list but belongs to no tenant keeps working exactly as before on the
existing console routes; they simply have no tenant-scoped rights (role "operator", tenant None).
"""
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .firebase_auth import require_auth
from .models import AdminAuditEntry, Tenant, TenantMembership

SUPER_ADMIN = "super_admin"
TENANT_ADMIN = "tenant_admin"
MEMBER = "operator"                 # the default non-admin role
ROLES = (SUPER_ADMIN, TENANT_ADMIN, "manager", MEMBER, "viewer")


def platform_admin_emails() -> set:
    from .tenancy import super_admin_emails
    return super_admin_emails()


@dataclass
class User:
    email: str
    role: str
    tenant_id: Optional[int] = None
    tenant_name: Optional[str] = None
    tenant_status: Optional[str] = None
    is_staff: bool = False                   # platform staff: super admins and STL staff with no tenant membership

    @property
    def is_super_admin(self) -> bool:
        return self.role == SUPER_ADMIN

    def to_dict(self) -> Dict[str, Any]:
        return {"email": self.email, "role": self.role, "tenant_id": self.tenant_id, "tenant_name": self.tenant_name,
                "tenant_status": self.tenant_status, "is_super_admin": self.is_super_admin,
                "is_platform_staff": self.is_staff}


async def resolve_user(auth: Dict[str, Any], db: AsyncSession) -> User:
    email = str(auth.get("email") or "").strip().lower()
    if auth.get("dev_auth_disabled") or email in platform_admin_emails():
        return User(email=email or "localdev", role=SUPER_ADMIN, is_staff=True)
    row = (await db.execute(
        select(TenantMembership, Tenant).join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(TenantMembership.email == email))).first()
    if row:
        member, org = row
        if member.status != "active":                 # first sign-in accepts the invitation
            member.status = "active"
            await db.commit()
        return User(email=email, role=member.role if member.role in ROLES else MEMBER, tenant_id=org.id,
                    tenant_name=org.name, tenant_status=org.status, is_staff=False)      # a membership makes them a tenant user
    return User(email=email, role=MEMBER, is_staff=bool(auth.get("is_staff")))


async def current_user(auth: Dict[str, Any] = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> User:
    return await resolve_user(auth, db)


async def optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> Optional[User]:
    """The signed-in user when there is one; None instead of an error (for screens that only narrow what they show)."""
    try:
        return await resolve_user(await require_auth(request), db)
    except HTTPException:
        return None
    except Exception:
        return None


def require_roles(*allowed: str):
    """Dependency factory: the caller must hold one of the roles, and their organization must not be suspended."""
    async def dep(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="You do not have permission to do that.")
        if not user.is_super_admin and user.tenant_status == "suspended":
            raise HTTPException(status_code=403, detail="Your organization is suspended.")
        return user
    return dep


require_super_admin = require_roles(SUPER_ADMIN)
require_tenant_admin = require_roles(SUPER_ADMIN, TENANT_ADMIN)


def scope_tenant(user: User, tenant_id: Optional[int]) -> int:
    """The organization an action applies to. Platform admins name it; everyone else is held to their own."""
    if user.is_super_admin:
        if tenant_id is None:
            raise HTTPException(status_code=400, detail="tenant_id is required")
        return int(tenant_id)
    if user.tenant_id is None:
        raise HTTPException(status_code=403, detail="You do not belong to an organization.")
    if tenant_id is not None and int(tenant_id) != user.tenant_id:
        raise HTTPException(status_code=403, detail="That belongs to a different organization.")
    return user.tenant_id


async def audit(db: AsyncSession, actor: str, action: str, *, tenant_id: Optional[int] = None,
                target: Optional[str] = None, detail: Optional[Dict[str, Any]] = None) -> None:
    """Record an administrative action. Callers must never put secret values in `detail`."""
    db.add(AdminAuditEntry(actor=actor, tenant_id=tenant_id, action=action, target=target, detail=detail or {},
                           created_at=datetime.utcnow()))
    await db.flush()
