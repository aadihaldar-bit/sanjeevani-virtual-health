"""Shared fixture: a throw-away database and an app with the organization routers, where the signed-in user is chosen
per request with the X-Test-User header (no Firebase needed)."""
import httpx
import pytest_asyncio
from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database, external_models_api, numbers_api, org_api, secret_store, vector_stores_api, workflow_api
from app.access import optional_user, resolve_user
from app.database import Base, get_db
from app.firebase_auth import require_auth

ADMIN = "aarit@stl.test"
ADMIN2 = "mithun@stl.test"
LEGACY = "staff@stl.test"           # passes the old allow-list but belongs to no organization


async def fake_auth(request: Request):
    email = request.headers.get("x-test-user")
    if not email:
        raise HTTPException(status_code=401, detail="Sign-in required.")
    return {"email": email, "email_verified": True, "is_staff": email.endswith("@stl.test")}      # STL staff vs a tenant user


class Env:
    def __init__(self, client, maker, app):
        self.client, self.maker, self.app = client, maker, app

    def as_user(self, email):
        return {"x-test-user": email}

    async def get(self, path, user=ADMIN, **kw):
        return await self.client.get(path, headers=self.as_user(user), **kw)

    async def post(self, path, user=ADMIN, **kw):
        return await self.client.post(path, headers=self.as_user(user), **kw)

    async def put(self, path, user=ADMIN, **kw):
        return await self.client.put(path, headers=self.as_user(user), **kw)

    async def patch(self, path, user=ADMIN, **kw):
        return await self.client.patch(path, headers=self.as_user(user), **kw)

    async def delete(self, path, user=ADMIN, **kw):
        return await self.client.delete(path, headers=self.as_user(user), **kw)

    async def make_org(self, name, admin_email=None, member_email=None):
        org = (await self.post("/api/tenants", json={"name": name})).json()
        if admin_email:
            await self.post(f"/api/tenants/{org['id']}/members", json={"email": admin_email, "role": "tenant_admin"})
        if member_email:
            await self.post(f"/api/tenants/{org['id']}/members", json={"email": member_email, "role": "operator"})
        return org


def build_app(maker, tenant_data: bool):
    """The app under test. With tenant_data=True the data routers (calls, tickets, knowledge, workflows, agents) are
    mounted behind the same tenant dependency main.py uses; otherwise only the admin routers plus the workflow options."""
    from app import agents_api, api_routes, knowledge_routes
    from app.tenancy import tenant_scope

    app = FastAPI()
    for module in (org_api, numbers_api, external_models_api, vector_stores_api):
        app.include_router(module.router)
    if tenant_data:
        from app.outbound_api_admin import outbound_admin_router as api_clients_router
        from app import architect_api, architect_design, architect_run, sanjeevani_reports, prescriptions_api
        for router in (api_routes.router, api_routes.tickets_router, knowledge_routes.router, workflow_api.router, agents_api.router,
                       api_clients_router, architect_api.router, architect_design.router, architect_run.router, prescriptions_api.console_router, sanjeevani_reports.reports_router):
            app.include_router(router, dependencies=[Depends(require_auth), Depends(tenant_scope)])
    else:
        app.include_router(workflow_api.router)

    async def db_override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[require_auth] = fake_auth

    async def optional_override(request: Request, db=Depends(db_override)):
        try:
            return await resolve_user(await fake_auth(request), db)
        except HTTPException:
            return None

    app.dependency_overrides[optional_user] = optional_override
    return app


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", f"{ADMIN}, {ADMIN2}")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", secret_store.generate_key())
    monkeypatch.delenv("EXTERNAL_HOST_ALLOWLIST", raising=False)
    monkeypatch.delenv("EXTERNAL_ALLOW_HTTP", raising=False)
    org_api._BUCKET_HITS.clear()                       # the bucket API's rate limiter is process-wide; each test starts fresh
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'org.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)         # code that opens its own session uses the test database

    app = build_app(maker, tenant_data=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Env(client, maker, app)
    await engine.dispose()
