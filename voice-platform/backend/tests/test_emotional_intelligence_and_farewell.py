import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agent_router import (
    AgentRouter,
    _detect_urgency_or_deadline,
    _detect_troubleshooting_failed,
    _detect_frustration,
    _generate_empathetic_ticket_request,
    _generate_ticket_confirmation,
    _generate_human_farewell,
)


def test_detect_urgency_or_deadline():
    assert _detect_urgency_or_deadline("I need this up and working by EOD")
    assert _detect_urgency_or_deadline("raise a ticket with utmost emergency")
    assert _detect_urgency_or_deadline("this is urgent, please help asap")
    assert _detect_urgency_or_deadline("today itself is the deadline")
    assert not _detect_urgency_or_deadline("can you please help me with my printer")
    assert not _detect_urgency_or_deadline("ok thanks")


def test_detect_troubleshooting_failed():
    assert _detect_troubleshooting_failed("nope, didnt really work")
    assert _detect_troubleshooting_failed("I already tried that, still failing")
    assert _detect_troubleshooting_failed("no luck with the restart")
    assert not _detect_troubleshooting_failed("yes it worked, thank you")
    assert not _detect_troubleshooting_failed("hello there")


def test_detect_frustration():
    assert _detect_frustration("I am fed up with this issue, I can't work")
    assert _detect_frustration("This is really frustrating and unacceptable")
    assert not _detect_frustration("Good morning, how are you?")


def test_generate_empathetic_ticket_request_eod():
    msg = "nope, didnt really work is it possible for u to raise a ticket with utmost emergency cause I need this up and working by EOD"
    resp = _generate_empathetic_ticket_request(msg)
    assert "end-of-day" in resp.lower() or "eod" in resp.lower()
    assert "emergency" in resp.lower() or "urgency" in resp.lower()
    assert "employee id or contact number" in resp.lower()


def test_generate_empathetic_ticket_request_trouble_failed():
    msg = "didn't work, can you please raise a ticket"
    resp = _generate_empathetic_ticket_request(msg)
    assert "sorry that didn't resolve it" in resp.lower() or "sorry" in resp.lower()
    assert "employee id or contact number" in resp.lower()


def test_generate_ticket_confirmation_eod():
    conf = _generate_ticket_confirmation(
        tkt="1268710",
        real_prio="P1",
        spoken_issue="SAP application freezing during transactions",
        user_message="EMP-9921",
        history=[
            {"role": "user", "content": "I need this up and working by EOD with utmost emergency"},
            {"role": "assistant", "content": "I completely understand the urgency..."},
            {"role": "user", "content": "EMP-9921"}
        ]
    )
    assert "1268710" in conf and "p1" in conf.lower() and "urgent" in conf.lower()
    for invented in ("sms", "eod", "on-call", "real-time", "actively"):      # never promise what we cannot guarantee
        assert invented not in conf.lower()
    assert "it team" in conf.lower()


def test_generate_human_farewell_with_eod_ticket():
    history = [
        {"role": "user", "content": "SAP is down, need it fixed by EOD emergency"},
        {"role": "assistant", "content": "I have created emergency ticket #1268710 with priority P1 for SAP issue. Real-time updates will be sent via SMS."},
        {"role": "user", "content": "thank you, that will be all"}
    ]
    farewell = _generate_human_farewell(history, "thank you, that will be all")
    assert "1268710" in farewell and "goodbye" in farewell.lower()
    for invented in ("sms", "eod", "real-time", "alerts"):
        assert invented not in farewell.lower()


def test_generate_human_farewell_without_ticket():
    farewell = _generate_human_farewell([], "thanks that's all")
    assert "glad i could help" in farewell.lower()
    assert "service desk" in farewell.lower()
    assert "goodbye" in farewell.lower()


@pytest.mark.asyncio
async def test_session_ticket_recall():
    mock_tool_executor = MagicMock()
    mock_tool_executor.get_tool_definitions.return_value = []
    mock_tool_executor.execute = AsyncMock()

    router = AgentRouter(
        llm=AsyncMock(),
        tool_executor=mock_tool_executor,
        rag_engine=MagicMock()
    )

    class MockNode:
        def __init__(self, node_id, node_type, name):
            self.id = node_id
            self.type = node_type
            self.data_json = {"name": name, "voice": "alloy", "llm": "gemini-2.5-flash", "prompt": "IT Helpdesk"}

    class MockWorkflow:
        id = 1
        name = "Support Flow"
        is_active = True
        nodes = [MockNode("support_agent", "agentNode", "IT Helpdesk")]
        edges = []

    router.get_active_workflow = AsyncMock(return_value=MockWorkflow())

    history = [
        {"role": "user", "content": "My SAP is freezing during ME21N transactions"},
        {"role": "assistant", "content": "I completely understand the urgency. Could you please share your Employee ID?"},
        {"role": "user", "content": "EMP-8831"},
        {"role": "assistant", "content": "I've got you covered. Your emergency ticket #1268710 has been created and assigned directly to our on-call engineers for immediate resolution before EOD."},
    ]

    # User asks to extract the last ticket raised
    res = await router.route_and_respond(
        session_id="session_recall_123",
        user_message="can u extract me the last ticket i had raised",
        history=history,
        current_node_id="support_agent"
    )

    # Agent MUST recall the ticket number 1268710 without asking the user for a ticket number!
    assert "1268710" in res["text"]
    assert "provide the ticket number" not in res["text"].lower()
    assert "what ticket number" not in res["text"].lower()
    assert "sap" in res["text"].lower() or "transaction" in res["text"].lower()
