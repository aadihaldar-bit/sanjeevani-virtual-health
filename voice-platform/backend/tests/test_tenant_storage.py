"""Automatic per-tenant buckets. A fake storage client stands in for Google Cloud: no real bucket is ever created."""
import json
from types import SimpleNamespace

import pytest
from google.api_core import exceptions as gexc
from sqlalchemy import select

from org_env import ADMIN, env  # noqa: F401
from app import tenant_storage
from app.models import AdminAuditEntry, Tenant

pytestmark = pytest.mark.asyncio


class FakeBucket:
    def __init__(self, name):
        self.name = name
        self.iam_configuration = SimpleNamespace(uniform_bucket_level_access_enabled=False, public_access_prevention=None)
        self.labels = {}


class FakeClient:
    """Records what would be created. `conflicts` names are refused as taken; `forbid` refuses everything."""

    def __init__(self, conflicts=0, forbid=False, writable=True):
        self.created, self.attempts, self.conflicts, self.forbid, self.writable = [], [], conflicts, forbid, writable
        self.probes = []

    def bucket(self, name):
        return FakeBucket(name)

    def create_bucket(self, bucket, location=None):
        self.attempts.append(bucket.name)
        if self.forbid:
            raise gexc.Forbidden("caller does not have storage.buckets.create")
        if self.conflicts > 0:
            self.conflicts -= 1
            raise gexc.Conflict("bucket name is taken")
        self.created.append((bucket, location))
        return bucket


@pytest.fixture
def cloud(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(tenant_storage, "_client", lambda: client)
    monkeypatch.setenv("AUTO_CREATE_TENANT_BUCKETS", "true")
    tenant_storage._cache.clear()
    tenant_storage._in_flight.clear()
    tenant_storage._tasks.clear()
    return client


async def new_tenant(env, name="Acme Corp"):
    res = await env.post("/api/tenants", json={"name": name})
    assert res.status_code == 201, res.text
    await tenant_storage.wait_idle()
    return res.json()["id"]


async def tenant_row(env, tenant_id):
    async with env.maker() as db:
        row = await db.get(Tenant, tenant_id)
        await db.refresh(row)
        return row


# --- names ---------------------------------------------------------------------------------------------------------------

def test_bucket_names_are_valid_and_carry_the_tenant():
    for slug in ("acme", "Acme Corp!!", "x" * 80, "google-labs", "a"):
        name = tenant_storage.make_bucket_name(slug, 12)
        assert 3 <= len(name) <= 63 and name == name.lower(), name
        assert "google" not in name and not name.startswith("goog")
        assert name.startswith("stl-voicebot-") and "-12-" in name
    assert tenant_storage.make_bucket_name("acme", 1) != tenant_storage.make_bucket_name("acme", 1)      # random tail


# --- creation ------------------------------------------------------------------------------------------------------------

async def test_a_new_tenant_gets_a_private_labelled_bucket(env, cloud):
    tenant_id = await new_tenant(env)
    assert len(cloud.created) == 1
    bucket, location = cloud.created[0]
    assert bucket.iam_configuration.uniform_bucket_level_access_enabled is True
    assert bucket.iam_configuration.public_access_prevention == "enforced"
    assert bucket.labels == {"tenant_id": str(tenant_id), "managed_by": "stl-voicebot"} and location == "us-central1"
    row = await tenant_row(env, tenant_id)
    assert row.gcs_bucket == bucket.name and row.settings_json["storage"]["status"] == "ready"
    listed = (await env.get("/api/tenants")).json()["orgs"]
    assert next(o for o in listed if o["id"] == tenant_id)["storage"]["bucket"] == bucket.name
    async with env.maker() as db:
        actions = [e.action for e in (await db.execute(select(AdminAuditEntry))).scalars().all()]
    assert "storage.bucket_created" in actions


async def test_nothing_is_created_while_the_feature_is_off(env, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(tenant_storage, "_client", lambda: client)
    monkeypatch.delenv("AUTO_CREATE_TENANT_BUCKETS", raising=False)
    tenant_id = await new_tenant(env)
    assert client.attempts == [] and (await tenant_row(env, tenant_id)).gcs_bucket is None
    assert (await env.post(f"/api/tenants/{tenant_id}/bucket")).status_code == 409


async def test_a_taken_name_is_retried_with_another(env, cloud):
    cloud.conflicts = 2
    tenant_id = await new_tenant(env)
    assert len(cloud.attempts) == 3 and len(set(cloud.attempts)) == 3 and len(cloud.created) == 1
    assert (await tenant_row(env, tenant_id)).settings_json["storage"]["status"] == "ready"


async def test_a_missing_permission_is_reported_and_tenant_creation_still_works(env, cloud):
    cloud.forbid = True
    tenant_id = await new_tenant(env)                               # the 201 above proves creation did not fail
    storage = (await tenant_row(env, tenant_id)).settings_json["storage"]
    assert storage["status"] == "failed" and "storage.buckets.create" in storage["error"]
    assert (await tenant_row(env, tenant_id)).gcs_bucket is None


async def test_too_many_taken_names_gives_up_cleanly(env, cloud):
    cloud.conflicts = 99
    tenant_id = await new_tenant(env)
    assert len(cloud.attempts) == tenant_storage.MAX_NAME_ATTEMPTS
    assert (await tenant_row(env, tenant_id)).settings_json["storage"]["status"] == "failed"


async def test_the_platforms_own_tenant_keeps_the_shared_bucket(env, cloud):
    async with env.maker() as db:
        stl = Tenant(name="STL Digital", slug="stl", is_system=True)
        db.add(stl)
        await db.commit()
        stl_id = stl.id
    assert (await tenant_storage.provision_for_tenant(stl_id))["status"] == "skipped" and cloud.attempts == []
    assert (await env.post(f"/api/tenants/{stl_id}/bucket")).status_code == 409


# --- retry and registering an existing bucket -----------------------------------------------------------------------------

async def test_a_super_admin_can_retry_and_it_is_not_repeated_once_ready(env, cloud):
    cloud.forbid = True
    tenant_id = await new_tenant(env)
    cloud.forbid = False
    res = await env.post(f"/api/tenants/{tenant_id}/bucket")
    assert res.status_code == 200 and res.json()["storage"]["status"] == "ready" and len(cloud.created) == 1
    again = await env.post(f"/api/tenants/{tenant_id}/bucket")
    assert again.json()["storage"]["status"] == "ready" and len(cloud.created) == 1          # no second bucket


async def test_only_a_super_admin_manages_buckets(env, cloud):
    acme = await env.make_org("Acme", admin_email="boss@acme.test")
    assert (await env.post(f"/api/tenants/{acme['id']}/bucket", user="boss@acme.test")).status_code == 403
    assert (await env.put(f"/api/tenants/{acme['id']}/bucket", user="boss@acme.test", json={"bucket": "my-bucket-1"})).status_code == 403


async def test_an_existing_bucket_can_be_registered_after_a_write_check(env, cloud, monkeypatch):
    tenant_id = await new_tenant(env)
    cloud_blobs = []

    class Blob:
        def upload_from_string(self, data):
            cloud_blobs.append("write")

        def delete(self):
            cloud_blobs.append("delete")

    monkeypatch.setattr(FakeBucket, "blob", lambda self, name: Blob(), raising=False)
    res = await env.put(f"/api/tenants/{tenant_id}/bucket", json={"bucket": "client-owned-logs"})
    assert res.status_code == 200 and res.json()["storage"]["bucket"] == "client-owned-logs"
    assert cloud_blobs == ["write", "delete"] and (await tenant_row(env, tenant_id)).gcs_bucket == "client-owned-logs"
    assert (await env.put(f"/api/tenants/{tenant_id}/bucket", json={"bucket": "Not A Bucket!"})).status_code == 422


async def test_registering_fails_clearly_when_the_bucket_cannot_be_written(env, cloud, monkeypatch):
    tenant_id = await new_tenant(env)

    class Blob:
        def upload_from_string(self, data):
            raise gexc.Forbidden("no")

    monkeypatch.setattr(FakeBucket, "blob", lambda self, name: Blob(), raising=False)
    res = await env.put(f"/api/tenants/{tenant_id}/bucket", json={"bucket": "someone-elses-bucket"})
    assert res.status_code == 422 and "Could not write" in res.text


# --- where call logs go --------------------------------------------------------------------------------------------------

async def test_call_logs_go_to_the_tenants_bucket_and_others_to_the_shared_one(env, cloud, monkeypatch, tmp_path):
    from app.event_logger import EventLogger
    tenant_id = await new_tenant(env)
    own_bucket = (await tenant_row(env, tenant_id)).gcs_bucket
    uploads = []

    class Blob:
        def __init__(self, bucket, path):
            self.bucket, self.path = bucket, path

        def upload_from_string(self, data, content_type=None):
            uploads.append((self.bucket, self.path))

    class Client:
        def bucket(self, name):
            return SimpleNamespace(blob=lambda path: Blob(name, path))

    log = EventLogger(log_dir=str(tmp_path / "events"))
    monkeypatch.setattr(log, "_get_gcs_storage_client", lambda: Client())
    log.bucket_name = "shared-bucket"
    tenant_storage._cache.clear()
    base = {"call_id": 7, "telephony": {"provider": "smartflo", "caller_phone": "+911"}}
    await log.upload_call_json({**base, "tenant_id": tenant_id})
    await log.upload_call_json({**base, "tenant_id": 999})                  # a tenant with no bucket of its own
    await log.upload_call_json(base)                                         # an older record with no tenant
    assert [b for b, _ in uploads] == [own_bucket, "shared-bucket", "shared-bucket"]


async def test_a_failed_bucket_is_never_used_for_logs(env, cloud):
    cloud.forbid = True
    tenant_id = await new_tenant(env)
    tenant_storage._cache.clear()
    assert await tenant_storage.bucket_for_tenant(tenant_id) is None
    assert await tenant_storage.bucket_for_tenant(None) is None
