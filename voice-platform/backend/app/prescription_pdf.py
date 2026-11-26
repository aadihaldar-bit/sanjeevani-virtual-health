"""Parse a *digital* PDF prescription into the structured ``PrescriptionPushRequest`` shape.

Safety first (this is medical data, see ``docs/sanjeevani_agent_prompt.md`` §4):
 - This reads the text layer of a PDF. It does **not** OCR scanned images or handwriting — a PDF
   with no extractable text is rejected, not guessed at.
 - The LLM extraction **never invents a dose**. When the dose, timing or duration is missing or
   unclear it is left empty and an ``open_question`` is recorded; the medicine is then flagged for a
   pharmacist rather than spoken to a patient.
 - Every extracted item comes back as a **draft**. Nothing extracted here is ever called on a
   patient until an administrator/pharmacist reviews and confirms it (see ``prescriptions_api`` →
   ``/prescriptions/{id}/confirm``), which is the only path that schedules follow-up calls.
 - The plain-language "what it is for" line is **not** generated here; it stays empty and is filled
   from the approved medicine knowledge base at review time.
"""
from __future__ import annotations

import io
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Keep the model from running away on a huge or adversarial document.
MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 20000


class PdfParseError(Exception):
    """The upload is not a readable digital PDF (empty, not a PDF, or image-only/scanned)."""


def extract_pdf_text(data: bytes) -> str:
    """Pull the text layer out of a digital PDF. Raises :class:`PdfParseError` for anything we cannot
    read as text (a scanned/image-only PDF has no text layer and must not be guessed at)."""
    if not data:
        raise PdfParseError("The file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise PdfParseError("The PDF is too large (over 8 MB).")
    if data[:5] != b"%PDF-":
        raise PdfParseError("That does not look like a PDF file.")
    try:
        from pypdf import PdfReader
    except Exception as exc:                                         # pragma: no cover - dependency guard
        raise PdfParseError("PDF support is not available on the server.") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                raise PdfParseError("The PDF is password-protected.")
        pages = [(page.extract_text() or "") for page in reader.pages]
    except PdfParseError:
        raise
    except Exception as exc:
        raise PdfParseError("The PDF could not be read.") from exc
    text = "\n".join(pages).strip()
    if len(text) < 20:
        raise PdfParseError(
            "No readable text was found. This looks like a scanned or photographed prescription; "
            "scanned prescriptions are not supported yet — please upload a digital PDF.")
    return text[:MAX_TEXT_CHARS]


_EXTRACTION_PROMPT = """You convert a hospital prescription into structured JSON. You are not a doctor and must NOT
interpret, correct, convert, round or complete anything. Copy only what is written on the prescription, word for word.

The prescription may be written in English, in Arabic, or in a mix of both (UAE prescriptions often are).
Read whichever language it is in. Copy each field in the SAME language and script it appears in on the
prescription — keep Arabic text in Arabic (do not translate or transliterate drug names, timing or notes).
Convert Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) to normal digits for the numeric fields (frequency_per_day,
duration_days) and for dates. If timing is written in Arabic (e.g. "صباحاً ومساءً"), copy it in Arabic.

Return ONLY a JSON object with this exact shape (no prose, no markdown):
{
  "patient": {"first_name": str|null, "last_name": str|null, "guardian_name": str|null, "identity_dob": "YYYY-MM-DD"|null},
  "prescription": {"external_id": str|null, "doctor_name": str|null, "facility": str|null, "issued_at": "YYYY-MM-DD"|null,
                   "course_days": int|null, "extra_instruction": str|null},
  "items": [
    {"drug_name": str|null, "strength": str|null, "form": str|null, "dose_text": str|null,
     "frequency_per_day": int|null, "timing": str|null, "duration_days": int|null,
     "warnings": str|null, "confidence": "high"|"medium"|"low", "open_question": str|null}
  ]
}

RULES:
- dose_text: copy exactly as written (e.g. "4 ml", "1 tablet"). If it is missing, unclear, smudged, shows two
  different amounts, or you are not certain, set dose_text to null, set confidence to "low", and write a short
  open_question describing what a pharmacist must confirm. NEVER guess or fill a dose.
- frequency_per_day / duration_days: only a plain integer you can read directly; otherwise null.
- Do NOT output any diagnosis, condition, ICD code, or advice even if the document contains one.
- Do NOT output a plain-language summary; that is added later from an approved source.
- If a field is not present, use null. Do not invent patient names, doctors, dates or medicines.

PRESCRIPTION TEXT:
---
%s
---
"""


# Arabic-Indic (٠-٩) and Eastern Arabic-Indic (۰-۹) digits -> ASCII, so a dose/duration written in
# an Arabic prescription still parses as a number.
_ARABIC_DIGITS = {ord(c): str(i % 10) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹")}


def _coerce_int(value) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = value.translate(_ARABIC_DIGITS).strip()
            if not value:
                return None
        return int(value)
    except (TypeError, ValueError):
        return None


IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf")
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def extract_prescription_from_file(data: bytes, mime: str) -> dict:
    """The same reading for a photograph or a scanned page of a prescription (or a PDF of one), which has no text layer to read.
    The same rules hold: nothing is guessed, an unclear dose is left empty and flagged for the pharmacist."""
    mime = (mime or "").lower()
    if not data:
        raise PdfParseError("The file is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise PdfParseError("The file is too large (over 10 MB).")
    if data[:5] == b"%PDF-":
        mime = "application/pdf"                    # by what the file is, not by what the browser called it
        try:
            # A digital PDF has its text in it: read that, exactly as the PDF review screen does. No picture-reading needed.
            return extract_prescription_fields(extract_pdf_text(data))
        except PdfParseError as exc:
            if "No readable text" not in str(exc):
                raise                                # damaged, encrypted, too large: not a scan, say so
            # a scanned PDF has pages but no text: read the page images
    if mime not in IMAGE_TYPES:
        raise PdfParseError("Please send the prescription as a PDF, or a photo (JPG or PNG).")
    return extract_prescription_fields("(the prescription is the attached image or document)", file=(mime, data))


def extract_prescription_fields(text: str, file: Optional[tuple] = None) -> dict:
    """Ask Gemini to map the prescription text to our structured shape. Returns a dict with
    ``patient``, ``prescription`` and ``items`` keys. Every item is normalised so an uncertain or
    missing dose becomes ``dose_text=None`` with an ``open_question`` — it never carries a guess.

    Raises :class:`PdfParseError` if no model key is configured or the model returns nothing usable.
    """
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_LIVE_API_KEY") or "").strip()
    if not api_key:
        raise PdfParseError("Prescription reading is not configured (no model key set).")
    try:
        import google.generativeai as genai
    except Exception as exc:                                         # pragma: no cover - dependency guard
        raise PdfParseError("The prescription reader is not available on the server.") from exc

    model_name = os.getenv("SANJEEVANI_PDF_MODEL", "gemini-flash-latest")
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config={"response_mime_type": "application/json", "temperature": 0},
        )
        prompt = _EXTRACTION_PROMPT % text
        contents = [prompt, {"mime_type": file[0], "data": file[1]}] if file else prompt
        response = model.generate_content(contents)
        raw = (getattr(response, "text", None) or "").strip()
    except Exception as exc:
        logger.warning("[Sanjeevani] PDF extraction call failed: %s", exc)
        raise PdfParseError("The prescription could not be read just now. Please try again.") from exc

    data = _loads_lenient(raw)
    if not isinstance(data, dict):
        raise PdfParseError("The prescription could not be read into the expected form.")

    patient = data.get("patient") or {}
    prescription = data.get("prescription") or {}
    raw_items = data.get("items") or []
    if not isinstance(raw_items, list):
        raw_items = []

    items = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        dose = (it.get("dose_text") or "").strip() or None
        confidence = (it.get("confidence") or "").strip().lower()
        open_q = (it.get("open_question") or "").strip() or None
        # Safety gate: no confident dose => flag for the pharmacist, never a guessed dose.
        uncertain = dose is None or confidence == "low"
        status = "needs_clarification" if uncertain else "confirmed"
        if uncertain and not open_q:
            open_q = "The dose was not clear on the prescription. A pharmacist must confirm it."
        if uncertain:
            dose = None
        items.append({
            "drug_name": (it.get("drug_name") or "").strip() or None,
            "strength": (it.get("strength") or "").strip() or None,
            "form": (it.get("form") or "").strip() or None,
            "dose_text": dose,
            "frequency_per_day": _coerce_int(it.get("frequency_per_day")),
            "timing": (it.get("timing") or "").strip() or None,
            "duration_days": _coerce_int(it.get("duration_days")),
            "warnings": (it.get("warnings") or "").strip() or None,
            "status": status,
            "open_question": open_q,
        })

    return {
        "patient": {
            "first_name": (patient.get("first_name") or "").strip() or None,
            "last_name": (patient.get("last_name") or "").strip() or None,
            "guardian_name": (patient.get("guardian_name") or "").strip() or None,
            "identity_dob": (patient.get("identity_dob") or "").strip() or None,
        },
        "prescription": {
            "external_id": (prescription.get("external_id") or "").strip() or None,
            "doctor_name": (prescription.get("doctor_name") or "").strip() or None,
            "facility": (prescription.get("facility") or "").strip() or None,
            "issued_at": (prescription.get("issued_at") or "").strip() or None,
            "course_days": _coerce_int(prescription.get("course_days")),
            "extra_instruction": (prescription.get("extra_instruction") or "").strip() or None,
        },
        "items": items,
    }


def _loads_lenient(raw: str):
    """Parse JSON, tolerating a stray ```json fence if the model adds one."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None
