"""The Home page's headline figures: counted from the caller's own company only, compared with the stretch before, and
never failing on a company that has no calls yet."""
from datetime import datetime, timedelta

import pytest

from org_env import env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from app.models import Appointment, Call, CallStatus, Ticket

pytestmark = pytest.mark.asyncio


async def _calls(world, tenant, *specs):
    """specs: (days_ago, extra fields) -> a finished call for that tenant."""
    async with world.maker() as db:
        for n, (days_ago, extras) in enumerate(specs):
            when = datetime.utcnow() - timedelta(days=days_ago)
            fields = dict(tenant_id=world.tenants[tenant], conversation_id=f"{tenant}-kpi-{days_ago}-{n}", status=CallStatus.COMPLETED,
                          started_at=when, duration_seconds=60.0, caller_number="+911")
            fields.update(extras)
            db.add(Call(**fields))
        await db.commit()


async def test_figures_cover_only_the_callers_company(world):
    acme = (await world.get("/api/analytics/kpis?days=30", user=ACME_BOSS)).json()
    globex = (await world.get("/api/analytics/kpis?days=30", user=GLOBEX_BOSS)).json()
    # each company has the two calls the world gives it: one live, one completed-and-escalated, one ticket
    for body in (acme, globex):
        assert body["current"]["conversations"] == 2
        assert body["current"]["handed_over"] == 1 and body["current"]["resolved"] == 0
        assert body["current"]["tickets"] == 1
        assert body["current"]["positive"] == 2 and body["current"]["rated"] == 2
    await _calls(world, "acme", (1, {}), (2, {}))
    assert (await world.get("/api/analytics/kpis?days=30", user=ACME_BOSS)).json()["current"]["conversations"] == 4
    assert (await world.get("/api/analytics/kpis?days=30", user=GLOBEX_BOSS)).json()["current"]["conversations"] == 2


async def test_each_figure_has_the_stretch_before_it(world):
    await _calls(world, "acme",
                 (3, {"duration_seconds": 120.0}),
                 (4, {"transferred_to_human": True, "duration_seconds": 30.0}),
                 (40, {"duration_seconds": 100.0}), (45, {}), (50, {}))      # the 30 days before the last 30
    body = (await world.get("/api/analytics/kpis?days=30", user=ACME_BOSS)).json()
    assert body["current"]["conversations"] == 4 and body["previous"]["conversations"] == 3
    assert body["current"]["handed_over"] == 2
    assert body["current"]["resolved"] == 1                                   # the transferred call is not "resolved"
    assert body["current"]["resolved_rate_percent"] == 25.0
    assert body["previous"]["resolved_rate_percent"] == 100.0
    assert body["current"]["talk_seconds"] == 150.0 and body["previous"]["talk_seconds"] == 220.0
    assert body["previous"]["average_duration_seconds"] == pytest.approx(73.3, abs=0.1)


async def test_tickets_appointments_and_test_calls_are_counted_in_their_window(world):
    await _calls(world, "acme", (1, {"source": "simulator"}), (2, {"source": "phone"}))
    async with world.maker() as db:
        tid = world.tenants["acme"]
        db.add(Ticket(tenant_id=tid, ticket_id="OLD-1", caller_number="+911", intent="i", issue="old", priority="high",
                      transcript_summary="s", created_at=datetime.utcnow() - timedelta(days=45)))
        db.add(Appointment(tenant_id=tid, appointment_id="APT-1", patient_name="A", caller_number="+911", doctor_name="D",
                           created_at=datetime.utcnow() - timedelta(days=1)))
        await db.commit()
    body = (await world.get("/api/analytics/kpis?days=30", user=ACME_BOSS)).json()
    assert (body["current"]["tickets"], body["previous"]["tickets"]) == (1, 1)
    assert (body["current"]["appointments"], body["previous"]["appointments"]) == (1, 0)
    assert body["current"]["test_calls"] == 1
    other = (await world.get("/api/analytics/kpis?days=30", user=GLOBEX_BOSS)).json()
    assert other["current"]["appointments"] == 0 and other["current"]["test_calls"] == 0


async def test_busiest_hour_agent_and_today(world):
    noon_ist = (datetime.utcnow().replace(hour=6, minute=30, second=0, microsecond=0)) - timedelta(days=2)     # 12:00 in India
    await _calls(world, "acme",
                 (0, {"started_at": noon_ist, "current_agent": "Front desk"}),
                 (0, {"started_at": noon_ist + timedelta(minutes=10), "current_agent": "Front desk"}),
                 (0, {"started_at": noon_ist + timedelta(minutes=20), "current_agent": "Billing"}))
    body = (await world.get("/api/analytics/kpis?days=30", user=ACME_BOSS)).json()
    assert body["peak_hour_ist"] == 12 and body["peak_hour_calls"] == 3
    assert body["top_agent"] == {"name": "Front desk", "calls": 2}
    assert body["calls_today"] >= 2                                              # the two the world created just now


async def test_a_company_with_no_calls_gets_empty_figures_not_an_error(world):
    empty = await world.make_org("Initech", admin_email="boss@initech.test")
    res = await world.get("/api/analytics/kpis?days=7", user="boss@initech.test")
    assert res.status_code == 200
    body = res.json()
    assert body["current"]["conversations"] == 0 and body["current"]["resolved_rate_percent"] is None
    assert body["current"]["average_duration_seconds"] is None and body["current"]["talk_seconds"] == 0
    assert body["peak_hour_ist"] is None and body["top_agent"] is None and body["calls_today"] == 0
    assert empty["id"] != world.tenants["acme"]


async def test_the_summary_transfer_rate_counts_calls_handed_to_a_person(world):
    await _calls(world, "acme", (1, {"transferred_to_human": True}), (1, {}))
    summary = (await world.get("/api/analytics/summary?days=30", user=ACME_BOSS)).json()
    # 4 calls: the escalated one and the transferred one were handed over
    assert summary["total_calls"] == 4 and summary["handed_over_calls"] == 2 and summary["transfer_rate_percent"] == 50.0


async def test_the_days_window_is_bounded(world):
    assert (await world.get("/api/analytics/kpis?days=0", user=ACME_BOSS)).status_code == 422
    assert (await world.get("/api/analytics/kpis?days=366", user=ACME_BOSS)).status_code == 422


async def test_the_daily_series_counts_calls_handed_to_a_person(world):
    await _calls(world, "acme", (0, {"transferred_to_human": True}), (0, {}))
    today = (await world.get("/api/analytics/daily?days=1", user=ACME_BOSS)).json()["data"][0]
    # the world's escalated call + the transferred one are handed over; of 4 calls today, 2 were not
    assert today["total_calls"] == 4 and today["escalated_calls"] == 2 and today["ai_resolved"] == 2


async def test_the_busiest_hour_and_today_follow_the_companys_time_zone(world):
    from app.models import Tenant
    async with world.maker() as db:
        (await db.get(Tenant, world.tenants["acme"])).timezone = "Asia/Dubai"
        await db.commit()
    noon_utc = datetime.utcnow().replace(hour=8, minute=30, second=0, microsecond=0) - timedelta(days=2)      # 12:30 in Dubai, 14:00 in India
    await _calls(world, "acme", (0, {"started_at": noon_utc}), (0, {"started_at": noon_utc + timedelta(minutes=5)}), (0, {"started_at": noon_utc + timedelta(minutes=9)}))
    dubai = (await world.get("/api/analytics/kpis?days=30", user=ACME_BOSS)).json()
    assert dubai["timezone"] == "Asia/Dubai" and dubai["peak_hour"] == 12
    india = (await world.get("/api/analytics/kpis?days=30", user=GLOBEX_BOSS)).json()       # Globex has no zone set
    assert india["timezone"] == "Asia/Kolkata"
