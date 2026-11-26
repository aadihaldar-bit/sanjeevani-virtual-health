"""
Integration tests for POST /api/v1/decision/jev endpoint.
"""
import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from app.models import APIClient
from app.outbound_api_auth import generate_api_key, hash_api_key

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    asyncio.run(init_db())

def test_jev_endpoint_unauthorized():
    client = TestClient(app)
    response = client.post("/api/v1/decision/jev", json={
        "state": "Caller has printer trouble",
        "questions": {}
    })
    assert response.status_code == 401

def test_jev_endpoint_decision_fallback():
    client = TestClient(app)
    # Register/create an API key first
    email = f"jev.tester.{uuid.uuid4().hex[:8]}@stltech.in"
    reg_res = client.post("/api/v1/auth/register", json={
        "name": "Jev Test Suite",
        "email": email,
        "company": "STL AI Lab"
    })
    assert reg_res.status_code == 201
    api_key = reg_res.json()["api_key"]

    # When Jev is not configured or returns fallback
    response = client.post(
        "/api/v1/decision/jev",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "state": "Caller wants to reboot",
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": "Select next step",
                    "options": [{"id": "reboot", "name": "Reboot machine"}]
                }
            }
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "answers" in data
    assert data["fallback"] is True

def test_jev_endpoint_mock_success():
    client = TestClient(app)
    # Register an API client
    email = f"jev.success.{uuid.uuid4().hex[:8]}@stltech.in"
    reg_res = client.post("/api/v1/auth/register", json={
        "name": "Jev Success Suite",
        "email": email,
        "company": "STL AI Lab"
    })
    assert reg_res.status_code == 201
    api_key = reg_res.json()["api_key"]


    mock_answers = {
        "target_node": {"choice": "node_printer", "probability": 0.98},
        "wants_escalation": {"probability": 0.05},
        "urgency": {"score": 2}
    }

    with patch("app.jev_client.JevDecisionEngine.decide", new_callable=AsyncMock) as mock_decide:
        mock_decide.return_value = {
            "model": "jev-1.13.0",
            "answers": mock_answers,
            "fallback": False
        }

        response = client.post(
            "/api/v1/decision/jev",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "state": "Caller paper jam on laser printer",
                "questions": {
                    "target_node": {
                        "type": "choice",
                        "instructions": "Select handling agent",
                        "options": [{"id": "node_printer", "name": "Printer Agent"}]
                    }
                }
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "jev-1.13.0"
        assert data["fallback"] is False
        assert data["answers"]["target_node"]["choice"] == "node_printer"
        assert data["answers"]["urgency"]["score"] == 2
