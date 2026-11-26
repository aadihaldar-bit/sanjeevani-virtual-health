"""
Integration tests for the Outbound Voice API and API key auth.
"""
import pytest
import asyncio
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from app.outbound_api_auth import generate_api_key

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    asyncio.run(init_db())

def test_outbound_api_unauthorized():
    client = TestClient(app)
    response = client.post("/api/v1/outbound/call", json={
        "phone_number": "+919876543210",
        "message_context": "Test quota call"
    })
    assert response.status_code == 401
    assert "Authorization header" in response.json()["detail"]

def test_outbound_api_invalid_key():
    client = TestClient(app)
    response = client.post(
        "/api/v1/outbound/call",
        headers={"Authorization": "Bearer stl_outbound_invalidkey1234567890"},
        json={
            "phone_number": "+919876543210",
            "message_context": "Test quota call"
        }
    )
    assert response.status_code == 401

def test_admin_client_creation_and_api_call(monkeypatch):
    monkeypatch.setenv("REQUIRE_FIREBASE_AUTH", "false")      # the admin endpoint needs a Google sign-in otherwise
    client = TestClient(app)
    
    # 1. Create API Client via admin endpoint (bypass strict auth for local dev test)
    create_res = client.post("/api/admin/api-clients", json={
        "client_name": "GCP Monitoring System",
        "client_company": "Internal Dev",
        "daily_call_quota": 50
    })
    assert create_res.status_code == 201
    data = create_res.json()
    assert "api_key" in data
    api_key = data["api_key"]
    assert api_key.startswith("stl_outbound_")

    # 2. Check usage endpoint with valid API key
    usage_res = client.get("/api/v1/outbound/usage", headers={"Authorization": f"Bearer {api_key}"})
    assert usage_res.status_code == 200
    usage = usage_res.json()
    assert usage["client_name"] == "GCP Monitoring System"
    assert usage["daily_call_quota"] == 50
    assert usage["calls_today"] == 0

    print("Outbound API integration tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(init_db())
    test_outbound_api_unauthorized()
    test_outbound_api_invalid_key()
    test_admin_client_creation_and_api_call()
