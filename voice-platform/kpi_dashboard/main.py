"""
Enterprise Voice Bot KPI Dashboard - FastAPI Main Application
Serves interactive dashboard UI and REST telemetry endpoints.
Deployable to Localhost (8080) and Google Cloud Run.
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from .config import PORT, HOST, DEFAULT_GCS_BUCKET
from .gcs_client import fetch_telemetry, filter_telemetry, compute_dashboard_metrics, save_kpis_to_gcs

logger = logging.getLogger("kpi_dashboard.main")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="STL Digital Enterprise Voice Bot KPI Dashboard",
    description="Enterprise Telemetry & Incident Intelligence Portal for Voice AI",
    version="1.0.0"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    """Render the primary Enterprise KPI & Telemetry Dashboard."""
    # Pre-fetch telemetry
    calls, events, meta = fetch_telemetry(force_refresh=False)
    filtered_calls, filtered_events, filter_meta = filter_telemetry(
        calls=calls,
        events=events,
        range_type="weekly",
        role="all"
    )
    initial_metrics = compute_dashboard_metrics(filtered_calls, filtered_events, filter_meta)
    
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "metrics": initial_metrics,
            "metrics_json": json.dumps(initial_metrics),
            "current_date": datetime.now().strftime("%d - %m - %Y"),
            "bucket_name": DEFAULT_GCS_BUCKET
        }
    )


@app.get("/api/kpis", response_class=JSONResponse)
async def get_kpis_api(
    range: str = Query("weekly", description="Range type: weekly, monthly, yearly, custom"),
    date: Optional[str] = Query(None, description="Anchor date (YYYY-MM-DD or DD - MM - YYYY)"),
    start_date: Optional[str] = Query(None, description="Custom start date"),
    end_date: Optional[str] = Query(None, description="Custom end date"),
    role: str = Query("all", description="RBAC Role: all, executive, service_desk, devops")
):
    """Retrieve dynamic aggregated KPIs based on time window and RBAC persona."""
    try:
        calls, events, sync_meta = fetch_telemetry(force_refresh=False)
        filtered_calls, filtered_events, filter_meta = filter_telemetry(
            calls=calls,
            events=events,
            range_type=range,
            target_date_str=date,
            start_date_str=start_date,
            end_date_str=end_date,
            role=role
        )
        filter_meta["source"] = sync_meta.get("source", "gcs_bucket")
        filter_meta["last_sync_iso"] = sync_meta.get("last_sync_iso")
        
        metrics = compute_dashboard_metrics(filtered_calls, filtered_events, filter_meta)
        return JSONResponse(content=metrics)
    except Exception as exc:
        logger.error(f"Error computing KPIs: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/sync", response_class=JSONResponse)
async def trigger_gcs_sync():
    """Forces an immediate pull from GCS bucket, updates caches, and publishes latest manifests back."""
    try:
        calls, events, meta = fetch_telemetry(force_refresh=True)
        filtered_calls, filtered_events, filter_meta = filter_telemetry(calls, events, range_type="weekly", role="all")
        filter_meta["source"] = meta.get("source", "gcs_bucket")
        filter_meta["last_sync_iso"] = meta.get("last_sync_iso")
        metrics = compute_dashboard_metrics(filtered_calls, filtered_events, filter_meta)
        
        # Auto-publish latest KPI manifests to GCS bucket
        gcs_publish_res = save_kpis_to_gcs(metrics)

        return JSONResponse(content={
            "status": "success",
            "message": f"Successfully synced and published to GCS bucket {DEFAULT_GCS_BUCKET}",
            "calls_count": len(calls),
            "events_count": len(events),
            "gcs_publish": gcs_publish_res,
            "metrics": metrics
        })
    except Exception as exc:
        logger.error(f"Error during GCS sync: {exc}")
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}")


@app.post("/api/save-to-bucket", response_class=JSONResponse)
async def save_kpis_to_bucket():
    """Explicitly publish current aggregated Voice Bot KPIs directly to GCS bucket."""
    try:
        calls, events, meta = fetch_telemetry(force_refresh=False)
        filtered_calls, filtered_events, filter_meta = filter_telemetry(calls, events, range_type="weekly", role="all")
        filter_meta["source"] = meta.get("source", "gcs_bucket")
        filter_meta["last_sync_iso"] = meta.get("last_sync_iso")
        metrics = compute_dashboard_metrics(filtered_calls, filtered_events, filter_meta)
        
        res = save_kpis_to_gcs(metrics)
        return JSONResponse(content={
            "status": "success",
            "message": f"KPIs stored in bucket {DEFAULT_GCS_BUCKET}",
            "details": res
        })
    except Exception as exc:
        logger.error(f"Error publishing to bucket: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/export")
async def export_report(
    format: str = Query("json", description="Export format: 'json' or 'markdown'"),
    range: str = Query("weekly"),
    role: str = Query("all")
):
    """Export the current dashboard report as downloadable JSON or Markdown."""
    calls, events, _ = fetch_telemetry(force_refresh=False)
    filtered_calls, filtered_events, filter_meta = filter_telemetry(calls, events, range_type=range, role=role)
    metrics = compute_dashboard_metrics(filtered_calls, filtered_events, filter_meta)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if format.lower() == "markdown" or format.lower() == "md":
        md_content = f"""# STL Digital Enterprise Voice Bot — KPI Report
**Period:** {metrics['metadata']['period_label']}  
**Generated At:** {metrics['metadata']['last_synced_at']}  
**Role Lens:** {role.upper()}  
**Data Source:** {metrics['metadata']['source']} ({DEFAULT_GCS_BUCKET})

---

## 1. Platform Adoption & Performance Matrix
- **Total Calls Handled:** {metrics['adoption_matrix']['total_calls']}
- **Autonomous Resolution Rate:** {metrics['adoption_matrix']['clearance_rate']} ({metrics['adoption_matrix']['clearance_fraction']})
- **Human Escalations (Tier-2):** {metrics['adoption_matrix']['escalated']}
- **Incidents Logged (Symphony Summit):** {metrics['adoption_matrix']['incidents_raised']}
- **Average Handling Time (AHT):** {metrics['adoption_matrix']['avg_duration_formatted']}
- **Perceived Turn Latency:** {metrics['adoption_matrix']['avg_turn_latency_sec']} ({metrics['adoption_matrix']['avg_turn_latency_ms']} ms)
- **First Contact Resolution (FCR):** {metrics['adoption_matrix']['first_contact_resolution']}
- **Tool Execution Success Rate:** {metrics['adoption_matrix']['tool_success_rate']}
- **Average CSAT Rating:** {metrics['adoption_matrix']['avg_csat_score']}

---

## 2. Infrastructure Economics & Unit Rates
- **Blended AI Cost per Call:** {metrics['infrastructure_economics']['cost_per_resolved_call']}
- **Cost per 1,000 Voice Calls:** {metrics['infrastructure_economics']['cost_per_1k_calls']}
- **Total Batch AI Compute:** {metrics['infrastructure_economics']['total_batch_ai_cost']}
- **Human Triage Hours Saved:** {metrics['financial_roi']['hours_saved']}

---

## 3. Incident Breakdown by Category
"""
        for cat, cnt in metrics['categories_breakdown'].items():
            md_content += f"- **{cat}:** {cnt} tickets\n"
            
        md_content += f"""
---

## 4. Sub-Second Latency Architecture
"""
        for s in metrics['latency_waterfall']:
            md_content += f"- **{s['subsystem']}:** {s['latency_ms']} ms ({s['pct']}%)\n"

        filename = f"STL_VoiceBot_KPI_Report_{timestamp}.md"
        return Response(
            content=md_content,
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    else:
        filename = f"STL_VoiceBot_KPI_Report_{timestamp}.json"
        return Response(
            content=json.dumps(metrics, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )


@app.get("/health")
async def health_check():
    """Liveness probe for Cloud Run and orchestrators."""
    return {
        "status": "healthy",
        "service": "stl-voicebot-kpi-dashboard",
        "gcs_bucket": DEFAULT_GCS_BUCKET,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
