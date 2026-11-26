"""A company's bucket is created in the region chosen for it, and the identity check reads a date the way people say it."""
import pytest
from sqlalchemy import select

from org_env import ADMIN, env  # noqa: F401
from test_tenant_storage import FakeClient, cloud, tenant_row  # noqa: F401
from app import tenant_storage
from app.tool_executor import ToolExecutor

pytestmark = pytest.mark.asyncio


async def test_the_bucket_is_created_in_the_companys_chosen_region(env, cloud, monkeypatch):
    monkeypatch.setenv("AUTO_CREATE_TENANT_BUCKETS", "false")                 # no bucket yet: the region comes first
    tid = (await env.post("/api/tenants", json={"name": "Gulf Health"})).json()["id"]
    assert (await tenant_row(env, tid)).gcs_bucket is None
    assert (await env.put(f"/api/tenants/{tid}/bucket-region", json={"region": "me-central1"})).status_code == 200
    assert (await env.put(f"/api/tenants/{tid}/bucket-region", json={"region": "moon-base1"})).status_code == 422

    monkeypatch.setenv("AUTO_CREATE_TENANT_BUCKETS", "true")
    res = await env.post(f"/api/tenants/{tid}/bucket")
    assert res.status_code == 200 and res.json()["storage"]["status"] == "ready"
    assert res.json()["storage"]["location"] == "me-central1"
    assert [loc for _, loc in cloud.created] == ["me-central1"]

    # a bucket cannot be moved afterwards
    assert (await env.put(f"/api/tenants/{tid}/bucket-region", json={"region": "europe-west1"})).status_code == 409


async def test_a_company_with_no_choice_gets_the_server_default(env, cloud, monkeypatch):
    monkeypatch.setenv("TENANT_BUCKET_LOCATION", "europe-west1")
    tid = (await env.post("/api/tenants", json={"name": "Default Region Co"})).json()["id"]
    await tenant_storage.wait_idle()
    assert [loc for _, loc in cloud.created] == ["europe-west1"]
    assert tid


async def test_only_a_system_administrator_may_choose_a_region(env):
    tid = (await env.post("/api/tenants", json={"name": "Region Guard"})).json()["id"]
    await env.post(f"/api/tenants/{tid}/members", json={"email": "boss@guard.test", "role": "tenant_admin"})
    assert (await env.put(f"/api/tenants/{tid}/bucket-region", user="boss@guard.test", json={"region": "me-central1"})).status_code in (401, 403)
    listing = (await env.get("/api/bucket-regions")).json()["regions"]
    assert any(r["id"] == "me-central1" for r in listing)


@pytest.mark.parametrize("said,ok", [
    ("14 March 2023", True), ("March 14th, 2023", True), ("the 14th of March 2023", True), ("14/03/2023", True),
    ("03/14/2023", True), ("14-3-23", True), ("2023-03-14", True), ("14 Mar 2023", True), ("١٤ مارس ٢٠٢٣", True),
    ("15 March 2023", False), ("14 April 2023", False), ("14 March 2022", False), ("March 2023", False),
    ("yes", False), ("", False), ("14 03 1923", False), ("14/04/2023", False),
])
def test_the_date_of_birth_is_matched_as_a_whole_date(said, ok):
    assert ToolExecutor._dob_matches("2023-03-14", said) is ok


def test_day_and_month_are_swapped_only_when_the_first_reading_is_impossible():
    assert ToolExecutor._dob_matches("2023-03-05", "5/3/2023") is True           # 5 March, as written
    assert ToolExecutor._dob_matches("2023-03-05", "3/5/2023") is False          # that is 3 May: a different date
    assert ToolExecutor._dob_matches("2023-03-14", "03/14/2023") is True         # month 14 is impossible, so it is read the US way
