"""Which tools an agent has on a call. An agent made from a description or a template says so itself (often: none), and nothing is
guessed from a word in its name. The guess is what handed a laptop-booking agent STL's sample clinic doctors."""
import pytest
from sqlalchemy import select

from org_env import ADMIN, env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, world  # noqa: F401
from app import media_bridge
from app.gemini_graph import DOCUMENT_TOOL
from app.gemini_live_processor import GeminiLiveProcessor
from app.models import Workflow, WorkflowNode
from app.tool_executor import ToolExecutor

pytestmark = pytest.mark.asyncio

resolve = media_bridge.PipecatMediaSession._resolve_gemini_tools


# --- choosing the tools -------------------------------------------------------------------------------------------------------

def test_an_agent_that_names_no_tools_gets_none_whatever_its_words_are():
    booking = {"name": "Booking Assistant", "tools": [], "tools_explicit": True,
               "system_prompt": "You take appointment requests for the clinic. Ask the patient's name and the doctor they want."}
    assert resolve(booking) == []                                  # "appointment", "clinic", "doctor" no longer pull in the sample clinic
    support = {"name": "Customer support", "tools_explicit": True, "system_prompt": "You are the service desk support assistant."}
    assert resolve(support) == []                                  # nor "support" the STL ticket tools


def test_an_agent_that_may_read_documents_gets_only_the_document_search():
    assert resolve({"tools_explicit": True, "use_rag": True, "tools": []}) == [DOCUMENT_TOOL]
    assert resolve({"tools_explicit": True, "use_rag": True, "tools": [DOCUMENT_TOOL]}) == [DOCUMENT_TOOL]       # not twice
    assert resolve({"tools_explicit": True, "use_rag": False, "tools": []}) == []


def test_the_tools_a_person_gave_an_agent_are_exactly_the_tools_it_has():
    given = {"tools_explicit": True, "tools": ["mark_do_not_call", "schedule_callback"], "use_rag": True,
             "name": "Clinic reception", "system_prompt": "You answer for the clinic."}
    assert resolve(given) == ["mark_do_not_call", "schedule_callback", DOCUMENT_TOOL]


def test_older_agents_are_still_wired_by_their_words():
    """Not every agent was made through Architect: STL's demos rely on this, and it is left alone."""
    assert "find_doctor" in resolve({"name": "Clinic receptionist", "system_prompt": "You book doctor appointments for the clinic."})
    assert "create_symphony_ticket" in resolve({"name": "IT Service Desk", "system_prompt": "You raise tickets."})
    assert resolve({"name": "Anything", "system_prompt": "You chat."}) is None                # not said: the call's default applies


# --- what a call is given -----------------------------------------------------------------------------------------------------

def names(processor):
    return [d["name"] for d in processor._tool_declarations()]


async def test_a_call_with_an_empty_tool_list_has_no_tools_and_one_with_none_keeps_the_default():
    none = GeminiLiveProcessor(system_instruction="x", tool_executor=ToolExecutor(), tool_names=[])
    assert names(none) == []                                       # [] is an answer: no tools
    unsaid = GeminiLiveProcessor(system_instruction="x", tool_executor=ToolExecutor(), tool_names=None)
    assert {"find_doctor", "schedule_appointment"} <= set(names(unsaid))                         # None: today's default, unchanged
    docs = GeminiLiveProcessor(system_instruction="x", tool_executor=ToolExecutor(), tool_names=[DOCUMENT_TOOL])
    declared = docs._tool_declarations()
    assert [d["name"] for d in declared] == [DOCUMENT_TOOL] and declared[0]["parameters"]["required"] == ["question"]


class Session:
    def __init__(self):
        self.responses = []

    async def send_tool_response(self, responses):
        self.responses.append(responses)


class Recorder:
    def __init__(self):
        self.seen = []

    async def execute(self, name, args):
        self.seen.append((name, args))
        return {"result": "ok"}

    def get_tool_definitions(self):
        return []


async def search(kb_docs):
    executor = Recorder()
    p = GeminiLiveProcessor(system_instruction="x", tool_executor=executor, tool_names=[DOCUMENT_TOOL], kb_docs=kb_docs)
    await p._handle_tool_calls([{"id": "1", "name": DOCUMENT_TOOL, "args": {"question": "opening hours"}}], Session())
    return executor.seen[0][1]


async def test_the_document_search_uses_the_documents_the_agent_was_given_or_the_whole_company_when_none():
    assert (await search(["price list.pdf"]))["node_docs"] == ["price list.pdf"]
    assert (await search(None))["node_docs"] is None                                     # none named: every document of the company
    assert (await search([]))["node_docs"] is None


# --- the agents that get made -------------------------------------------------------------------------------------------------

async def agent_of(world, name):
    async with world.maker() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.name == name))).scalars().first()
        rows = (await db.execute(select(WorkflowNode).where(WorkflowNode.workflow_id == wf.id))).scalars().all()
    return wf, rows


@pytest.mark.parametrize("template", ["customer_support", "appointment_booking", "lead_qualification", "feedback_survey"])
async def test_an_agent_made_from_a_template_has_no_tools_picked_from_its_name(world, template):
    name = f"Acme {template}"
    assert (await world.post("/api/agents/", user=ACME_BOSS, json={"name": name, "template": template})).status_code == 200
    _, rows = await agent_of(world, name)
    agent = next(n.data_json for n in rows if n.type == "agentNode")
    assert agent["tools"] == [] and agent["tools_explicit"] is True
    assert resolve({**agent, "_node_name": agent["name"]}) == ([DOCUMENT_TOOL] if agent["use_rag"] else [])


async def test_a_blank_agent_has_no_tools_and_a_company_s_carries_no_stl_number(world):
    for user, company in ((ACME_BOSS, "Acme"), (ADMIN, "STL")):
        assert (await world.post("/api/agents/", user=user, json={"name": f"{company} blank", "template": "blank"})).status_code == 200
        _, rows = await agent_of(world, f"{company} blank")
        agent = next(n.data_json for n in rows if n.type == "agentNode")
        assert agent["tools"] == [] and agent["tools_explicit"] is True and agent["use_rag"] is True
        label = next(n.data_json["toolName"] for n in rows if n.type == "toolNode")
        assert ("97643" in label) == (company == "STL"), label            # STL's own copy is unchanged; a company's never shows STL's line
