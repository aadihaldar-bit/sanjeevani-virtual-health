import pytest
from unittest.mock import AsyncMock, patch
from app.agent_router import (
    AgentRouter,
    _has_technical_symptom,
    _detect_app_or_system,
)

def test_has_technical_symptom():
    # Merely mentioning an app or generic word is NOT a technical symptom
    assert not _has_technical_symptom("I need to raise a SAP ticket")
    assert not _has_technical_symptom("SAP issue")
    assert not _has_technical_symptom("printer problem")
    assert not _has_technical_symptom("ERP")
    assert not _has_technical_symptom("Can you help me with VPN")
    assert not _has_technical_symptom("9000000001")
    assert not _has_technical_symptom("My employee id is 123456")

    # True symptoms
    assert _has_technical_symptom("My session is locked on transaction ME21N")
    assert _has_technical_symptom("The printer has a paper jam in tray 2")
    assert _has_technical_symptom("VPN keeps disconnecting with timeout error")
    assert _has_technical_symptom("Outlook crashed and won't open")
    assert _has_technical_symptom("Getting access denied 403 on portal")
    assert _has_technical_symptom("Laptop screen is completely black and won't turn on")

def test_detect_app_or_system():
    assert _detect_app_or_system("I need to raise a SAP ticket") == "sap"
    assert _detect_app_or_system("My printer is broken") == "printer"
    assert _detect_app_or_system("Can't connect to VPN") == "vpn"
    assert _detect_app_or_system("Outlook email issue") == "outlook"
    assert _detect_app_or_system("Something is wrong") is None

@pytest.mark.asyncio
async def test_summarize_ticket_issue_avoids_ids():
    router = AgentRouter(llm=AsyncMock(), tool_executor=AsyncMock(), rag_engine=AsyncMock())
    # If the user only gave an ID on the last turn, summary should use earlier problem context
    spoken, full_desc = await router._summarize_ticket_issue(
        user_message="9000000001",
        history=[
            {"role": "user", "text": "I need to raise a SAP ticket"},
            {"role": "assistant", "text": "What specific error are you experiencing with SAP?"},
            {"role": "user", "text": "My transaction ME21N is locked and crashing with timeout"},
            {"role": "assistant", "text": "Could you provide your Employee ID?"}
        ]
    )
    assert "9000000001" not in spoken
    assert "9000000001" not in full_desc
    assert "SAP" in full_desc or "locked" in full_desc or "timeout" in full_desc

@pytest.mark.asyncio
async def test_agent_node_clarifies_unspecified_app_issue():
    from unittest.mock import MagicMock
    mock_tool_executor = MagicMock()
    mock_tool_executor.get_tool_definitions.return_value = [
        {"name": "create_symphony_ticket", "description": "Create a Symphony incident ticket"}
    ]
    mock_tool_executor.execute = AsyncMock()

    mock_rag_engine = MagicMock()
    mock_rag_engine.query = AsyncMock(return_value="")

    router = AgentRouter(
        llm=AsyncMock(),
        tool_executor=mock_tool_executor,
        rag_engine=mock_rag_engine
    )

    class MockNode:
        def __init__(self, node_id, node_type, name):
            self.id = node_id
            self.type = node_type
            self.data_json = {
                "name": name,
                "voice": "alloy",
                "llm": "gemini-2.5-flash",
                "prompt": "You are IT helpdesk.",
                "tools": ["create_symphony_ticket"]
            }

    class MockWorkflow:
        id = 1
        name = "IT Support Flow"
        is_active = True
        nodes = [MockNode("it_agent", "agentNode", "IT Helpdesk")]
        edges = []

    router.get_active_workflow = AsyncMock(return_value=MockWorkflow())
    router.llm.generate = AsyncMock(return_value="I can help with that.")

    # User says "I need to raise a SAP ticket" with no technical symptoms
    res = await router.route_and_respond(
        session_id="test_session",
        user_message="I need to raise a SAP ticket",
        history=[],
        current_node_id="it_agent"
    )

    assert res["tool_called"] is None
    # Must ask for specific error/symptom for SAP, NOT employee ID
    assert "SAP" in res["text"]
    assert "specific error or issue" in res["text"]
    assert "employee id" not in res["text"].lower()
