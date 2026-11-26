import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.future import select

from app.workflow_api import save_workflow, WorkflowSchema, NodeSchema, EdgeSchema
from app.workflow_validation import validate_workflow


def node(i, t="agentNode", **data):
    return NodeSchema(id=i, type=t, position={"x": 0, "y": 0}, data=data)


def edge(i, s, t):
    return EdgeSchema(id=i, source=s, target=t)


def good(name="Flow", wid=None, active=True, prompt="You are helpful."):
    return WorkflowSchema(
        id=wid, name=name, description="", is_active=active,
        nodes=[node("s", "startNode"), node("a", system_prompt=prompt, name="Agent")],
        edges=[edge("e1", "s", "a")],
    )


def test_validate_ok():
    d = good()
    assert validate_workflow(d.nodes, d.edges) == ([], [])


def test_validate_errors():
    errs, _ = validate_workflow([], [])
    assert errs
    errs, _ = validate_workflow([node("a"), node("a")], [])
    assert any("unique" in e for e in errs)
    errs, _ = validate_workflow([node("a")], [edge("e", "a", "ghost")])
    assert any("missing target" in e for e in errs)
    errs, _ = validate_workflow([node("t", "toolNode")], [])
    assert any("Agent node" in e for e in errs)
    errs, _ = validate_workflow([node("s1", "startNode"), node("s2", "startNode"), node("a")], [])
    assert any("one Start" in e for e in errs)


def test_validate_warnings():
    d = WorkflowSchema(id=None, name="x", description="", is_active=True,
                       nodes=[node("a", name="Bot"), node("b", name="Loose", system_prompt="hi")],
                       edges=[])
    _, warns = validate_workflow(d.nodes, d.edges)
    assert any("no system prompt" in w for w in warns)
    assert any("not connected" in w for w in warns)


@pytest_asyncio.fixture
async def session(tmp_path):
    from app.models import Base
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'w.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_resave_active_workflow_does_not_crash_and_replaces_graph(session):
    from app.models import Workflow, WorkflowNode
    first = await save_workflow(good("A"), session)
    wid = first["id"]

    d = good("A renamed", wid=wid)
    d.nodes.append(node("b", system_prompt="second", name="Second"))
    d.edges.append(edge("e2", "a", "b"))
    second = await save_workflow(d, session)  # used to raise AttributeError (select().update)
    assert second["id"] == wid

    nodes = (await session.execute(select(WorkflowNode).where(WorkflowNode.workflow_id == wid))).scalars().all()
    assert sorted(n.id for n in nodes) == ["a", "b", "s"]
    wf = (await session.execute(select(Workflow).where(Workflow.id == wid))).scalars().first()
    assert wf.name == "A renamed"


@pytest.mark.asyncio
async def test_activating_one_workflow_deactivates_others(session):
    from app.models import Workflow
    d1 = good("one")
    d1.nodes = [node("s1", "startNode"), node("a1", system_prompt="x")]
    d1.edges = [edge("e1", "s1", "a1")]
    d2 = good("two")
    d2.nodes = [node("s2", "startNode"), node("a2", system_prompt="y")]
    d2.edges = [edge("e2", "s2", "a2")]
    r1 = await save_workflow(d1, session)
    r2 = await save_workflow(d2, session)
    rows = (await session.execute(select(Workflow))).scalars().all()
    active = {w.id for w in rows if w.is_active}
    assert active == {r2["id"]} and r1["id"] not in active


@pytest.mark.asyncio
async def test_invalid_save_returns_422_and_keeps_old_graph(session):
    from app.models import WorkflowNode
    r = await save_workflow(good("keep"), session)
    bad = WorkflowSchema(id=r["id"], name="keep", description="", is_active=True, nodes=[], edges=[])
    with pytest.raises(HTTPException) as exc:
        await save_workflow(bad, session)
    assert exc.value.status_code == 422 and exc.value.detail["errors"]
    nodes = (await session.execute(select(WorkflowNode).where(WorkflowNode.workflow_id == r["id"]))).scalars().all()
    assert len(nodes) == 2


@pytest.mark.asyncio
async def test_warnings_returned_on_success(session):
    r = await save_workflow(good("w", prompt=""), session)
    assert any("no system prompt" in w for w in r["warnings"])
