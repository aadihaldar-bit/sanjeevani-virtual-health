"""An agent made from a description (through Architect, or the builder's AI box) must carry that description.

It did not: the model was asked for a spoken-length answer (250 tokens) and had its underscores stripped, so the workflow JSON
arrived cut off; the stand-in graph then crashed on a JSON `false`; and the agent was created with the stock one-line prompt, so
a "Lenovo laptop booking" agent told callers it could not take orders."""
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from org_env import env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, world  # noqa: F401
from app import workflow_api
from app.models import Workflow, WorkflowEdge, WorkflowNode

pytestmark = pytest.mark.asyncio

DESCRIPTION = "You answer calls for Lenovo laptop booking. Ask the caller which type of laptop they want and book it. Never quote prices."
STOCK = "You are the primary AI voice agent for"

DRAWN = {
    "name": "Lenovo bookings", "description": "Books Lenovo laptops",
    "nodes": [
        {"id": "start-1", "type": "startNode", "position": {"x": 100, "y": 200}, "data": {"name": "Start", "system_prompt": "Call entrypoint greeting"}},
        {"id": "agent-1", "type": "agentNode", "position": {"x": 400, "y": 150},
         "data": {"name": "Booking agent", "system_prompt": DESCRIPTION, "voice": "Aarohi", "llm": "groq", "use_rag": False}},
        {"id": "odd-1", "type": "mysteryNode", "position": {}, "data": {}},
    ],
    "edges": [{"id": "e1", "source": "start-1", "target": "agent-1", "label": "Connected"},
              {"id": "e2", "source": "agent-1", "target": "ghost-1", "label": "to a step that is not there"}],
}
CUT_OFF = '{\n  "name": "Lenovo Laptop Booking",\n  "description": "Voice workflow to handle Lenovo laptop bookings,'


class Model:
    """Stands in for the thinking model: answers `reply` (or raises it), and remembers how it was asked."""
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    async def ask(self, system_prompt, prompt, history=None, *, cfg=None, answer_tokens=700):
        self.asked.append({"system": system_prompt, "prompt": prompt, "answer_tokens": answer_tokens})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def answer_with(monkeypatch, reply):
    model = Model(reply if isinstance(reply, (str, Exception)) else json.dumps(reply))
    monkeypatch.setattr("app.architect_model.ask", model.ask)
    return model


async def graph_of(world, name):
    async with world.maker() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.name == name))).scalars().first()
        nodes = (await db.execute(select(WorkflowNode).where(WorkflowNode.workflow_id == wf.id))).scalars().all()
        edges = (await db.execute(select(WorkflowEdge).where(WorkflowEdge.workflow_id == wf.id))).scalars().all()
        return wf, nodes, edges


# --- the generator -------------------------------------------------------------------------------------------------------

async def test_the_generator_asks_for_room_to_finish_a_whole_workflow(monkeypatch):
    model = answer_with(monkeypatch, DRAWN)
    await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    assert model.asked[0]["answer_tokens"] >= 2000                  # a workflow does not fit the 250 tokens of a spoken answer
    assert DESCRIPTION in model.asked[0]["prompt"]


async def test_a_drawn_workflow_is_tidied_for_the_builder_and_for_calls(monkeypatch):
    answer_with(monkeypatch, DRAWN)
    out = await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    kinds = {n["id"]: n["type"] for n in out["nodes"]}
    assert kinds == {"start-1": "startNode", "agent-1": "agentNode"}                              # the unknown kind is dropped
    prompts = {n["id"]: n["data"].get("system_prompt") for n in out["nodes"]}
    assert prompts == {"start-1": None, "agent-1": DESCRIPTION}                                   # only the agent carries instructions
    assert [e["id"] for e in out["edges"]] == ["e1"]                                              # no arrow to a step that is not there
    assert all(isinstance(n["position"]["x"], float) and isinstance(n["position"]["y"], float) for n in out["nodes"])


async def test_a_drawn_agent_has_no_tools_whatever_the_model_guessed(monkeypatch):
    """Which tools an agent has is for a person to choose: a model's guess names no tool we have, and a keyword must not pick one."""
    guessed = json.loads(json.dumps(DRAWN))
    guessed["nodes"][1]["data"].update(tools=["book_laptop", "find_doctor"])
    answer_with(monkeypatch, guessed)
    out = await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    agent = next(n for n in out["nodes"] if n["type"] == "agentNode")["data"]
    assert agent["tools"] == [] and agent["tools_explicit"] is True
    assert agent["use_rag"] is False                                # the model's own choice about documents stands


async def test_an_agent_that_says_nothing_about_documents_may_read_them(monkeypatch):
    bare = json.loads(json.dumps(DRAWN))
    bare["nodes"][1]["data"].pop("use_rag")
    answer_with(monkeypatch, bare)
    out = await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    assert next(n for n in out["nodes"] if n["type"] == "agentNode")["data"]["use_rag"] is True


async def test_the_model_is_told_what_an_agent_cannot_do_and_that_a_call_runs_one_agent(monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_LIVE_WORKFLOW_IDS", raising=False)
    model = answer_with(monkeypatch, DRAWN)
    await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    system = model.asked[0]["system"]
    assert "Google Calendar" in system and "while the call is live" in system         # what the agent must not be told to do
    assert "exactly ONE agent step" in system and "do not draw several agents" in system


async def test_where_the_older_pipeline_answers_several_agents_may_be_drawn(monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "false")
    monkeypatch.delenv("GEMINI_LIVE_WORKFLOW_IDS", raising=False)
    model = answer_with(monkeypatch, DRAWN)
    await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    system = model.asked[0]["system"]
    assert "one agent step for each desk" in system and "exactly ONE agent step" not in system


async def test_a_brief_longer_than_the_limit_is_refused(world):
    assert (await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Long", "template": "custom_ai", "prompt": "x" * 4001})).status_code == 422
    with pytest.raises(Exception):
        workflow_api.AIGenerateRequest(prompt="x" * 4001)


@pytest.mark.parametrize("reply", [
    CUT_OFF,                                                                                       # what the model sent at 250 tokens
    "I am sorry, I cannot do that.",
    json.dumps({"name": "x", "nodes": [{"id": "a", "type": "agentNode", "data": {"name": "No instructions"}}], "edges": []}),
    RuntimeError("model unavailable"),
])
async def test_a_reply_that_cannot_be_used_is_an_error_not_a_made_up_workflow(monkeypatch, reply):
    answer_with(monkeypatch, reply)
    with pytest.raises(HTTPException) as caught:
        await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    assert caught.value.status_code == 502


async def test_a_reply_wrapped_in_chatter_or_a_code_fence_still_works(monkeypatch):
    answer_with(monkeypatch, "Here you go:\n```json\n" + json.dumps(DRAWN) + "\n```\nLet me know!")
    out = await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    assert [n["id"] for n in out["nodes"]] == ["start-1", "agent-1"]


# --- the agent that is made from it -------------------------------------------------------------------------------------

async def test_an_agent_made_from_a_description_uses_the_drawn_workflow(world, monkeypatch):
    answer_with(monkeypatch, DRAWN)
    res = await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Lenovo Laptop Assistant", "template": "custom_ai", "prompt": DESCRIPTION})
    assert res.status_code == 200, res.text
    wf, nodes, edges = await graph_of(world, "Lenovo Laptop Assistant")
    prompts = {n.type: (n.data_json or {}).get("system_prompt") for n in nodes}
    assert prompts["agentNode"] == DESCRIPTION and prompts["startNode"] is None
    assert len(edges) == 1 and wf.description == DESCRIPTION                   # what the person asked for is kept whole on the agent


async def count_named(world, name):
    async with world.maker() as db:
        return len((await db.execute(select(Workflow).where(Workflow.name == name))).scalars().all())


async def test_a_drawn_agent_is_made_once(world, monkeypatch):
    """Turning the saved agent into its card used to fail on the drawn path, and the fallback then made a second agent."""
    answer_with(monkeypatch, DRAWN)
    res = await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Lenovo Laptop Assistant", "template": "custom_ai", "prompt": DESCRIPTION})
    assert res.status_code == 200 and res.json()["node_count"] == 2 and res.json()["edge_count"] == 1
    assert await count_named(world, "Lenovo Laptop Assistant") == 1
    wf, _, _ = await graph_of(world, "Lenovo Laptop Assistant")
    assert wf.tenant_id == world.tenants["acme"] and wf.is_active is False                  # the company's, and not live until chosen


async def test_a_step_id_the_model_used_twice_is_kept_once(monkeypatch):
    twice = json.loads(json.dumps(DRAWN))
    twice["nodes"].append({**twice["nodes"][1], "data": {**twice["nodes"][1]["data"], "system_prompt": "A second agent with the same id."}})
    answer_with(monkeypatch, twice)
    out = await workflow_api.generate_ai_workflow(workflow_api.AIGenerateRequest(prompt=DESCRIPTION))
    agents = [n for n in out["nodes"] if n["type"] == "agentNode"]
    assert len(agents) == 1 and agents[0]["data"]["system_prompt"] == DESCRIPTION


async def test_a_drawing_that_cannot_be_saved_leaves_one_agent_not_two(world, monkeypatch):
    from app import agents_api
    real, made = agents_api.WorkflowEdge, []

    def flaky(**fields):                                         # the first arrow fails to save, as a full disk would
        made.append(fields)
        if len(made) == 1:
            raise RuntimeError("disk full")
        return real(**fields)

    answer_with(monkeypatch, DRAWN)
    monkeypatch.setattr(agents_api, "WorkflowEdge", flaky)
    res = await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Lenovo Laptop Assistant", "template": "custom_ai", "prompt": DESCRIPTION})
    assert res.status_code == 200, res.text
    assert await count_named(world, "Lenovo Laptop Assistant") == 1                     # the half-made drawing was rolled back
    _, nodes, _ = await graph_of(world, "Lenovo Laptop Assistant")
    assert next(n for n in nodes if n.type == "agentNode").data_json["system_prompt"] == DESCRIPTION      # and the description is what remains


async def test_an_agent_keeps_the_description_when_the_model_fails(world, monkeypatch):
    """The incident: the model's reply was cut off, and the person's description was thrown away for a stock sentence."""
    answer_with(monkeypatch, CUT_OFF)
    res = await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Lenovo Laptop Assistant", "template": "custom_ai", "prompt": DESCRIPTION})
    assert res.status_code == 200, res.text
    wf, nodes, _ = await graph_of(world, "Lenovo Laptop Assistant")
    agent = next(n for n in nodes if n.type == "agentNode")
    assert agent.data_json["system_prompt"] == DESCRIPTION                                         # their words, not the stock sentence
    assert STOCK not in agent.data_json["system_prompt"]
    assert wf.description == DESCRIPTION
    assert agent.data_json["tools"] == [] and agent.data_json["tools_explicit"] is True            # and no tool picked from a word in it


async def test_an_agent_given_no_description_still_gets_the_stock_prompt(world, monkeypatch):
    answer_with(monkeypatch, RuntimeError("the model must not be asked"))
    res = await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Plain desk", "template": "blank"})
    assert res.status_code == 200, res.text
    wf, nodes, _ = await graph_of(world, "Plain desk")
    agent = next(n for n in nodes if n.type == "agentNode")
    assert agent.data_json["system_prompt"].startswith(STOCK + " Plain desk") and wf.description == "Custom Multi-Agent AI Workflow"
