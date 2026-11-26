"""
Unit & Integration Tests for Enterprise Voice Bot KPI Dashboard
Tests all API routes, filters, RBAC roles, exports, and template rendering.
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient
from kpi_dashboard.main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "stld-bkt-data-prod-voicebot-log-uscentral01-001" in data["gcs_bucket"]
    print(" PASS: /health")

def test_dashboard_html():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "STL Digital Voice Agent" in html
    assert "Executive Leadership Scorecard" in html
    assert "Speech & Acoustics" in html
    assert "Save to Bucket" in html
    print(" PASS: GET / (HTML rendering with Voice Agent obsidian design)")

def test_save_to_bucket():
    response = client.post("/api/save-to-bucket?range=weekly&role=all")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "details" in data
    assert "uploaded_blobs" in data["details"]
    assert len(data["details"]["uploaded_blobs"]) >= 3
    print(" PASS: POST /api/save-to-bucket -> Persisted manifests to GCS")




def test_kpi_weekly_all_roles():
    response = client.get("/api/kpis?range=weekly&role=all")
    assert response.status_code == 200
    data = response.json()
    assert "adoption_matrix" in data
    matrix = data["adoption_matrix"]
    assert matrix["total_calls"] >= 90
    assert "clearance_rate" in matrix
    assert "categories_breakdown" in data
    print(f" PASS: /api/kpis (weekly/all) -> {matrix['total_calls']} calls, {matrix['clearance_rate']} clearance")

def test_kpi_monthly_executive():
    response = client.get("/api/kpis?range=monthly&role=executive")
    assert response.status_code == 200
    data = response.json()
    assert "financial_roi" in data
    roi = data["financial_roi"]
    assert "estimated_savings_usd" in roi
    print(f" PASS: /api/kpis (monthly/executive) -> Savings: {roi['estimated_savings_usd']}")

def test_kpi_yearly_service_desk():
    response = client.get("/api/kpis?range=yearly&role=service_desk")
    assert response.status_code == 200
    data = response.json()
    assert "categories_breakdown" in data
    cats = data["categories_breakdown"]
    assert "SAP ERP Systems" in cats or "Hardware / Office Printers" in cats
    print(f" PASS: /api/kpis (yearly/service_desk) -> Categories: {list(cats.keys())[:3]}")

def test_kpi_custom_devops():
    response = client.get("/api/kpis?range=custom&start_date=2026-09-01&end_date=2026-09-28&role=devops")
    assert response.status_code == 200
    data = response.json()
    assert "latency_waterfall" in data
    assert len(data["latency_waterfall"]) >= 5
    print(f" PASS: /api/kpis (custom/devops) -> Waterfall: {len(data['latency_waterfall'])} subsystem benchmarks")

def test_export_markdown():
    response = client.get("/api/export?format=markdown&range=weekly&role=all")
    assert response.status_code == 200
    assert "# STL Digital Enterprise Voice Bot" in response.text
    assert "Autonomous Resolution Rate" in response.text
    print(" PASS: /api/export (markdown)")

def test_export_json():
    response = client.get("/api/export?format=json&range=weekly&role=all")
    assert response.status_code == 200
    data = response.json()
    assert "adoption_matrix" in data
    print(" PASS: /api/export (json)")

if __name__ == "__main__":
    print("\n--- Running KPI Dashboard Integration Tests ---")
    test_health()
    test_dashboard_html()
    test_save_to_bucket()
    test_kpi_weekly_all_roles()
    test_kpi_monthly_executive()
    test_kpi_yearly_service_desk()
    test_kpi_custom_devops()
    test_export_markdown()
    test_export_json()
    print("--- All 9 Integration Tests Passed Successfully! ---\n")

