import pytest
from unittest.mock import AsyncMock, patch
from app.agent_router import AgentRouter
from app.jev_client import JevDecisionEngine, JevRoutingResult

@pytest.mark.asyncio
async def test_agent_router_has_jev_engine():
    router = AgentRouter(llm=AsyncMock(), tool_executor=AsyncMock(), rag_engine=AsyncMock())
    assert hasattr(router, "jev_engine")
    assert isinstance(router.jev_engine, JevDecisionEngine)

@pytest.mark.asyncio
async def test_agent_router_uses_jev_for_transition():
    router = AgentRouter(llm=AsyncMock(), tool_executor=AsyncMock(), rag_engine=AsyncMock())
    router.jev_engine = JevDecisionEngine(api_key="mock_key")
    
    mock_jev_result = JevRoutingResult(
        target_node_id="target_printer_node",
        wants_escalation=False,
        urgency=2,
        confidence=0.95,
        is_fallback=False
    )
    
    with patch.object(router.jev_engine, "route_turn", new_callable=AsyncMock) as mock_route:
        mock_route.return_value = mock_jev_result
        
        # Candidate edges mockup with multiple outgoing options
        class MockEdge:
            def __init__(self, edge_id, target_id, condition):
                self.id = edge_id
                self.target_node_id = target_id
                self.condition = condition
                self.label = condition
        
        class MockNode:
            def __init__(self, node_id, name):
                self.id = node_id
                self.type = "agentNode"
                self.data_json = {"name": name}

        all_nodes = {
            "target_printer_node": MockNode("target_printer_node", "Hardware Agent"),
            "target_software_node": MockNode("target_software_node", "Software Agent")
        }
        candidate_edges = [
            MockEdge("edge_1", "target_printer_node", "Hardware inspection"),
            MockEdge("edge_2", "target_software_node", "Software diagnosis")
        ]
        
        # Test Jev routing call
        res = await router.determine_next_node(
            edges=candidate_edges,
            all_nodes=all_nodes,
            user_message="The machine on 3rd floor is flashing red and needs inspection",
            current_node=MockNode("reception_node", "Main Reception"),
            history=[]
        )
        assert res == "target_printer_node"
        mock_route.assert_called_once()

