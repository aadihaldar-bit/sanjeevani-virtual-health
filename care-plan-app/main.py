import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from config import settings
from db import PATIENT_RECORDS_BASE_DIR, db_manager, ensure_patient_directories, save_patient_document_dedup, scan_local_patient_documents
from schemas import normalize_and_validate_e164, validate_iso_dob
from webhook import push_prescription_to_voicebot
from ai_brain.extractor import extract_prescription_text
from ai_brain.reasoner import analyze_prescription_and_plan_journey
from vitals_api import router as vitals_router
from access_gate import access_middleware, login_page, login_submit
import voicebot
import alerts
import audit
import chat
import events
from pharmacy_finder import (
    address_suggestions,
    find_nearby_pharmacies,
    geocode_address,
    recommend_sanjeevani_branches,
    reverse_geocode,
)

# In-memory Live Telemetry Tracker (preserved for dashboard)
analytics_state = {
    "total_calls": 14820,
    "answered_calls": 13101,
    "csat_score": 4.88,
    "total_pharmacy_orders": 3420,
    "order_breakdown": {
        "medicine_only": 2180,
        "rpm_only": 410,
        "combo_meds_and_rpm": 830
    },
    "total_revenue_aed": 974700.0,
    "recent_events": [
        {
            "id": "EVT-1",
            "type": "clarification",
            "title": "Voice Doubt Clarified",
            "desc": "Patient Sonika queried dosage timing for Oflazest OZ (Answered in 1.2s)",
            "time": "10s ago",
            "flag": "VOICE_AI"
        },
        {
            "id": "EVT-2",
            "type": "order",
            "title": "Pharmacy Courier Dispatched",
            "desc": "Order #SNJ-RX-8419 to Al Reem Island (Same-day courier)",
            "time": "1m ago",
            "flag": "MEDICINE_ONLY"
        },
        {
            "id": "EVT-3",
            "type": "rpm",
            "title": "Smart RPM Device Added",
            "desc": "Smart Connected BP Monitor added to refill order (Patient Fatima H.)",
            "time": "3m ago",
            "flag": "RPM_ONLY"
        }
    ]
}

app = FastAPI(
    title="Sanjeevani Virtual Health Store - AI Journey & Voice Bot Orchestrator",
    description="AI-Led Engagement Layer with MongoDB Persistence & Voice Bot Server-to-Server Push",
    version="2.0.0"
)

# Enable CORS for cross-origin integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static folder for email preview and dashboard
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


app.include_router(vitals_router)
app.include_router(voicebot.router)
app.include_router(alerts.router)
app.include_router(chat.router)
app.include_router(audit.router)
app.include_router(events.router)

# Shared password for public preview links (only when ACCESS_PASSWORD is set)
app.middleware("http")(access_middleware)
app.get("/login", include_in_schema=False)(login_page)
app.post("/login", include_in_schema=False)(login_submit)


@app.get("/")
def serve_landing():
    """Serves the public landing page."""
    landing_file = os.path.join("static", "landing.html")
    if os.path.exists(landing_file):
        return FileResponse(landing_file)
    return {"message": "Sanjeevani AI-Led Virtual Health Store API is active. Go to /docs for Swagger."}


@app.get("/app")
def serve_dashboard():
    """Serves the interactive web demo dashboard."""
    index_file = os.path.join("static", "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Dashboard file not found."}


@app.get("/pickup")
def serve_pickup():
    """The nearby-pharmacy finder now lives in the app's Pharmacy view."""
    return RedirectResponse("/app#pharmacy")


@app.get("/api/v1/pickup/nearby-pharmacies")
async def nearby_pharmacies(lat: float, lon: float, radius_m: Optional[int] = None):
    """Find pharmacies near a location for medicine pickup, ranked by distance + open-now
    (and by customer rating when a Foursquare key is configured). Map tiles are OpenStreetMap.

    Example: GET /api/v1/pickup/nearby-pharmacies?lat=24.4539&lon=54.3773
    """
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid lat/lon.")
    if radius_m is not None and not (500 <= radius_m <= 8000):
        raise HTTPException(status_code=400, detail="radius_m must be between 500 and 8000 metres.")
    try:
        result = await find_nearby_pharmacies(lat, lon, radius_m)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch nearby pharmacies: {exc}")
    return JSONResponse(content=result)


@app.get("/api/v1/pickup/geocode")
async def geocode_pickup_address(address: str):
    """Resolve an address or landmark before finding nearby pickup pharmacies."""
    address = " ".join(address.split())
    if not (3 <= len(address) <= 200):
        raise HTTPException(status_code=400, detail="Enter an address or landmark between 3 and 200 characters.")
    try:
        location = await geocode_address(address)
    except Exception:
        raise HTTPException(status_code=502, detail="Address search is temporarily unavailable. Please try again.")
    if location is None:
        raise HTTPException(status_code=404, detail="Address not found. Try adding the city or a nearby landmark.")
    return JSONResponse(content=location)


@app.get("/api/v1/pickup/address-suggestions")
async def pickup_address_suggestions(q: str):
    """Return matching real addresses for the pickup address autocomplete."""
    q = " ".join(q.split())
    if not (3 <= len(q) <= 200):
        return JSONResponse(content={"suggestions": []})
    try:
        suggestions = await address_suggestions(q)
    except Exception:
        suggestions = []
    return JSONResponse(content={"suggestions": suggestions})


@app.get("/presentation")
def serve_presentation():
    """Serves the executive presentation slide deck."""
    pres_file = os.path.join("static", "presentation.html")
    if os.path.exists(pres_file):
        return FileResponse(pres_file)
    return {"message": "Presentation file not found."}


@app.get("/sanjeevani_demo_presentation.pptx")
@app.get("/sanjeevani_presentation.pptx")
@app.get("/sanjeevani_presentation_modern.pptx")
@app.get("/sanjeevani_healthcare_demo.pptx")
@app.get("/sanjeevani_executive_deck.pptx")
def download_presentation_pptx():
    """Downloads the official Sanjeevani healthcare demo PowerPoint file."""
    for pptx_file in ["sanjeevani_demo_presentation.pptx", "sanjeevani_virtual_health_store.pptx", "sanjeevani_healthcare_demo.pptx", "sanjeevani_executive_deck.pptx", "sanjeevani_presentation_modern.pptx", "sanjeevani_presentation.pptx"]:
        if os.path.exists(pptx_file):
            return FileResponse(pptx_file, filename="sanjeevani_healthcare_demo.pptx", media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    return {"message": "PPTX file not found."}


MAX_HISTORY_DOCS = int(os.getenv("MAX_HISTORY_DOCS", "6"))      # stored documents sent to the model as history, so a long record cannot stall a plan


@app.get("/landing")
def serve_landing_explicit():
    """Serves the landing page."""
    landing_file = os.path.join("static", "landing.html")
    if os.path.exists(landing_file):
        return FileResponse(landing_file)
    return {"message": "landing.html not found."}


@app.get("/api/v1/pickup/sanjeevani-branches")
async def get_sanjeevani_branches(lat: Optional[float] = None, lon: Optional[float] = None, address: Optional[str] = None):
    """Sanjeevani Pharmacy pickup locations ranked nearest first. Takes coordinates, or an address to geocode."""
    origin_lat, origin_lon = lat, lon
    if (origin_lat is None or origin_lon is None) and address and address.strip():
        try:
            loc = await geocode_address(address.strip())
            if loc:
                origin_lat, origin_lon = loc["lat"], loc["lon"]
        except Exception:
            pass
    if origin_lat is None or origin_lon is None:
        origin_lat, origin_lon = 24.49537, 54.40524          # Al Reem Island, Abu Dhabi
    branches = recommend_sanjeevani_branches(origin_lat, origin_lon)
    return JSONResponse(content={"origin": {"lat": origin_lat, "lon": origin_lon}, "count": len(branches),
                                 "nearest": branches[0] if branches else None, "branches": branches})


@app.get("/api/v1/pickup/reverse-geocode")
async def reverse_geocode_address(lat: float, lon: float):
    """The address for a GPS position, for the Use my location button."""
    return JSONResponse(content=await reverse_geocode(lat, lon))


# ---------------------------------------------------------------- stored patient documents (prescriptions and reports kept on disk)
def _patient_dir_id(patient_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", str(patient_id).strip())


def _document_path(patient_id: str, filename: str) -> str:
    """The file's path inside that patient's folder only: the id is sanitised and only the base name of the file is used."""
    safe_id, safe_name = _patient_dir_id(patient_id), os.path.basename(filename)
    path = os.path.join(PATIENT_RECORDS_BASE_DIR, safe_id, safe_name)
    if not os.path.exists(path):
        for sub in ("prescriptions", "reports"):          # folders an older version used
            legacy = os.path.join(PATIENT_RECORDS_BASE_DIR, safe_id, sub, safe_name)
            if os.path.exists(legacy):
                return legacy
    return path


@app.get("/api/v1/patient-records/{patient_id}/documents", tags=["Patient Records"])
async def list_patient_documents(patient_id: str, request: Request):
    """Lists the prescription scans and reports stored for a patient."""
    safe_id = _patient_dir_id(patient_id)
    docs = scan_local_patient_documents(safe_id)
    audit.log("document.listed", safe_id, {"created": len(docs)}, request)
    return JSONResponse(content={"status": "success", "patient_id": safe_id, "documents": docs, "count": len(docs)})


@app.get("/api/v1/patient-records/{patient_id}/documents/{filename:path}", tags=["Patient Records"])
async def view_patient_document_flat(patient_id: str, filename: str, request: Request, download: bool = False):
    """Serves one stored document from the patient's folder."""
    path = _document_path(patient_id, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Document file not found.")
    audit.log("document.viewed", _patient_dir_id(patient_id), None, request)
    return FileResponse(path, headers={"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{os.path.basename(path)}"'})


@app.get("/api/v1/patient-records/{patient_id}/documents/{category}/{filename}", tags=["Patient Records"])
async def view_patient_document(patient_id: str, category: str, filename: str, request: Request, download: bool = False):
    """Older route kept for compatibility."""
    return await view_patient_document_flat(patient_id, filename, request, download)


@app.delete("/api/v1/patient-records/{patient_id}/documents/{filename:path}", tags=["Patient Records"])
async def delete_patient_document(patient_id: str, filename: str, request: Request):
    """Deletes one stored document."""
    path = _document_path(patient_id, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Document file not found.")
    try:
        os.remove(path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")
    audit.log("document.deleted", _patient_dir_id(patient_id), None, request)
    return JSONResponse(content={"status": "success", "message": f"File '{os.path.basename(path)}' deleted successfully."})


# ==============================================================================
# 1. CORE PRESCRIPTION TRIGGER (Parse -> Persist to MongoDB -> Push to Voice Bot)
# ==============================================================================
@app.post("/api/v1/journey/prescription-trigger")
async def trigger_prescription_journey(
    request: Request,
    # Mandatory / Primary Prescription Inputs
    prescription_text: Optional[str] = Form(None, description="Raw prescription text or doctor notes"),
    prescription_file: Optional[UploadFile] = File(None, description="Single prescription image or PDF"),
    prescription_files: Optional[List[UploadFile]] = File(None, description="Several prescription images or PDFs"),
    
    # Optional Patient Medical History Inputs (Text / Past Files / Record)
    history_text: Optional[str] = Form(None, description="Previous Medical History (Chronic conditions, allergies, past medications)"),
    history_file: Optional[UploadFile] = File(None, description="Single Previous Prescription Image/PDF"),
    history_files: Optional[List[UploadFile]] = File(None, description="Multiple Previous Prescription or Medical Record Images/PDFs"),
    included_history_documents: Optional[str] = Form(None, description="JSON list of stored document names to use as history; none sent means all stored ones"),

    # Mandatory Patient Identity & Contact for Voice Bot Outbound Calls
    mobile_number: str = Form(..., description="Mandatory Patient Mobile in E.164 format (+9715...)"),
    identity_dob: str = Form(..., description="Mandatory Date of Birth (YYYY-MM-DD) for on-call identity check"),
    
    # Optional Demographics
    patient_id: Optional[str] = Form(None, description="Unique Patient Identifier (MRN)"),
    patient_name: Optional[str] = Form(None, description="Full Name of the patient"),
    patient_age: Optional[int] = Form(None, description="Patient Age"),
    patient_gender: Optional[str] = Form(None, description="Gender"),
    email: Optional[str] = Form(None, description="Optional email address"),
    delivery_address: Optional[str] = Form(None, description="Patient Delivery Address"),
    chronic_conditions: Optional[str] = Form(None, description="Known medical history"),
    blood_pressure: Optional[str] = Form(None, description="Recent BP reading"),
    weight_kg: Optional[float] = Form(None, description="Weight in kg"),
    insurance_tier: Optional[str] = Form(None, description="Insurance Tier"),
    consent_calls: bool = Form(False, description="The patient asked for follow-up calls about this prescription")
):
    """
    Core AI Journey Ingestion Endpoint:
    1. Validates mandatory E.164 phone number & YYYY-MM-DD identity DOB.
    2. Ingests prescription in any format (Text, File, or Both) and extracts text via Gemini Vision OCR.
    3. Feeds context into AI Brain & extracts structured medicines with computed end dates.
    4. Persists patient and prescription to MongoDB (viewable in MongoDB Compass).
    5. Pushes the structured prescription to Voice Bot server-to-server endpoint (POST /api/v1/prescriptions).
    """
    req_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "#"*80)
    print(f" [REQUEST RECEIVED] POST /api/v1/journey/prescription-trigger | {req_time}")
    print("#"*80)

    # 1. Validate mandatory mobile number in E.164 format
    try:
        validated_mobile = normalize_and_validate_e164(mobile_number)
    except ValueError as ve:
        print(f" [!] Validation Error on mobile_number: {ve}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation Error: {str(ve)}"
        )

    # 2. Validate mandatory identity Date of Birth (YYYY-MM-DD)
    try:
        validated_dob = validate_iso_dob(identity_dob)
    except ValueError as ve:
        print(f" [!] Validation Error on identity_dob: {ve}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation Error: {str(ve)}"
        )

    # 3. Validate Prescription Input Presence
    has_rx_files = bool(prescription_files and any(pf and pf.filename for pf in prescription_files)) or bool(prescription_file and prescription_file.filename)
    if (not prescription_text or not prescription_text.strip()) and not has_rx_files:
        print(" [!] Validation Error: Neither prescription_text nor prescription file provided.")
        raise HTTPException(
            status_code=400,
            detail="At least one prescription input is required: 'prescription_text' or prescription files."
        )

    # Several prescription files can come in at once, and the older single-file field still works. Identical files count once.
    rx_file_bytes_list: List[bytes] = []
    rx_file_mimes: List[str] = []
    rx_file_names: List[str] = []
    for pf in list(prescription_files or []) + ([prescription_file] if prescription_file else []):
        if pf and pf.filename:
            print(f" -> Uploaded Prescription File: {pf.filename} (Type: {pf.content_type})")
            pb = await pf.read()
            if pb and pb not in rx_file_bytes_list:
                rx_file_bytes_list.append(pb)
                rx_file_mimes.append(pf.content_type or "application/octet-stream")
                rx_file_names.append(pf.filename)
    file_bytes = rx_file_bytes_list[0] if rx_file_bytes_list else None
    file_mime = rx_file_mimes[0] if rx_file_mimes else None
    hist_file_names: List[str] = []

    hist_file_bytes_list: List[bytes] = []
    if history_files:
        for hf in history_files:
            if hf and hf.filename:
                print(f" -> Uploaded Medical History File: {hf.filename} (Type: {hf.content_type})")
                hb = await hf.read()
                if hb:
                    hist_file_bytes_list.append(hb)
    if history_file and history_file.filename:
        print(f" -> Uploaded Medical History File: {history_file.filename} (Type: {history_file.content_type})")
        hb = await history_file.read()
        if hb and hb not in hist_file_bytes_list:
            hist_file_bytes_list.append(hb)

    # 4. Build Clean Patient Profile
    resolved_patient_id = patient_id.strip() if patient_id and patient_id.strip() else ""
    if not resolved_patient_id and mobile_number:
        # Match both mobile AND patient name so family members sharing a phone (e.g. father & child) don't collide
        existing_pat = db_manager.get_patient_by_phone_and_name(mobile_number.strip(), patient_name.strip() if patient_name else None)
        if existing_pat and existing_pat.get("patient_id"):
            resolved_patient_id = existing_pat.get("patient_id")
            print(f" [Patient Match] Reusing existing patient ID '{resolved_patient_id}' for '{patient_name}' ({mobile_number.strip()}).")

    if not resolved_patient_id:
        resolved_patient_id = f"SNJ-PAT-{int(time.time()) % 100000}"
    resolved_patient_name = patient_name.strip() if patient_name and patient_name.strip() else "Valued Patient"
    resolved_delivery_address = delivery_address.strip() if delivery_address and delivery_address.strip() else "Registered Address on File"

    # Keep the uploaded documents in the patient's folder (identical files stored once), and offer stored ones as history.
    ensure_patient_directories(resolved_patient_id)
    stored_new = 0
    for fname, fbytes in zip(rx_file_names, rx_file_bytes_list):
        stored_new += 0 if save_patient_document_dedup(resolved_patient_id, fname, fbytes).get("is_duplicate") else 1
    for hf in list(history_files or []) + ([history_file] if history_file else []):
        if hf and hf.filename and hf.filename not in hist_file_names:
            hist_file_names.append(hf.filename)
    for fname, hbytes in zip(hist_file_names, hist_file_bytes_list):
        save_patient_document_dedup(resolved_patient_id, fname, hbytes)
    if stored_new:
        audit.log("document.stored", resolved_patient_id, {"created": stored_new}, request)

    allowed_names = None
    if included_history_documents is not None:
        try:
            parsed = json.loads(included_history_documents)
            allowed_names = set(parsed) if isinstance(parsed, list) else None
        except Exception as pe:
            print(f" [!] Could not parse included_history_documents JSON: {pe}")
    recalled = 0
    for doc in reversed(scan_local_patient_documents(resolved_patient_id)):          # newest first
        if allowed_names is not None and doc["filename"] not in allowed_names:
            continue
        if recalled >= MAX_HISTORY_DOCS:
            break
        path = _document_path(resolved_patient_id, doc["filename"])
        try:
            with open(path, "rb") as rf:
                content = rf.read()
        except OSError as read_err:
            print(f" [!] Could not read stored document {doc['filename']}: {read_err}")
            continue
        if content not in hist_file_bytes_list and content not in rx_file_bytes_list:
            hist_file_bytes_list.append(content)
            recalled += 1

    patient_profile = {
        "patient_id": resolved_patient_id,
        "patient_name": resolved_patient_name,
        "mobile_number": validated_mobile,
        "identity_dob": validated_dob,
        "patient_age": patient_age,
        "patient_gender": patient_gender.strip() if patient_gender and patient_gender.strip() else None,
        "email": email.strip() if email and email.strip() else None,
        "delivery_address": resolved_delivery_address,
        "chronic_conditions": chronic_conditions.strip() if chronic_conditions and chronic_conditions.strip() else None,
        "blood_pressure": blood_pressure.strip() if blood_pressure and blood_pressure.strip() else None,
        "weight_kg": weight_kg,
        "insurance_tier": insurance_tier.strip() if insurance_tier and insurance_tier.strip() else "Standard"
    }

    clean_context = {k: v for k, v in patient_profile.items() if v is not None}
    print(f" -> Form Demographics Ingested (Mobile: {validated_mobile}, DOB: {validated_dob})")

    # 5. Run ⚡ High-Speed Single-Pass Multimodal AI Reasoner (Current Rx + History Fusion)
    start_proc = time.time()
    # The Gemini call blocks, so it runs in a worker thread and the rest of the app keeps answering.
    journey_result = await run_in_threadpool(
        analyze_prescription_and_plan_journey,
        prescription_text=prescription_text,
        image_bytes=file_bytes,
        image_mime_type=file_mime,
        prescription_images=rx_file_bytes_list,
        prescription_image_mimes=rx_file_mimes,
        history_text=history_text,
        history_images=hist_file_bytes_list,
        patient_data=clean_context
    )
    extracted_rx = journey_result.get("extracted_transcription") or prescription_text or "Prescription ingested and analyzed."
    patient_history = journey_result.get("patient_history", {})
    proc_duration = time.time() - start_proc
    print(f" -> [AI Journey Total Processing Time]: {proc_duration:.2f}s")

    # Extract structured medicines list
    medicines = journey_result.get("clinical_summary", {}).get("medications", [])
    prescription_id = f"RX-SNJ-{int(time.time() * 1000) % 1000000}"

    # 6. Persist Patient & Prescription to MongoDB (Viewable in MongoDB Compass)
    mongo_status = "unconnected"
    if db_manager.is_connected():
        try:
            db_manager.upsert_patient(patient_profile)
            save_res = db_manager.save_prescription(
                prescription_id=prescription_id,
                patient_id=resolved_patient_id,
                raw_text=extracted_rx,
                medicines=medicines,
                parsed_data=journey_result,
                push_status="PARSED"
            )
            mongo_status = save_res.get("status", "persisted")
        except Exception as dbe:
            print(f" [!] [MongoDB Error]: {dbe}")
    else:
        print(" [!] [MongoDB Notice] MongoDB is currently offline or unreachable. Skipping persistence.")

    # 7. Push to Voice Bot Integration Seam (POST /api/v1/prescriptions)
    push_payload = {
        "prescription_id": prescription_id,
        "patient": {
            "patient_id": resolved_patient_id,
            "patient_name": resolved_patient_name,
            "mobile_number": validated_mobile,
            "identity_dob": validated_dob,
            "patient_age": patient_age,
            "patient_gender": patient_gender,
            "delivery_address": resolved_delivery_address,
            "chronic_conditions": patient_profile.get("chronic_conditions")
        },
        "patient_history": patient_history,
        "medicines": medicines,
        "clinical_summary": journey_result.get("clinical_summary"),
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    audit.log("careplan.created", prescription_id, {"medicines": len(medicines), "consent_calls": consent_calls}, request)
    voicebot.remember_plan(prescription_id, push_payload["patient"], medicines, journey_result.get("clinical_summary"),
                           journey_result.get("patient_explanation"))
    follow_up_calls = {"status": "not_connected"}
    push_result = {"status": "DISPATCHED", "endpoint": settings.VOICE_BOT_ENDPOINT_URL}
    if voicebot.configured():
        # The STL voicebot schedules real phone calls, so it only hears about prescriptions the patient asked to be called about.
        if not consent_calls:
            follow_up_calls = {"status": "not_requested"}
        elif not voicebot.number_allowed(validated_mobile):
            follow_up_calls = {"status": "not_allowed", "detail": "calls from this preview only go to the demo numbers"}
        else:
            follow_up_calls = await voicebot.push_prescription(voicebot.to_voicebot_prescription(
                prescription_id, push_payload["patient"], medicines, journey_result.get("clinical_summary"), True))
        follow_up_calls["phone_number"] = validated_mobile
        push_result = {"status": follow_up_calls["status"], "endpoint": voicebot._base() + "/api/v1/prescriptions"}
        print(f" -> [Voicebot] follow-up calls: {follow_up_calls['status']}")
    else:
        try:
            # Non-blocking async execution or fast dispatch
            import asyncio
            asyncio.create_task(push_prescription_to_voicebot(
                prescription_id=prescription_id,
                payload=push_payload
            ))
            push_result = {"success": True, "status": "DISPATCHED_ASYNC", "endpoint": settings.VOICE_BOT_ENDPOINT_URL}
        except Exception as pe:
            print(f" [!] Voicebot push dispatch notice: {pe}")

    print("\n" + "="*80)
    print(f" [ORCHESTRATION COMPLETE] Rx ID: {prescription_id} | MongoDB: {mongo_status} | Duration: {proc_duration:.2f}s")
    print("="*80 + "\n")

    # 8. Return Unified JSON Response Instantly
    return JSONResponse(
        content={
            "status": "success",
            "message": "AI Prescription Journey successfully orchestrated and persisted.",
            "prescription_id": prescription_id,
            "latency_seconds": round(proc_duration, 2),
            "database_persistence": {
                "engine": "MongoDB",
                "database": settings.MONGODB_DB_NAME,
                "status": mongo_status,
                "viewable_in": "MongoDB Compass"
            },
            "voice_bot_integration": {
                "endpoint": settings.VOICE_BOT_ENDPOINT_URL,
                "push_status": "PUSHED",
                "details": push_result,
                "follow_up_calls": follow_up_calls
            },
            "patient_context": clean_context,
            "patient_history": patient_history,
            "extracted_prescription": extracted_rx,
            "structured_medicines": medicines,
            "ai_clinical_summary": journey_result.get("clinical_summary"),
            "ai_patient_explanation": journey_result.get("patient_explanation"),
            "ai_next_best_actions": journey_result.get("next_best_actions")
        }
    )


# ==============================================================================
# PATIENT MEDICAL HISTORY AUTO-RECALL ENDPOINT
# ==============================================================================
@app.get("/api/v1/patients", tags=["Patients"])
async def list_registered_patients():
    """
    Returns the real list of registered patients directly from MongoDB Atlas.
    """
    db_patients = db_manager.get_all_patients()
    formatted = []
    for p in db_patients:
        name = p.get("patient_name") or "Patient"
        parts = name.strip().split(" ")
        formatted.append({
            "external_id": p.get("patient_id") or "",
            "patient_id": p.get("patient_id") or "",
            "name": name,
            "first_name": parts[0] if parts else "",
            "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
            "phone_e164": p.get("mobile_number") or "",
            "mobile_number": p.get("mobile_number") or "",
            "identity_dob": p.get("identity_dob") or "",
            "patient_age": p.get("patient_age"),
            "gender": p.get("patient_gender") or "",
            "patient_gender": p.get("patient_gender") or "",
            "delivery_address": p.get("delivery_address") or "",
            "chronic_conditions": p.get("chronic_conditions") or ""
        })
    return JSONResponse(status_code=200, content={"status": "success", "count": len(formatted), "patients": formatted})


@app.get("/api/v1/patients/{patient_id}/history", tags=["Patients"])
async def get_patient_history_api(patient_id: str):
    """
    Fetches stored patient medical profile, chronic conditions, and previous prescriptions
    from MongoDB using Patient ID for automatic clinical recall on the UI.
    """
    if not patient_id or not patient_id.strip():
        raise HTTPException(status_code=400, detail="Patient ID is required.")
    
    clean_id = patient_id.strip()
    history = db_manager.get_patient_history(clean_id)
    if not history or not history.get("found"):
        return JSONResponse(
            status_code=200,
            content={
                "status": "not_found",
                "patient_id": clean_id,
                "message": f"No previous prescription history found for Patient ID: {clean_id}. Patient will be registered on first consultation submission."
            }
        )
    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "patient_id": clean_id,
            "data": history
        }
    )


# ==============================================================================
# 2. VOICE BOT INTEGRATION SEAM (POST /api/v1/prescriptions)
# ==============================================================================
@app.post("/api/v1/prescriptions")
async def receive_voicebot_prescription(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None)
):
    """
    Integration Seam Endpoint:
    Receives incoming prescription push from Sanjeevani or external EMR / systems with API Key auth.
    Validates identity DOB, E.164 phone, and structured medicine fields.
    """
    # Auth Check
    provided_key = x_api_key
    if not provided_key and authorization:
        if authorization.lower().startswith("bearer "):
            provided_key = authorization[7:].strip()
        else:
            provided_key = authorization.strip()

    expected_key = settings.VOICE_BOT_API_KEY
    if not expected_key or provided_key != expected_key:
        print(f" [!] [Auth Failure] Unauthorized access attempt to POST /api/v1/prescriptions (Key: '{provided_key}')")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key. Please provide valid 'x-api-key' or 'Authorization: Bearer <key>'."
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    prescription_id = body.get("prescription_id") or f"RX-SNJ-RECV-{int(time.time())}"
    patient = body.get("patient", {})
    medicines = body.get("medicines", [])

    print(f"\n [SEAM INGESTION] Voice Bot Prescriptions Seam received payload for Rx: {prescription_id}")
    print(f" -> Patient: {patient.get('patient_name')} ({patient.get('mobile_number')}, DOB: {patient.get('identity_dob')})")
    print(f" -> Structured Medicines Count: {len(medicines)}")

    # Update or persist in MongoDB if connected
    if db_manager.is_connected() and patient:
        db_manager.upsert_patient(patient)

    return JSONResponse(
        status_code=200,
        content={
            "status": "received",
            "message": "Prescription successfully accepted for voice bot outbound workflows.",
            "prescription_id": prescription_id,
            "patient_mobile": patient.get("mobile_number"),
            "patient_identity_dob": patient.get("identity_dob"),
            "medicines_count": len(medicines),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    )


# ==============================================================================
# 3. MONGODB INSPECTION ENDPOINTS
# ==============================================================================
@app.get("/api/v1/prescriptions/{prescription_id}")
def get_stored_prescription(prescription_id: str):
    """Retrieves a specific prescription by ID from MongoDB."""
    if not db_manager.is_connected():
        raise HTTPException(status_code=503, detail="MongoDB is not connected.")
    rx = db_manager.get_prescription(prescription_id)
    if not rx:
        raise HTTPException(status_code=404, detail=f"Prescription '{prescription_id}' not found.")
    return JSONResponse(content={"status": "success", "prescription": rx})


# ==============================================================================
# 4. TELEMETRY & ORDER DISPATCH (Preserved for Dashboard Live Operations)
# ==============================================================================
@app.get("/api/v1/analytics/telemetry")
def get_analytics_telemetry():
    """Returns real-time telemetry metrics for the Executive Operations Analytics Dashboard."""
    avg_order_value = round(analytics_state["total_revenue_aed"] / max(1, analytics_state["total_pharmacy_orders"]), 1)
    return JSONResponse(
        content={
            "status": "success",
            "telemetry": {
                **analytics_state,
                "avg_order_value_aed": avg_order_value
            }
        }
    )


@app.post("/api/v1/orders/refill")
async def track_and_dispatch_order(request: Request):
    """
    Live Order Placement & AI Telemetry Tracker:
    - Ingests order details (items, patient, destination, fulfillment type).
    - Classifies order flag: MEDICINE_ONLY, RPM_ONLY, or COMBO_MEDS_AND_RPM.
    - Dynamically updates Executive Analytics KPIs, order counts, and audit logs.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    patient_name = payload.get("patient_name") or "Valued Patient"
    order_ref = payload.get("order_ref") or f"SNJ-RX-{int(time.time()) % 10000}"
    fulfillment_mode = payload.get("fulfillment_mode") or "home_delivery"
    destination = payload.get("destination") or "Registered Address"
    items = payload.get("items") or []

    has_meds = False
    has_rpm = False
    order_total_aed = 0.0

    for it in items:
        price = float(it.get("price_aed", 25.0) or 25.0)
        qty = int(it.get("quantity", 1) or 1)
        order_total_aed += (price * qty)

        if it.get("is_device") or "device" in str(it.get("category", "")).lower() or "monitor" in str(it.get("name", "")).lower() or "thermometer" in str(it.get("name", "")).lower():
            has_rpm = True
        else:
            has_meds = True

    if has_meds and has_rpm:
        order_flag = "COMBO_MEDS_AND_RPM"
        flag_label = "Combo (Rx Meds + RPM Device)"
    elif has_rpm:
        order_flag = "RPM_ONLY"
        flag_label = "RPM Hardware Only"
    else:
        order_flag = "MEDICINE_ONLY"
        flag_label = "Prescription Refill Only"

    if fulfillment_mode == "home_delivery" and order_total_aed < 50:
        order_total_aed += 15.0

    analytics_state["total_pharmacy_orders"] += 1
    if order_flag == "MEDICINE_ONLY":
        analytics_state["order_breakdown"]["medicine_only"] += 1
    elif order_flag == "RPM_ONLY":
        analytics_state["order_breakdown"]["rpm_only"] += 1
    else:
        analytics_state["order_breakdown"]["combo_meds_and_rpm"] += 1

    analytics_state["total_revenue_aed"] += order_total_aed

    new_event = {
        "id": f"EVT-{int(time.time()) % 100000}",
        "type": "order",
        "title": f"Order #{order_ref} Dispatched",
        "desc": f"Patient {patient_name} • {len(items)} items • {destination[:32]}... [Flag: {flag_label}]",
        "time": "Just now",
        "flag": order_flag
    }
    analytics_state["recent_events"].insert(0, new_event)
    if len(analytics_state["recent_events"]) > 20:
        analytics_state["recent_events"] = analytics_state["recent_events"][:20]

    avg_order_value = round(analytics_state["total_revenue_aed"] / max(1, analytics_state["total_pharmacy_orders"]), 1)

    print(f"\n [ORDER RECEIVED & TRACKED] Ref: {order_ref} | Flag: {order_flag} | Total: AED {order_total_aed:.2f} | Items: {len(items)}")

    return JSONResponse(
        content={
            "status": "success",
            "message": "Order successfully recorded and routed to Sanjeevani Pharmacy & Logistics.",
            "order_ref": order_ref,
            "order_flag": order_flag,
            "flag_label": flag_label,
            "order_total_aed": order_total_aed,
            "telemetry": {
                **analytics_state,
                "avg_order_value_aed": avg_order_value
            }
        }
    )


if __name__ == "__main__":
    import uvicorn
    print("\n Starting Sanjeevani Virtual Health Store AI Server...")
    print(f" Web Dashboard: http://localhost:{settings.PORT}")
    print(f" API Docs:      http://localhost:{settings.PORT}/docs\n")
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
