"""Bring-your-own provider keys and custom model endpoints. Secrets are accepted but never returned."""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import external_models as em
from . import secret_store
from .access import User, audit, require_tenant_admin, scope_tenant
from .config import get_config
from .database import get_db
from .models import ModelEndpoint, ProviderCredential
from .provider_health import probe_key

router = APIRouter(prefix="/api/external", tags=["external-models"])


class CredentialBody(BaseModel):
    tenant_id: Optional[int] = None
    kind: str = Field(max_length=8)
    provider: str = Field(max_length=32)
    api_key: str = Field(max_length=600, repr=False)


class EndpointBody(BaseModel):
    tenant_id: Optional[int] = None
    platform_wide: bool = False
    name: str = Field(max_length=100)
    base_url: str = Field(max_length=512)
    model: str = Field(max_length=200)
    api_key: Optional[str] = Field(default=None, max_length=600, repr=False)
    headers: Optional[Dict[str, str]] = Field(default=None, repr=False)
    supports_tools: bool = False
    enabled: bool = True
    acknowledge_data_sharing: bool = False


def _fail(exc: em.ExternalModelError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


@router.get("/status")
async def status(user: User = Depends(require_tenant_admin)):
    return {"encryption_configured": secret_store.is_configured(),
            "supported_credentials": [{"kind": k, "provider": p} for (k, p) in sorted(em.CREDENTIAL_FIELDS)]}


@router.get("/credentials")
async def list_credentials(tenant_id: Optional[int] = Query(default=None), user: User = Depends(require_tenant_admin),
                           db: AsyncSession = Depends(get_db)):
    target = scope_tenant(user, tenant_id)
    rows = (await db.execute(select(ProviderCredential).where(ProviderCredential.tenant_id == target)
                             .order_by(ProviderCredential.kind, ProviderCredential.provider))).scalars().all()
    return {"credentials": [r.to_dict() for r in rows]}


@router.put("/credentials")
async def put_credential(body: CredentialBody, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    target = scope_tenant(user, body.tenant_id)
    try:
        row = await em.set_credential(db, target, body.kind, body.provider, body.api_key, user.email)
    except em.ExternalModelError as exc:
        await db.rollback()
        _fail(exc)
    await audit(db, user.email, "credential.set", tenant_id=target, target=f"{row.kind}/{row.provider}")      # never the key
    await db.commit()
    return row.to_dict()


@router.delete("/credentials/{credential_id}")
async def delete_credential(credential_id: int, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(ProviderCredential, credential_id)
    if not row:
        raise HTTPException(status_code=404, detail="Credential not found.")
    scope_tenant(user, row.tenant_id)
    await audit(db, user.email, "credential.delete", tenant_id=row.tenant_id, target=f"{row.kind}/{row.provider}")
    await db.delete(row)
    await db.commit()
    return {"deleted": True}


@router.post("/credentials/{credential_id}/test")
async def test_credential(credential_id: int, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    """One tiny live request with the saved key, so a wrong, expired or unfunded key is found now and not on a call."""
    row = await db.get(ProviderCredential, credential_id)
    if not row:
        raise HTTPException(status_code=404, detail="Credential not found.")
    scope_tenant(user, row.tenant_id)
    if row.kind != "llm":
        return {"supported": False, "ok": None, "issue": "Only language-model keys can be checked from here."}
    try:
        key = secret_store.decrypt(row.secret_enc)
    except secret_store.SecretStoreError:
        return {"supported": True, "ok": False, "issue": "The saved key cannot be read. Enter it again."}
    cfg = em.apply_org_credentials(get_config(), {(row.kind, row.provider): key})
    result = await probe_key(cfg, row.provider)           # the issue text is generic and never contains the key
    await audit(db, user.email, "credential.test", tenant_id=row.tenant_id, target=f"{row.kind}/{row.provider}")
    await db.commit()
    return {"supported": True, "ok": bool(result["ok"]), "issue": result.get("issue"), "model": result.get("model")}


def _owner_org(user: User, body: EndpointBody) -> Optional[int]:
    if body.platform_wide:
        if not user.is_super_admin:
            raise HTTPException(status_code=403, detail="Only platform administrators can create platform-wide endpoints.")
        return None
    return scope_tenant(user, body.tenant_id)


@router.get("/endpoints")
async def list_endpoints(tenant_id: Optional[int] = Query(default=None), user: User = Depends(require_tenant_admin),
                         db: AsyncSession = Depends(get_db)):
    q = select(ModelEndpoint).order_by(ModelEndpoint.name)
    if user.is_super_admin and tenant_id is None:
        pass
    else:
        target = scope_tenant(user, tenant_id)
        q = q.where((ModelEndpoint.tenant_id == target) | (ModelEndpoint.tenant_id.is_(None)))
    return {"endpoints": [e.to_dict() for e in (await db.execute(q)).scalars().all()]}


@router.post("/endpoints", status_code=201)
async def create_endpoint(body: EndpointBody, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    owner = _owner_org(user, body)
    try:
        row = await em.save_endpoint(db, user.email, tenant_id=owner, name=body.name, base_url=body.base_url, model=body.model,
                                     api_key=body.api_key, headers=body.headers, supports_tools=body.supports_tools,
                                     acknowledge=body.acknowledge_data_sharing)
    except em.ExternalModelError as exc:
        await db.rollback()
        _fail(exc)
    row.enabled = body.enabled
    await audit(db, user.email, "endpoint.create", tenant_id=owner, target=row.name, detail={"base_url": row.base_url, "model": row.model})
    await db.commit()
    return row.to_dict()


async def _editable(db: AsyncSession, user: User, endpoint_id: int) -> ModelEndpoint:
    row = await db.get(ModelEndpoint, endpoint_id)
    if not row:
        raise HTTPException(status_code=404, detail="Endpoint not found.")
    if row.tenant_id is None:
        if not user.is_super_admin:
            raise HTTPException(status_code=403, detail="Platform-wide endpoints can only be changed by platform administrators.")
    else:
        scope_tenant(user, row.tenant_id)
    return row


@router.put("/endpoints/{endpoint_id}")
async def update_endpoint(endpoint_id: int, body: EndpointBody, user: User = Depends(require_tenant_admin),
                          db: AsyncSession = Depends(get_db)):
    row = await _editable(db, user, endpoint_id)
    try:
        await em.save_endpoint(db, user.email, tenant_id=row.tenant_id, name=body.name, base_url=body.base_url, model=body.model,
                               api_key=body.api_key, headers=body.headers, supports_tools=body.supports_tools,
                               acknowledge=body.acknowledge_data_sharing, endpoint=row)
    except em.ExternalModelError as exc:
        await db.rollback()
        _fail(exc)
    row.enabled = body.enabled
    await audit(db, user.email, "endpoint.update", tenant_id=row.tenant_id, target=row.name, detail={"base_url": row.base_url, "model": row.model})
    await db.commit()
    return row.to_dict()


@router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(endpoint_id: int, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    row = await _editable(db, user, endpoint_id)
    await audit(db, user.email, "endpoint.delete", tenant_id=row.tenant_id, target=row.name)
    await db.delete(row)
    await db.commit()
    return {"deleted": True}


@router.post("/endpoints/{endpoint_id}/test")
async def test_endpoint(endpoint_id: int, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    row = await db.get(ModelEndpoint, endpoint_id)
    if not row:
        raise HTTPException(status_code=404, detail="Endpoint not found.")
    if row.tenant_id is not None:
        scope_tenant(user, row.tenant_id)
    result = await em.test_endpoint(db, row)
    await audit(db, user.email, "endpoint.test", tenant_id=row.tenant_id, target=row.name, detail={"ok": result["ok"], "ms": result["ms"]})
    await db.commit()
    return result
