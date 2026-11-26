"""Scheduler with a fake clock: calling-window gating, lock-once, retries, caps, cancel rules."""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import followup_scheduler as fs
from app.database import Base
from app.models import FollowUpTask, Patient, Prescription

pytestmark = pytest.mark.asyncio

KOLKATA = fs.resolve_tz("Asia/Kolkata")          # UTC+5:30


class Env:
    def __init__(self, db, maker):
        self.db, self.maker = db, maker


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    # A file-backed DB so update_task_from_call's own session sees the same rows.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sched.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(fs, "async_session", maker)
    async with maker() as db:
        yield Env(db, maker)
    await engine.dispose()


# --- pure helpers ----------------------------------------------------------------------------

def test_window_and_clamp():
    assert fs.within_window(datetime(2026, 1, 15, 5, 0), KOLKATA) is True      # 10:30 local
    assert fs.within_window(datetime(2026, 1, 15, 20, 0), KOLKATA) is False    # 01:30 local
    clamped = fs.clamp_to_calling_hours(datetime(2026, 1, 15, 20, 0), KOLKATA)
    assert fs.within_window(clamped, KOLKATA) is True


def test_retry_backoff_then_needs_human():
    now = datetime(2026, 1, 15, 12, 0)
    assert fs.next_attempt_after(1, 3, now) == ("no_answer", now + timedelta(hours=2))
    assert fs.next_attempt_after(2, 3, now) == ("no_answer", now + timedelta(days=1))
    assert fs.next_attempt_after(3, 3, now) == ("needs_human", None)


# --- run_due_tasks ---------------------------------------------------------------------------

async def _patient(db, **kw):
    p = Patient(tenant_id=None, first_name="Pat", phone_e164="+971500000000", **kw)
    db.add(p)
    await db.flush()
    return p


async def _task(db, patient, due, **kw):
    kw.setdefault("status", "pending")
    t = FollowUpTask(tenant_id=None, patient_id=patient.id, kind="explain", due_at=due, **kw)
    db.add(t)
    await db.flush()
    return t


def _recorder():
    placed = []

    async def placer(db, task):
        placed.append(task.id)
        return 999
    return placed, placer


IN_HOURS = datetime(2026, 1, 15, 6, 0)          # 11:30 local, inside 09:00-20:00
OUT_HOURS = datetime(2026, 1, 15, 20, 0)        # 01:30 local, outside


async def test_only_in_window_tasks_fire(env):
    db = env.db
    p = await _patient(db)
    t = await _task(db, p, due=IN_HOURS - timedelta(hours=2))      # due before both clocks
    await db.commit()
    placed, placer = _recorder()

    assert await fs.run_due_tasks(db, now=OUT_HOURS, placer=placer) == 0   # out of calling hours
    assert placed == []
    assert await fs.run_due_tasks(db, now=IN_HOURS, placer=placer) == 1
    assert placed == [t.id]


async def test_a_task_is_never_placed_twice(env):
    db = env.db
    p = await _patient(db)
    await _task(db, p, due=IN_HOURS - timedelta(hours=1))
    await db.commit()
    placed, placer = _recorder()

    assert await fs.run_due_tasks(db, now=IN_HOURS, placer=placer) == 1
    assert await fs.run_due_tasks(db, now=IN_HOURS, placer=placer) == 0   # now 'calling', skipped
    assert len(placed) == 1


async def test_do_not_call_and_cancelled_prescription_are_not_called(env):
    db = env.db
    dnc = await _patient(db, do_not_call=True)
    await _task(db, dnc, due=IN_HOURS - timedelta(hours=1))

    p2 = await _patient(db)
    presc = Prescription(tenant_id=None, patient_id=p2.id, status="cancelled")
    db.add(presc)
    await db.flush()
    await _task(db, p2, due=IN_HOURS - timedelta(hours=1), prescription_id=presc.id)
    await db.commit()

    placed, placer = _recorder()
    assert await fs.run_due_tasks(db, now=IN_HOURS, placer=placer) == 0
    assert placed == []
    statuses = {t.status for t in (await db.execute(select(FollowUpTask))).scalars().all()}
    assert statuses == {"cancelled"}


async def test_daily_cap_limits_calls(env, monkeypatch):
    db = env.db
    monkeypatch.setenv("SANJEEVANI_DAILY_CALL_CAP", "2")
    monkeypatch.setenv("SANJEEVANI_MAX_CONCURRENT_CALLS", "10")
    for _ in range(4):
        p = await _patient(db)
        await _task(db, p, due=IN_HOURS - timedelta(hours=1))
    await db.commit()
    placed, placer = _recorder()
    assert await fs.run_due_tasks(db, now=IN_HOURS, placer=placer) == 2


async def test_concurrency_limit_respected(env, monkeypatch):
    db = env.db
    monkeypatch.setenv("SANJEEVANI_DAILY_CALL_CAP", "100")
    monkeypatch.setenv("SANJEEVANI_MAX_CONCURRENT_CALLS", "2")
    busy = await _patient(db)
    await _task(db, busy, due=IN_HOURS, status="calling", locked_at=IN_HOURS)   # one already in-flight
    for _ in range(3):
        p = await _patient(db)
        await _task(db, p, due=IN_HOURS - timedelta(hours=1))
    await db.commit()
    placed, placer = _recorder()
    assert await fs.run_due_tasks(db, now=IN_HOURS, placer=placer) == 1          # 2 - 1 in-flight


async def test_update_task_from_call_settles_outcome(env):
    db = env.db
    p = await _patient(db)
    done = await _task(db, p, due=IN_HOURS, attempts=1)
    noans = await _task(db, p, due=IN_HOURS, attempts=1, max_attempts=3)
    final = await _task(db, p, due=IN_HOURS, attempts=3, max_attempts=3)
    await db.commit()
    now = datetime(2026, 1, 15, 12, 0)

    assert await fs.update_task_from_call(done.id, "answered", now=now) == "done"
    assert await fs.update_task_from_call(noans.id, "no_answer", now=now) == "no_answer"
    assert await fs.update_task_from_call(final.id, "no_answer", now=now) == "needs_human"

    async with env.maker() as db2:
        refreshed = {t.id: t for t in (await db2.execute(select(FollowUpTask))).scalars().all()}
    assert refreshed[noans.id].next_attempt_at == now + timedelta(hours=2)
    assert refreshed[final.id].next_attempt_at is None
