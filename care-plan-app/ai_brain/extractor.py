import os
import io
import time
from typing import Optional
from PIL import Image
import llm
from dotenv import load_dotenv

def get_gemini_api_key():
    load_dotenv(override=True)
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()

def extract_prescription_text(
    prescription_text: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    file_mime_type: Optional[str] = None
) -> str:
    """
    Extracts structured / readable clinical text from either:
    1. Direct prescription text
    2. Prescription image/PDF using Gemini Multimodal Vision OCR
    3. Both (merges text hints with file extraction)
    """
    print("\n" + "="*80)
    print(" [OCR / VISION EXTRACTOR] Ingesting clinical input...")
    print("="*80)
    
    extracted_text = ""
    api_key = get_gemini_api_key()

    if file_bytes:
        file_size_kb = len(file_bytes) / 1024
        print(f" -> File received: Size = {file_size_kb:.2f} KB | MIME = {file_mime_type}")
        if api_key:
            try:
                print(" -> Configuring Gemini Vision...")
                
                if file_mime_type and ("image" in file_mime_type or "png" in file_mime_type or "jpeg" in file_mime_type or "jpg" in file_mime_type):
                    print(" -> Running Multimodal Vision OCR on prescription image...")
                    start_t = time.time()
                    image = Image.open(io.BytesIO(file_bytes))
                    prompt = (
                        "You are an expert clinical OCR and medical transcription specialist. "
                        "Read this handwritten clinical/prescription note carefully. "
                        "Extract: Doctor name, Hospital, Patient findings, Medical condition/diagnosis, "
                        "Medications (if any), dosage, instructions, and Diagnostic/Lab tests recommended. "
                        "Provide a clear, detailed clinical transcription."
                    )
                    extracted_text = llm.generate([prompt, image], ["gemini-3.1-flash-lite", "gemini-3.5-flash"], temperature=0.1, max_tokens=2048, per_model_timeout=30, deadline=70, purpose="ocr")
                    duration = time.time() - start_t
                    print(f" -> [Vision OCR Success] Extracted {len(extracted_text)} characters in {duration:.2f}s.")
                    print(f" -> Extracted Snippet:\n{extracted_text[:200]}...\n")
                else:
                    extracted_text = file_bytes.decode("utf-8", errors="ignore")
                    print(f" -> Non-image file decoded: {len(extracted_text)} characters.")
            except Exception as e:
                print(f" [!] [OCR Error] Gemini Vision call failed: {e}")
                extracted_text = f"[OCR Error: {e}]"
        else:
            print(" [!] No GEMINI_API_KEY detected in .env. Skipping Vision OCR.")
            extracted_text = (
                "[Image received]: To enable real-time Vision OCR for handwritten doctor notes, "
                "add a free GEMINI_API_KEY to your .env file."
            )

    if prescription_text and prescription_text.strip():
        print(f" -> Direct prescription text provided ({len(prescription_text.strip())} chars):")
        print(f"    \"{prescription_text.strip()[:150]}...\"")
        if extracted_text and not extracted_text.startswith("["):
            merged = f"--- OCR TRANSCRIPTION ---\n{extracted_text}\n\n--- CLINICAL NOTES ---\n{prescription_text.strip()}"
            print(" -> Merged OCR transcription with provided text.")
            return merged
        return prescription_text.strip()

    if extracted_text:
        return extracted_text

    print(" [!] Warning: No readable prescription data found.")
    return "No readable prescription data provided."
