import pytest
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.security_store import build_security_block, merge_event, record_guard_event


def _ev(turn, sev=0, jb=False, attack="benign"):
    return {"turn": turn, "threat_severity": sev, "is_jailbreak": jb, "attack_type": attack}


def test_build_security_block_aggregates():
    block = build_security_block([_ev(1), _ev(2, 3, True, "roleplay_hijack"), _ev(3, 1, False, "other_policy_violation")])
    assert block["max_threat_severity"] == 3
    assert block["jailbreak_attempts"] == 1
    assert block["attack_types_seen"] == ["other_policy_violation", "roleplay_hijack"]
    assert block["turns_checked"] == 3


def test_build_security_block_empty():
    block = build_security_block([])
    assert block["max_threat_severity"] == 0 and block["turns_checked"] == 0


def test_merge_event_replaces_same_turn_and_sorts():
    block = merge_event(None, _ev(2))
    block = merge_event(block, _ev(1))
    block = merge_event(block, _ev(2, 2, False, "other_policy_violation"))
    assert [e["turn"] for e in block["guard_events"]] == [1, 2]
    assert block["max_threat_severity"] == 2


@pytest.mark.asyncio
async def test_record_guard_event_persists_to_call(tmp_path):
    from app.models import Base, Call

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        call = Call(conversation_id="c1", status="completed")
        db.add(call)
        await db.commit()
        call_id = call.id

    result = {"is_jailbreak": True, "jailbreak_probability": 0.97, "attack_type": "direct_instruction_override",
              "attack_type_confidence": 0.9, "threat_severity": 3, "guard_engine": "prompt-guard-2+llm"}
    with patch("app.database.async_session", Session), \
         patch("app.websocket_manager.dashboard_manager.broadcast", new_callable=AsyncMock) as bc:
        await record_guard_event(call_id, 1, {**result, "is_jailbreak": False, "threat_severity": 0, "attack_type": "benign"})
        await record_guard_event(call_id, 2, result)
        await record_guard_event(call_id, 2, result)  # duplicate turn must not double count
        assert bc.await_count == 2  # only the flagged turn broadcasts

    async with Session() as db:
        saved = await db.get(Call, call_id)
        sec = saved.to_dict()["security"]
    assert sec["turns_checked"] == 2
    assert sec["jailbreak_attempts"] == 1
    assert sec["max_threat_severity"] == 3
    assert sec["attack_types_seen"] == ["direct_instruction_override"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_record_guard_event_never_raises():
    await record_guard_event(None, 1, {"x": 1})
    await record_guard_event("not-a-number", 1, {"x": 1})
    await record_guard_event(999999, 1, {"threat_severity": 0})


@pytest.mark.asyncio
async def test_security_summary_aggregates(tmp_path):
    from app.models import Base, Call
    from app.security_api import security_summary

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'s.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(Call(conversation_id="a", status="completed",
                    security_json=build_security_block([_ev(1), _ev(2)])))
        db.add(Call(conversation_id="b", status="completed",
                    security_json=build_security_block([_ev(1), _ev(2, 3, True, "roleplay_hijack")])))
        db.add(Call(conversation_id="c", status="completed"))  # never checked
        await db.commit()
        out = await security_summary(days=30, db=db)

    assert out["calls_total"] == 3 and out["calls_checked"] == 2
    assert out["turns_checked"] == 4
    assert out["flagged_calls"] == 1 and out["jailbreak_attempts"] == 1
    assert out["severity_counts"] == {"0": 3, "1": 0, "2": 0, "3": 1}
    assert out["attack_types"] == {"roleplay_hijack": 1}
    assert out["recent_flagged"][0]["max_threat_severity"] == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_security_summary_ignores_json_null(tmp_path):
    from app.models import Base, Call
    from app.security_api import security_summary

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'n.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(Call(conversation_id="x", status="completed", security_json=None))  # stored as JSON null
        await db.commit()
        out = await security_summary(days=30, db=db)
    assert out["calls_checked"] == 0
    await engine.dispose()
