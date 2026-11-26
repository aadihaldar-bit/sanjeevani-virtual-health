from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.call_reaper import reap_stale_calls
from app.database import Base


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    await engine.dispose()


def _call(cid, source, caller, started, **kw):
    return models.Call(id=cid, conversation_id=f"c{cid}", call_sid=f"s{cid}", caller_number=caller, source=source,
                       status=models.CallStatus.ACTIVE, started_at=started, **kw)


@pytest.mark.asyncio
async def test_quiet_calls_are_closed_and_live_ones_are_kept(session):
    now = datetime(2026, 10, 13, 15, 0, 0)
    session.add_all([
        _call(1, "simulator", "Web Simulator", now - timedelta(days=3)),                 # abandoned simulator tab
        _call(2, "simulator", "Web Simulator", now - timedelta(minutes=3)),              # a test in progress
        _call(3, "smartflo-stream", "+911234567890", now - timedelta(minutes=14)),       # long phone call, still talking
        _call(4, "smartflo-stream", "+911234567891", now - timedelta(hours=2)),          # dropped stream
    ])
    session.add(models.TranscriptMessage(call_id=3, role="user", message="hello", sequence_number=1, timestamp=now - timedelta(seconds=30)))
    await session.commit()

    assert await reap_stale_calls(session, now=now) == 2

    status = {c.id: c.status for c in (await session.execute(select(models.Call))).scalars().all()}
    assert status[1] == models.CallStatus.COMPLETED and status[4] == models.CallStatus.COMPLETED
    assert status[2] == models.CallStatus.ACTIVE and status[3] == models.CallStatus.ACTIVE
