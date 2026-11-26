import os
import io
import json
import re
import time
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional
from PIL import Image
import llm
from dotenv import load_dotenv

# Fast model first; each attempt is cut off so one stuck call cannot hold the request past the tunnel's 100 s limit.
CAREPLAN_MODELS = [m.strip() for m in os.getenv("CAREPLAN_MODELS", "gemini-3.1-flash-lite,gemini-3.5-flash").split(",") if m.strip()]
CAREPLAN_MODEL_TIMEOUT = float(os.getenv("CAREPLAN_MODEL_TIMEOUT", "25"))
CAREPLAN_DEADLINE = float(os.getenv("CAREPLAN_DEADLINE", "85"))

def get_gemini_api_key() -> str:
    load_dotenv(override=True)
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()

def calculate_medicine_end_date(start_date_str: str, duration_days: int) -> str:
    """Calculates end_date = start_date + duration_days."""
    try:
        start_d = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except Exception:
        start_d = date.today()
    duration = max(1, int(duration_days if duration_days else 1))
    return (start_d + timedelta(days=duration)).strftime("%Y-%m-%d")

def optimize_image_for_vision(file_bytes: bytes, max_dimension: int = 1800) -> Image.Image:
    """
    Optimizes input image for Gemini Vision with zero loss of clinical readability:
    - If dimensions exceed max_dimension, downsamples proportionally.
    - Preserves high contrast and crisp resolution for handwriting and small dosage text.
    """
    img = Image.open(io.BytesIO(file_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    
    w, h = img.size
    if max(w, h) > max_dimension:
        if w > h:
            new_w = max_dimension
            new_h = int(h * (max_dimension / float(w)))
        else:
            new_h = max_dimension
            new_w = int(w * (max_dimension / float(h)))
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    return img

def standardize_and_enrich_medications(medications: List[Dict[str, Any]], today_iso: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Standardizes medication fields into strict structured types required by voice bot & DB:
    - drug_name (str)
    - strength (str)
    - form (str)
    - dose_text (str)
    - frequency_per_day (int)
    - timing (str)
    - duration_days (int)
    - start_date (YYYY-MM-DD)
    - end_date (YYYY-MM-DD)
    """
    if not today_iso:
        today_iso = date.today().strftime("%Y-%m-%d")

    standardized = []
    for med in medications:
        if not isinstance(med, dict):
            continue

        raw_name = med.get("drug_name") or med.get("name") or "Prescribed Medication"
        
        # Strength extraction / fallback
        strength = str(med.get("strength") or med.get("dosage_strength") or "").strip()
        if not strength or strength.lower() in ["standard", "none", "as prescribed"]:
            str_match = re.search(r'(\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|%))', raw_name, re.IGNORECASE)
            strength = str_match.group(1) if str_match else "Standard"

        # Form extraction / fallback
        form = str(med.get("form") or "").strip()
        if not form:
            if re.search(r'\b(cap|capsule)\b', raw_name, re.I):
                form = "Capsule"
            elif re.search(r'\b(syrup|suspension|liquid|solution)\b', raw_name, re.I):
                form = "Syrup"
            elif re.search(r'\b(inj|injection)\b', raw_name, re.I):
                form = "Injection"
            elif re.search(r'\b(inhaler|puff)\b', raw_name, re.I):
                form = "Inhaler"
            elif re.search(r'\b(drop|drops)\b', raw_name, re.I):
                form = "Drops"
            else:
                form = "Tablet"

        # Dose text
        dose_text = str(med.get("dose_text") or "").strip()
        if not dose_text:
            if form == "Syrup":
                dose_text = "5 ml"
            elif form in ["Tablet", "Capsule"]:
                dose_text = f"1 {form.lower()}"
            elif form == "Inhaler":
                dose_text = "2 puffs"
            elif form == "Drops":
                dose_text = "2 drops"
            else:
                dose_text = "1 dose"

        # Frequency per day
        freq_raw = med.get("frequency_per_day") or med.get("frequency")
        freq_int = 1
        if isinstance(freq_raw, int):
            freq_int = max(1, min(12, freq_raw))
        elif isinstance(freq_raw, str):
            freq_lower = freq_raw.lower()
            if "tds" in freq_lower or "tid" in freq_lower or "three" in freq_lower or "3" in freq_lower:
                freq_int = 3
            elif "bd" in freq_lower or "bid" in freq_lower or "twice" in freq_lower or "morning and evening" in freq_lower or "2" in freq_lower:
                freq_int = 2
            elif "qid" in freq_lower or "four" in freq_lower or "4" in freq_lower:
                freq_int = 4
            elif "od" in freq_lower or "once" in freq_lower or "daily" in freq_lower or "night" in freq_lower or "bedtime" in freq_lower:
                freq_int = 1

        # Timing description
        timing = str(med.get("timing") or "").strip()
        if not timing:
            if freq_int == 1:
                timing = "once daily after meal"
            elif freq_int == 2:
                timing = "morning and evening after meals"
            elif freq_int == 3:
                timing = "morning, noon, and night after meals"
            elif freq_int == 4:
                timing = "every 6 hours after food"
            else:
                timing = "as directed by physician"

        # Duration days
        dur_raw = med.get("duration_days") or med.get("duration")
        dur_int = 30
        duration_assumed = not dur_raw
        if isinstance(dur_raw, int):
            dur_int = max(1, min(365, dur_raw))
        elif isinstance(dur_raw, str):
            dur_match = re.search(r'(\d+)', dur_raw)
            if dur_match:
                dur_int = int(dur_match.group(1))
                if "week" in dur_raw.lower():
                    dur_int *= 7
                elif "month" in dur_raw.lower():
                    dur_int *= 30

        # Start & End date
        start_date = str(med.get("start_date") or today_iso).strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', start_date):
            start_date = today_iso

        end_date = med.get("end_date")
        if not end_date or not re.match(r'^\d{4}-\d{2}-\d{2}$', str(end_date)):
            end_date = calculate_medicine_end_date(start_date, dur_int)

        # Instructions
        instructions_plain = str(med.get("instructions_plain") or (
            f"Take {dose_text} ({timing})." if duration_assumed else f"Take {dose_text} ({timing}) for {dur_int} days.")).strip()
        instructions_arabic = str(med.get("instructions_arabic") or "تناول الدواء حسب تعليمات الطبيب.").strip()

        standardized.append({
            "drug_name": str(raw_name).strip(),
            "strength": strength,
            "form": form,
            "dose_text": dose_text,
            "frequency_per_day": freq_int,
            "timing": timing,
            "duration_days": dur_int,
            "start_date": start_date,
            "end_date": str(end_date),
            "instructions_plain": instructions_plain,
            "instructions_arabic": instructions_arabic,
            "duration_assumed": duration_assumed,
            "needs_confirmation": bool(med.get("needs_confirmation")),
            "confirm_note": str(med.get("confirm_note") or "").strip()
        })

    return standardized


def prepare_multimodal_part(file_bytes: bytes, mime_type: Optional[str] = None):
    """Prepares an image or PDF buffer for Gemini multimodal API."""
    if not file_bytes:
        return None
    if file_bytes.startswith(b'%PDF-') or (mime_type and 'pdf' in mime_type.lower()):
        return llm.part_from_bytes(file_bytes, "application/pdf")
    try:
        return optimize_image_for_vision(file_bytes)
    except Exception:
        if mime_type:
            return llm.part_from_bytes(file_bytes, mime_type)
        return None



def analyze_prescription_and_plan_journey(
    prescription_text: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    image_mime_type: Optional[str] = None,
    prescription_images: Optional[List[bytes]] = None,
    prescription_image_mimes: Optional[List[str]] = None,
    history_text: Optional[str] = None,
    history_image_bytes: Optional[bytes] = None,
    history_image_mime_type: Optional[str] = None,
    history_images: Optional[List[bytes]] = None,
    patient_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    ⚡ Single-Pass Multimodal AI Clinical Brain:
    Combines Vision OCR, clinical entity parsing, patient medical history synthesis (supporting multiple history files/scans and multiple prescription uploads),
    and Next-Best-Action care plan formulation into a single high-speed Gemini round-trip.
    """
    if patient_data is None:
        patient_data = {}

    print("\n" + "="*80)
    print(" [AI CLINICAL REASONER] Single-Pass Multimodal Journey Formulation (with History Context)...")
    print("="*80)

    # Collect all active prescription images
    all_rx_images: List[bytes] = []
    if prescription_images:
        all_rx_images.extend([img for img in prescription_images if img])
    if image_bytes and image_bytes not in all_rx_images:
        all_rx_images.append(image_bytes)

    # Collect all history images
    all_hist_images: List[bytes] = []
    if history_images:
        all_hist_images.extend([img for img in history_images if img])
    if history_image_bytes and history_image_bytes not in all_hist_images:
        all_hist_images.append(history_image_bytes)

    api_key = get_gemini_api_key()
    today_iso = date.today().strftime("%Y-%m-%d")

    if api_key:
        try:
            result = _call_single_pass_gemini(
                prescription_text=prescription_text,
                image_bytes=all_rx_images[0] if len(all_rx_images) == 1 else None,
                image_mime_type=image_mime_type,
                prescription_images=all_rx_images if len(all_rx_images) > 1 else None,
                history_text=history_text,
                history_images=all_hist_images,
                patient_data=patient_data,
                today_iso=today_iso
            )
            # Ensure medications are strictly standardized
            if "clinical_summary" in result and "medications" in result["clinical_summary"]:
                meds = result["clinical_summary"]["medications"]
                # Fallback recovery: if active meds is empty but patient provided a single prescription image and meds were put in past_medications
                if not meds and not all_hist_images and not history_text:
                    past_meds = result.get("patient_history", {}).get("past_medications", [])
                    if past_meds:
                        meds = [{"drug_name": pm.get("drug_name"), "dose_text": pm.get("dosage", "1 dose"), "timing": "as prescribed", "frequency_per_day": 1,
                                 "needs_confirmation": True, "confirm_note": "Read from the note's history section; please confirm with the doctor."} for pm in past_meds]
                result["clinical_summary"]["medications"] = standardize_and_enrich_medications(
                    meds,
                    today_iso
                )
            return result
        except Exception as e:
            print(f" [!] [AI Brain LLM Error] {e}. Falling back to Rule-Based AI Engine.")
            return _fallback_rule_based_brain(prescription_text or "", history_text or "", patient_data, today_iso)
    else:
        print(" [!] No GEMINI_API_KEY detected. Using Generic Rule-Based AI Engine.")
        return _fallback_rule_based_brain(prescription_text or "", history_text or "", patient_data, today_iso)


def repair_and_parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text, strict=False)
    except Exception:
        pass

    start = text.find('{')
    if start == -1:
        raise ValueError("No JSON object opening brace found")

    candidate = text[start:]
    stack = []
    in_string = False
    escape = False
    clean_chars = []

    for ch in candidate:
        if ch == '\\' and not escape:
            escape = True
            clean_chars.append(ch)
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            clean_chars.append(ch)
            escape = False
            continue
        escape = False

        if not in_string:
            if ch == '{':
                stack.append('}')
            elif ch == '[':
                stack.append(']')
            elif ch in ('}', ']'):
                if stack and stack[-1] == ch:
                    stack.pop()
        clean_chars.append(ch)

    fixed_str = "".join(clean_chars)
    if in_string:
        fixed_str += '"'

    fixed_str = fixed_str.rstrip().rstrip(',')
    while stack:
        closer = stack.pop()
        fixed_str = fixed_str.rstrip().rstrip(',')
        fixed_str += closer

    fixed_str = re.sub(r',\s*([\]\}])', r'\1', fixed_str)
    return json.loads(fixed_str, strict=False)


def _call_single_pass_gemini(
    prescription_text: Optional[str],
    image_bytes: Optional[bytes],
    image_mime_type: Optional[str],
    history_text: Optional[str],
    history_images: Optional[List[bytes]],
    patient_data: Dict[str, Any],
    today_iso: str,
    prescription_images: Optional[List[bytes]] = None
) -> Dict[str, Any]:
    start_time = time.time()
    
    patient_name = patient_data.get('patient_name') or 'Valued Patient'
    patient_id = patient_data.get('patient_id') or 'SNJ-PATIENT'
    delivery_address = patient_data.get('delivery_address') or 'Registered Address on File'
    
    text_hint = f"\nADDITIONAL CLINICAL NOTES / TEXT HINT (CURRENT PRESCRIPTION):\n\"\"\"{prescription_text.strip()}\"\"\"\n" if prescription_text and prescription_text.strip() else ""

    history_img_count = len(history_images) if history_images else 0
    total_docs_count = (1 if image_bytes else 0) + history_img_count

    history_hint = ""
    combined_history_context = []
    if patient_data.get('chronic_conditions'):
        combined_history_context.append(f"Recorded Chronic Conditions: {patient_data.get('chronic_conditions')}")
    if history_text and history_text.strip():
        combined_history_context.append(f"Patient Reported History / Past Meds / Allergies:\n\"\"\"{history_text.strip()}\"\"\"")
    if history_images:
        for idx in range(len(history_images)):
            combined_history_context.append(f"[Attached Document/Scan {idx+1} of {history_img_count}: Distinct Past Prescription / Historical Medical Record Document]")

    if combined_history_context:
        history_hint = "\n" + "="*40 + "\nPATIENT PREVIOUS MEDICAL HISTORY CONTEXT (" + str(history_img_count) + " SEPARATE HISTORICAL DOCUMENTS ATTACHED):\n" + "\n".join(combined_history_context) + "\n" + "="*40 + "\n"

    prompt = f"""
You are the AI Clinical Brain of Sanjeevani Holdings Virtual Health Store.
Your task is to analyze the provided clinical prescription / doctor note along with the PATIENT'S PREVIOUS MEDICAL HISTORY (past conditions, previous medications, allergies, and {history_img_count} distinct past prescription/report records) in a SINGLE PASS:

================================================================================
CRITICAL CLINICAL EXTRACTION DIRECTIVE (STRICT ACTIVE PRESCRIPTION vs HISTORY SEPARATION):
1. CURRENT ACTIVE PRESCRIPTION (Designated as [CURRENT ACTIVE PRESCRIPTION / ACTIVE PRESCRIPTION DOCUMENT]):
   - The Active Prescription is the ABSOLUTE PRIMARY DRIVER of this consultation.
   - Any prescription document or image submitted in the Active Prescription slot MUST have all its prescribed medications, doctor name, and primary diagnosis extracted into `clinical_summary.medications` and `clinical_summary.primary_condition`, REGARDLESS of the printed date on the physical slip (e.g. even if dated 2019 or earlier, the user is submitting this prescription to formulate their active care plan today!).
   - You MUST extract clinical_summary.primary_condition, clinical_summary.doctor_name, clinical_summary.medications, extracted_transcription, patient_explanation (greeting, friendly_overview, arabic_summary), and ALL next_best_actions (care_program_enrollment, lab_and_diagnostics, doctor_revisit_schedule, device_rental_and_rpm, diet_and_lifestyle) EXCLUSIVELY and ENTIRELY from THIS CURRENT ACTIVE PRESCRIPTION.
   - NEVER leave `clinical_summary.medications` empty if there are readable medications written on the Active Prescription document!
   - ALWAYS populate `diet_and_lifestyle` (with at least 3 actionable dietary guidelines and 2 lifestyle guidelines) and `doctor_revisit_schedule` (revisit_recommended: true only if the note or the condition calls for a follow-up) matching the patient's condition.
   - Extract the EXACT medications, strengths, dosages, and instructions written on the CURRENT prescription.
   - NEVER let historical blood test reports or past records override the primary diagnosis, doctor explanation, care program, or recommended lab tests!

2. PATIENT PREVIOUS MEDICAL HISTORY (Designated as [HISTORICAL MEDICAL RECORDS]):
   - Historical documents, past scans, and prior EMR notes are for BACKGROUND CONTEXT ONLY.
   - Use them ONLY to populate patient_history.chronic_conditions, patient_history.allergies, patient_history.past_medications, and patient_history.chronological_encounters.
   - DO NOT copy or hallucinate past medications from history into the current active medications list unless they are explicitly re-written on the Current Active Prescription!

3. MANDATORY ENCOUNTER EXTRACTION RULE FOR chronological_encounters:
   - `chronological_encounters` MUST contain EXCLUSIVELY PREVIOUS/HISTORICAL MEDICAL ENCOUNTERS from the attached historical documents or past EMR notes ({history_img_count} past records).
   - DO NOT include the Current Active Prescription in `chronological_encounters`. The Current Prescription belongs exclusively in the active care plan and Medicines section.
   - If 0 historical documents/records are attached, `chronological_encounters` MUST be [].
   - If {history_img_count} historical documents are attached, extract exactly 1 distinct past encounter object for EACH historical scan (total {history_img_count} past encounters).
   - DO NOT merge multiple history documents into one entry! Inspect each historical image individually, extract its specific date (or estimated year/month if faint), doctor name, and facility.
   - Sort all past encounters chronologically by date (most recent past encounter first).
   - For EACH past encounter, provide an array of STRICTLY MAXIMUM 3 concise bullet points summarizing:
     (Point 1) Primary diagnosis or clinical reason for encounter,
     (Point 2) Medications or clinical interventions prescribed with dosage,
     (Point 3) Outcome, vitals, or follow-up instruction.
   - NEVER exceed 3 bullet points per encounter.
4. Formulate structured digital medication records with exact dosage and duration for the current prescription.
5. Generate localized patient explanations (English and Arabic) reflecting the CURRENT PRESCRIPTION diagnosis.
6. Identify Next-Best-Actions (Pharmacy 1-click refill payload, Diagnostics/Labs, Care Program Enrollment, Smart RPM device, Doctor Revisit) tailored to the CURRENT PRESCRIPTION diagnosis.
7. NUTRITION & LIFESTYLE SYNTHESIS (POINT-WISE & LAB BIOMARKER AWARE - ISOLATED SCOPE):
   - You MUST formulate dietary and lifestyle guidance strictly as structured, point-wise bullet items.
   - STRICT BLOOD WORK / LAB REPORT SCOPE:
     * Blood work / lab reports are used SOLELY AND EXCLUSIVELY as input for this Nutrition & Lifestyle section (and their corresponding historical encounter entry).
     * Blood work must NEVER replace or override the active prescription diagnosis, care pathway, or doctor briefing!
      * ONLY if blood work or lab report documents/scans/text data are provided (in active uploads or patient history):
        a) Carefully inspect the blood work / lab reports for specific biomarkers (e.g. Hemoglobin, Ferritin, TIBC, HbA1c, Fasting Blood Sugar, Vitamin D, Vitamin B12, Total Cholesterol / LDL, etc.).
        b) CRITICAL BIOMARKER CONSOLIDATION DIRECTIVE:
           - ALWAYS MERGE related blood parameters that belong to the same biological panel into ONE SINGLE consolidated card to avoid repetitive cards and clutter!
           - Example (Iron / Anemia Panel): When Hemoglobin, Ferritin, and TIBC are present, DO NOT make 3 separate cards. MERGE them into ONE single card: `biomarker: "Iron & Blood Health Profile (Hemoglobin, Ferritin, TIBC)"`, with a unified clinical trend (`clinical_trend: "Hemoglobin (9.8 g/dL), Ferritin (8.2 ng/mL), and elevated TIBC (445 mcg/dL) confirm iron deficiency anemia."`) and ONE concise point-wise dietary action.
           - Example (Lipid Panel): Merge Total Cholesterol, LDL, Triglycerides into `Lipid & Cardiovascular Panel`.
           - Example (Glycemic Panel): Merge Fasting Glucose and HbA1c into `Glycemic & Metabolic Panel`.
        c) Set `has_lab_biomarkers: true` and populate `biomarker_nutrition_insights` with at most 1-2 consolidated panel entries.
     * If NO blood work or lab reports are present (i.e. strictly prescription notes / clinical consultation orders without lab reports):
       - DO NOT fabricate, invent, or hallucinate fake blood test numbers or biomarker comparisons (NEVER make up fake hemoglobin or past blood numbers when only a prescription is provided!).
       - Provide condition-tailored point-wise dietary guidelines (e.g. for diabetes: low GI foods, high fiber; for hypertension: low sodium, DASH diet; for respiratory: anti-inflammatory antioxidants).
       - Set `has_lab_biomarkers: false` and `biomarker_nutrition_insights: []`.
================================================================================

STRICT RULES (this is a patient-safety document, never guess silently):
- Only write what is on the Current Active Prescription. If the diagnosis, ICD-10 code, doctor name or a duration is NOT written there, leave it as an empty string (or null for duration_days). Never invent them.
- If a medicine name, strength or dose is hard to read or you are not sure, still give your best reading but set "needs_confirmation": true and say in "confirm_note" what to check with the doctor (e.g. "Could be Crocin or Amoxil, confirm with the doctor").
- Keep every text field short and plain.

TODAY'S DATE: {today_iso}

PATIENT METADATA:
- Name: {patient_name}
- Patient ID: {patient_id}
- Delivery Address: {delivery_address}
- Age: {patient_data.get('patient_age', 'Not specified')} | Gender: {patient_data.get('patient_gender', 'Not specified')}
- Vitals: BP: {patient_data.get('blood_pressure', 'N/A')}, Weight: {patient_data.get('weight_kg', 'N/A')} kg
- Insurance Tier: {patient_data.get('insurance_tier', 'Standard')}
{text_hint}
{history_hint}

OUTPUT SCHEMA REQUIREMENT:
CRITICAL: Return ONLY a valid, well-formed JSON object without markdown fences, unescaped raw control characters, or unescaped quotes inside strings. Conforming to the following structure:
{{
  "patient_history": {{
    "raw_history_input": "Brief summary of patient's historical records provided",
    "chronic_conditions": [
      "Diagnosed chronic conditions (e.g. Type 2 Diabetes, Hypertension, Asthma, or 'None reported')"
    ],
    "allergies": [
      "Known drug or food allergies (or 'No known drug allergies')"
    ],
    "past_medications": [
      {{
        "drug_name": "Past medication name",
        "dosage": "Dosage/frequency or 'Recorded prior medication'"
      }}
    ],
    "chronological_encounters": [
      {{
        "date": "YYYY-MM-DD or Month Year (date picked from that prescription/report)",
        "facility_or_doctor": "Doctor or Hospital name (e.g. Dr. ... / Sanjeevani Medical City)",
        "encounter_type": "Prescription / Lab Report / Clinical Consultation",
        "key_points": [
          "Key diagnosis or primary clinical focus",
          "Medications or clinical interventions prescribed",
          "Follow-up or vital/recovery instruction (max 3 points total)"
        ]
      }}
    ],
    "clinical_profile_overview": "Concise 1-2 sentence medical summary of the patient's historical baseline and how it informs their current care."
  }},
  "extracted_transcription": "Full readable transcription of the doctor prescription note",
  "clinical_summary": {{
    "primary_condition": "Diagnosis exactly as written on the note, or empty string if not written",
    "icd_10_code": "ICD-10 code only if written on the note, else empty string",
    "doctor_name": "Doctor name and department only if written on the note, else empty string",
    "urgency_level": "Standard / Routine / Diagnostic Follow-up Required / Priority",
    "medications": [
      {{
        "drug_name": "Exact medication name",
        "strength": "Dosage strength (e.g. 500mg, 10mg, 5ml)",
        "form": "Tablet / Capsule / Syrup / Injection / Inhaler / Drops",
        "dose_text": "e.g. 1 tablet / 4 ml",
        "frequency_per_day": 2,
        "timing": "e.g. morning and evening after meals",
        "duration_days": "number of days if written on the note, else null",
        "needs_confirmation": false,
        "confirm_note": "empty unless needs_confirmation is true",
        "start_date": "{today_iso}",
        "instructions_plain": "Clear plain-language instructions for the patient",
        "instructions_arabic": "Arabic translation of dosage instructions"
      }}
    ]
  }},
  "patient_explanation": {{
    "greeting": "Dear {patient_name},",
    "friendly_overview": "A warm, clear paragraph explaining what the doctor evaluated and key next steps.",
    "arabic_summary": "فقرة ملخصة وواضحة باللغة العربية تشرح نتائج الاستشارة والخطوات العلاجية القادمة."
  }},
  "next_best_actions": {{
    "medication_delivery": {{
      "title": "Sanjeevani Pharmacy 1-Click Refill",
      "message": "Message informing patient medications are ready for same-day home delivery to {delivery_address}.",
      "post_endpoint": "https://api.sanjeevani.com/v1/pharmacy/orders/refill",
      "order_payload": {{
        "patient_id": "{patient_id}",
        "delivery_address": "{delivery_address}",
        "prescription_ref": "RX-SNJ-{patient_id}",
        "items": [
          {{
            "medicine_name": "Medication name and strength",
            "quantity": "Total quantity (e.g. 60 tablets / 1 box)",
            "dosage": "Dosage instructions summary"
          }}
        ],
        "action": "SAME_DAY_HOME_DELIVERY"
      }},
      "cta_button_text": "Confirm Pharmacy Home Delivery"
    }},
    "lab_and_diagnostics": {{
      "is_required": true,
      "test_name": "Diagnostic lab test or scan recommended by doctor or clinical guidelines",
      "timing": "Recommended timing (e.g. within 7 days / in 3 months)",
      "reason": "Why this test is required in simple patient language",
      "home_collection_available": true,
      "cta_button_text": "Book Lab / Scan Appointment"
    }},
    "care_program_enrollment": {{
      "is_recommended": true,
      "program_name": "Specific Sanjeevani Care Pathway relevant to condition",
      "services_included": [
        "Clinical Dietician consultation for personalized meal planning",
        "Quarterly Doctor Review & Progress Monitoring",
        "Dedicated Care Navigator support via Sanjeevani App"
      ],
      "cta_button_text": "Enroll in Care Program"
    }},
    "device_rental_and_rpm": {{
      "is_recommended": true,
      "device_name": "Relevant connected health device (e.g. Smart Connected BP Monitor / Connected Glucometer Kit)",
      "purpose": "Why tracking helps manage their condition",
      "benefit": "Automated data sync with Sanjeevani EMR and doctor dashboard",
      "can_purchase": true,
      "purchase_price_aed": 120,
      "rental_price_aed": 35,
      "cta_button_text": "Order Smart Health Device"
    }},
    "doctor_revisit_schedule": {{
      "revisit_recommended": true,
      "timing": "Recommended follow-up timeframe (e.g. Follow up in 4 weeks)",
      "purpose": "Purpose of follow-up consultation",
      "cta_button_text": "Schedule Follow-up Consultation"
    }},
    "diet_and_lifestyle": {{
      "has_lab_biomarkers": false,
      "biomarker_nutrition_insights": [
        {{
          "biomarker": "Biomarker Name (e.g. Hemoglobin / HbA1c / Vitamin D / Fasting Glucose)",
          "previous_value": "Previous value (e.g. 10.2 g/dL)",
          "current_value": "Current value (e.g. 11.5 g/dL)",
          "clinical_trend": "Trend summary (e.g. Hemoglobin improved from 10.2 g/dL to 11.5 g/dL)",
          "actionable_dietary_guidance": "Specific dietary remedy (e.g. To improve hemoglobin, consume iron-rich foods such as spinach, beetroot, lentils, pomegranate, and combine with Vitamin C like citrus fruits)."
        }}
      ],
      "dietary_guidelines": [
        "Concise point-wise dietary recommendation 1",
        "Concise point-wise dietary recommendation 2",
        "Concise point-wise dietary recommendation 3"
      ],
      "lifestyle_guidelines": [
        "Concise point-wise lifestyle & physical activity recommendation 1",
        "Concise point-wise lifestyle & physical activity recommendation 2"
      ]
    }}
  }}
}}
"""

    inputs = [prompt]
    if prescription_images and len(prescription_images) > 0:
        for idx, rx_bytes in enumerate(prescription_images):
            print(f" -> Preparing active prescription document {idx+1}/{len(prescription_images)} for multimodal inference...")
            part_img = prepare_multimodal_part(rx_bytes)
            if part_img is not None:
                inputs.append(f"=== [ACTIVE PRESCRIPTION DOCUMENT #{idx+1} of {len(prescription_images)}] ===\n(CRITICAL: Extract ALL current medications, primary diagnosis, and doctor instructions strictly from this active prescription document below):")
                inputs.append(part_img)
    elif image_bytes:
        print(" -> Preparing current prescription/clinical document for multimodal inference...")
        part_img = prepare_multimodal_part(image_bytes, image_mime_type)
        if part_img is not None:
            inputs.append("=== [DOCUMENT 1: CURRENT ACTIVE PRESCRIPTION / LAB REPORT / DOCTOR ORDER] ===\n(CRITICAL: Extract ALL current medications, primary diagnosis, and doctor instructions strictly from this document below):")
            inputs.append(part_img)
    elif prescription_text and prescription_text.strip():
        inputs.append(f"=== [CURRENT ACTIVE PRESCRIPTION CONSULTATION TEXT] ===\n(CRITICAL: Extract active medications and diagnosis strictly from this text):\n{prescription_text.strip()}")

    if history_images:
        for idx, h_bytes in enumerate(history_images):
            print(f" -> Preparing patient history document {idx+1}/{len(history_images)} for multimodal inference...")
            part_hist = prepare_multimodal_part(h_bytes)
            if part_hist is not None:
                inputs.append(f"=== [HISTORICAL MEDICAL RECORD #{idx+1} (BACKGROUND CONTEXT & PAST ENCOUNTER TIMELINE ONLY)] ===\n(NOTE: Inspect this document for past diagnoses, prior medications, and previous blood work / lab values for biomarker comparison):")
                inputs.append(part_hist)

    print(f" -> Executing Single-Pass Multimodal call to Gemini ({', '.join(CAREPLAN_MODELS)})...")

    def _log(name, secs, err):
        if err is None:
            print(f" -> [{name}] answered in {secs:.1f}s")
        else:
            print(f" [!] [{name}] failed after {secs:.1f}s: {type(err).__name__}; trying again or the next model")

    try:
        raw_text = llm.generate(inputs, CAREPLAN_MODELS, temperature=0.1, max_tokens=8192, json_mode=True,
                                per_model_timeout=CAREPLAN_MODEL_TIMEOUT, deadline=CAREPLAN_DEADLINE - (time.time() - start_time),
                                on_attempt=_log, purpose="careplan")
    except llm.LLMUnavailable as e:
        raise RuntimeError(str(e))          # the caller falls back to the rule-based engine
    duration = time.time() - start_time
    print(f" -> [Gemini Single-Pass Complete] in {duration:.2f}s | Response: {len(raw_text)} chars")

    # Strip code block wrappers if any
    clean_text = raw_text
    if clean_text.startswith("```json"):
        clean_text = clean_text[7:]
    elif clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()

    try:
        parsed = repair_and_parse_json(clean_text)
        print(" -> [JSON Parse Success] Structured Care Plan generated successfully.")
        return parsed
    except Exception as e:
        print(f" [!] JSON parsing error on model output ({e}). Falling back to rule-based engine.")
        return _fallback_rule_based_brain(prescription_text or "", history_text or "", patient_data, today_iso)


def _fallback_rule_based_brain(prescription_text: str, history_text: str = "", patient_data: Optional[Dict[str, Any]] = None, today_iso: Optional[str] = None) -> Dict[str, Any]:
    """
    Robust rule-based parser that handles clinical inputs dynamically if LLM API is unavailable.
    """
    if patient_data is None:
        patient_data = {}
    print(" -> Executing Dynamic Rule-Based Clinical Parser (with History Synthesis)...")
    rx_lower = (prescription_text + " " + history_text).lower()
    if not today_iso:
        today_iso = date.today().strftime("%Y-%m-%d")
    
    patient_name = patient_data.get('patient_name') or 'Valued Patient'
    patient_id = patient_data.get('patient_id') or 'SNJ-98214'
    delivery_address = patient_data.get('delivery_address') or 'Registered Address on File'

    # Synthesize history profile
    chronic_conds = []
    if "diabet" in rx_lower: chronic_conds.append("Type 2 Diabetes")
    if "hypertens" in rx_lower or "bp" in rx_lower: chronic_conds.append("Hypertension")
    if "asthma" in rx_lower: chronic_conds.append("Asthma")
    if not chronic_conds:
        raw_cond = patient_data.get("chronic_conditions")
        if raw_cond:
            chronic_conds.append(raw_cond)
        else:
            chronic_conds = ["None reported"]

    allergies = ["Penicillin"] if "penicillin" in rx_lower else ["No known drug allergies"]

    patient_history = {
        "raw_history_input": history_text.strip() or patient_data.get("chronic_conditions", "None on file"),
        "chronic_conditions": chronic_conds,
        "allergies": allergies,
        "past_medications": [{"drug_name": "Metformin 500mg", "dosage": "Prior record"}] if "diabet" in rx_lower else [],
        "clinical_profile_overview": f"Patient profile baseline: {', '.join(chronic_conds)}. Care and nutrition synthesized to prevent contraindications."
    }

    # Extract doctor name
    doctor_match = re.search(r'(?:dr\.|doctor)\s+([a-zA-Z\s\.]+)', prescription_text, re.IGNORECASE)
    doctor_name = doctor_match.group(0).strip() if doctor_match else "Attending Specialist, Sanjeevani Medical City"

    # Identify primary condition
    if "diabet" in rx_lower or "metformin" in rx_lower or "gluco" in rx_lower or "hba1c" in rx_lower or "sugar" in rx_lower:
        condition = "Type 2 Diabetes Mellitus"
        icd_10 = "E11.9"
        lab_test = "Comprehensive Metabolic Panel (HbA1c & Fasting Glucose)"
        lab_reason = "Essential quarterly biomarker tracking to assess glycemic stability."
        program = "Sanjeevani Diabetes & Metabolic Wellness Pathway"
        device = "Smart Connected Bluetooth Glucometer Kit"
        device_purpose = "Continuous blood sugar telemetry synced directly with your doctor's EMR."
        device_price = 145
        rental_price = 40
        revisit_time = "4 weeks"
        revisit_purpose = "Review glycemic logs and optimize medication dosage."
        diet_guide = "Low glycemic index, rich in dietary fiber, lean protein, and portion-controlled complex carbs."
        life_guide = "30 minutes of moderate aerobic activity daily and optimal hydration."
    elif "hypertens" in rx_lower or "bp" in rx_lower or "amlodipine" in rx_lower or "losartan" in rx_lower or "pressure" in rx_lower:
        condition = "Essential Hypertension"
        icd_10 = "I10"
        lab_test = "Renal Function Panel & Serum Electrolytes"
        lab_reason = "Monitors kidney filtration and electrolyte balance under anti-hypertensive therapy."
        program = "Sanjeevani CardioCare & Vascular Health Program"
        device = "Smart Connected Cellular Blood Pressure Monitor"
        device_purpose = "Daily blood pressure readings automatically logged to your Sanjeevani clinical chart."
        device_price = 180
        rental_price = 45
        revisit_time = "6 weeks"
        revisit_purpose = "Assess blood pressure stability and cardiovascular response."
        diet_guide = "DASH dietary regimen, low sodium (<2,000mg/day), rich in potassium and leafy greens."
        life_guide = "Stress reduction techniques, regular brisk walking, and limiting caffeine intake."
    elif "lipid" in rx_lower or "cholesterol" in rx_lower or "atorvastatin" in rx_lower or "statin" in rx_lower:
        condition = "Hyperlipidemia / Dyslipidemia"
        icd_10 = "E78.5"
        lab_test = "Fasting Lipid Profile (Total Cholesterol, HDL, LDL, Triglycerides)"
        lab_reason = "Evaluates lipid reduction efficacy and cardiovascular risk reduction."
        program = "Sanjeevani Lipid & Cardiovascular Prevention Pathway"
        device = "Smart Digital Body Composition Scale"
        device_purpose = "Tracks weight trajectory and metabolic body composition changes."
        device_price = 120
        rental_price = 30
        revisit_time = "8 weeks"
        revisit_purpose = "Evaluate follow-up lipid profile and liver function enzymes."
        diet_guide = "Mediterranean heart-healthy diet, rich in Omega-3 fatty acids and soluble fiber."
        life_guide = "At least 150 minutes of moderate cardiovascular exercise per week."
    elif "respirat" in rx_lower or "asthma" in rx_lower or "inhaler" in rx_lower or "cough" in rx_lower:
        condition = "Chronic Bronchial Asthma / Respiratory Infection"
        icd_10 = "J45.909"
        lab_test = "Spirometry / Pulmonary Function & Total IgE"
        lab_reason = "Measures airway airflow obstruction and allergic sensitization levels."
        program = "Sanjeevani Respiratory Care & Asthma Management"
        device = "Smart Digital Peak Flow Meter & Pulse Oximeter"
        device_purpose = "Monitors lung capacity and blood oxygen saturation (SpO2) at home."
        device_price = 135
        rental_price = 35
        revisit_time = "2 weeks"
        revisit_purpose = "Review symptom triggers, inhaler technique, and lung capacity."
        diet_guide = "Anti-inflammatory nutrient-dense diet with abundant Vitamin C and antioxidants."
        life_guide = "Avoid known allergen triggers, monitor humidity levels, and keep indoor air purified."
    else:
        condition = "General Clinical Consultation & Maintenance"
        icd_10 = "Z00.00"
        lab_test = "Comprehensive Annual Wellness Blood Panel"
        lab_reason = "Routine biochemical baseline evaluation to support long-term wellness."
        program = "Sanjeevani Comprehensive Preventive Health Program"
        device = "Smart Connected Multi-Vital Health Monitor"
        device_purpose = "Tracks baseline vitals including temperature, pulse, and oxygen saturation."
        device_price = 150
        rental_price = 35
        revisit_time = "3 months"
        revisit_purpose = "Routine health review and lifestyle progress check."
        diet_guide = "Balanced whole-food nutrition emphasizing fresh vegetables, lean proteins, and hydration."
        life_guide = "Regular physical activity (10,000 steps daily) and 7-8 hours of restful sleep."

    # Parse medication lines
    medications = []
    lines = prescription_text.splitlines()
    for line in lines:
        cleaned_line = line.strip()
        if not cleaned_line or len(cleaned_line) < 3:
            continue
        if any(hdr in cleaned_line.lower() for hdr in ["dr.", "hospital", "patient", "date:", "phone:", "diagnosis:", "rx", "investigation"]):
            continue

        med_match = re.search(r'([A-Za-z0-9\-\s]+?)(?:\s+(\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|%)))?(?:\s+(tab|cap|syrup|inj|drops))?(?:\s+[-–:]\s*|\s+)?(.*)', cleaned_line, re.I)
        if med_match:
            d_name = med_match.group(1).strip()
            if len(d_name) > 2 and not d_name.lower().startswith(("take", "advised", "note", "investigation")):
                medications.append({
                    "drug_name": d_name.title(),
                    "strength": med_match.group(2) or "Standard",
                    "form": (med_match.group(3) or "Tablet").title(),
                    "dose_text": "1 dose",
                    "frequency_per_day": 2,
                    "timing": "morning and evening after food",
                    "duration_days": 30,
                    "start_date": today_iso,
                    "instructions_plain": f"Take as directed by doctor ({cleaned_line}).",
                    "instructions_arabic": "تناول الدواء حسب تعليمات الطبيب."
                })

    if not medications:
        medications = [{
            "drug_name": "Prescribed Clinical Medication",
            "strength": "Standard",
            "form": "Tablet",
            "dose_text": "1 tablet",
            "frequency_per_day": 1,
            "timing": "once daily after meal",
            "duration_days": 30,
            "start_date": today_iso,
            "instructions_plain": "Take one tablet daily after food as prescribed by your physician.",
            "instructions_arabic": "تناول حبة واحدة يومياً بعد الطعام حسب إرشادات الطبيب المعالج."
        }]

    standardized_meds = standardize_and_enrich_medications(medications, today_iso)

    return {
        "patient_history": patient_history,
        "extracted_transcription": prescription_text.strip() or "Standard clinical prescription ingested.",
        "clinical_summary": {
            "primary_condition": condition,
            "icd_10_code": icd_10,
            "doctor_name": doctor_name,
            "urgency_level": "Routine Post-Consultation",
            "medications": standardized_meds
        },
        "patient_explanation": {
            "greeting": f"Dear {patient_name},",
            "friendly_overview": f"Your consultation plan has been reviewed. Your doctor has established a care pathway for {condition}. All prescribed medications have been structured for your convenient adherence.",
            "arabic_summary": f"مرحباً {patient_name}، تم إعداد خطة الرعاية الطبية الخاصة بك ومتابعة حالة ({condition}) وتسهيل وصول الأدوية والخدمات التشخيصية إليك مباشرة."
        },
        "next_best_actions": {
            "medication_delivery": {
                "title": "Sanjeevani Pharmacy 1-Click Refill",
                "message": f"Your prescribed medications are verified and prepared for same-day home delivery to {delivery_address}.",
                "post_endpoint": "https://api.sanjeevani.com/v1/pharmacy/orders/refill",
                "order_payload": {
                    "patient_id": patient_id,
                    "delivery_address": delivery_address,
                    "prescription_ref": f"RX-SNJ-{patient_id}",
                    "items": [{"medicine_name": m["drug_name"], "quantity": f"{m['duration_days']} days supply", "dosage": m["timing"]} for m in standardized_meds],
                    "action": "SAME_DAY_HOME_DELIVERY"
                },
                "cta_button_text": "Confirm Pharmacy Home Delivery"
            },
            "lab_and_diagnostics": {
                "is_required": True,
                "test_name": lab_test,
                "timing": "Within 2-4 weeks",
                "reason": lab_reason,
                "home_collection_available": True,
                "cta_button_text": "Book Lab / Scan Appointment"
            },
            "care_program_enrollment": {
                "is_recommended": True,
                "program_name": program,
                "services_included": [
                    "Clinical Dietician consultation for personalized meal planning",
                    "Quarterly Doctor Review & Progress Monitoring",
                    "Dedicated Care Navigator support via Sanjeevani App"
                ],
                "cta_button_text": "Enroll in Care Program"
            },
            "device_rental_and_rpm": {
                "is_recommended": True,
                "device_name": device,
                "purpose": device_purpose,
                "benefit": "Automated data sync with Sanjeevani EMR and doctor dashboard",
                "can_purchase": True,
                "purchase_price_aed": device_price,
                "rental_price_aed": rental_price,
                "cta_button_text": "Order Smart Health Device"
            },
            "doctor_revisit_schedule": {
                "revisit_recommended": True,
                "timing": revisit_time,
                "purpose": revisit_purpose,
                "cta_button_text": "Schedule Follow-up Consultation"
            },
            "diet_and_lifestyle": {
                "dietary_guidelines": diet_guide,
                "lifestyle_guidelines": life_guide
            }
        }
    }
