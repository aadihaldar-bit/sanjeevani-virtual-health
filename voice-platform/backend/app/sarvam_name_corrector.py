"""
Background Sarvam name correction for medical appointments.

After an appointment is booked, fetches the full call recording from
Smartflo CDR, re-transcribes it with Sarvam STT (with Indian name hints),
then uses an LLM to extract the patient name from the transcript.
"""

import os
import re
import asyncio
import logging
import subprocess
import tempfile
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SMARTFLO_LOGIN_URL = "https://api-smartflo.tatateleservices.com/v1/auth/login"
SMARTFLO_CDR_URL = "https://api-smartflo.tatateleservices.com/v1/call/records"
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"

_smartflo_token: Optional[str] = None
_smartflo_token_ts: float = 0

INDIAN_NAME_HINTS = [
    "Aarav", "Aadi", "Aadit", "Aadhya", "Aanya", "Aarushi", "Abhishek",
    "Aditi", "Aisha", "Ajay", "Akash", "Akshat", "Amit", "Amita", "Anamika",
    "Ananya", "Anil", "Anita", "Anjali", "Ankita", "Ankit", "Anusha", "Arjun",
    "Arun", "Aruna", "Ashok", "Bhavna", "Chandra", "Deepak", "Deepika",
    "Devi", "Dhruv", "Divya", "Ganesh", "Gaurav", "Geeta", "Hari", "Harsh",
    "Harsha", "Hemant", "Ishaan", "Jaya", "Karan", "Kavita", "Kishore",
    "Krishna", "Kumar", "Lakshmi", "Lata", "Mahesh", "Manish", "Meena",
    "Mohan", "Mukesh", "Nandini", "Naresh", "Naveen", "Neha", "Nikhil",
    "Nisha", "Pallavi", "Pankaj", "Pooja", "Pradeep", "Prakash", "Pranav",
    "Priya", "Priyanka", "Rahul", "Raj", "Rajesh", "Rakesh", "Rama", "Ramesh",
    "Rani", "Ravi", "Rekha", "Rishi", "Rohit", "Rohan", "Sachin", "Sandeep",
    "Sanjay", "Sarika", "Seema", "Shaila", "Shanti", "Shikha", "Shivani",
    "Shreya", "Shubham", "Sita", "Smita", "Sneha", "Sonika", "Sonali",
    "Sridhar", "Srinivas", "Sudhir", "Sunil", "Sunita", "Suresh", "Swati",
    "Tanvi", "Usha", "Varun", "Vijay", "Vikram", "Vinay", "Vinod", "Vikas",
    "Vishal", "Vivek", "Yash",
    "Agarwal", "Banerjee", "Bhat", "Chakraborty", "Chauhan", "Das", "Desai",
    "Gupta", "Haldar", "Halder", "Iyer", "Jain", "Joshi", "Kalder", "Kaldas",
    "Kapoor", "Khan", "Kulkarni", "Kumar", "Malhotra", "Mehta", "Menon",
    "Mishra", "Mukherjee", "Nair", "Patel", "Pillai", "Rao", "Reddy",
    "Saxena", "Sharma", "Shukla", "Singh", "Sinha", "Trivedi", "Varma",
    "Verma", "Yadav",
]


async def _get_smartflo_token() -> Optional[str]:
    global _smartflo_token, _smartflo_token_ts
    import time
    if _smartflo_token and (time.time() - _smartflo_token_ts) < 3000:
        return _smartflo_token

    user = os.getenv("SMARTFLO_API_USER", "")
    pwd = os.getenv("SMARTFLO_API_PASSWORD", "")
    if not user or not pwd:
        logger.warning("[SarvamNameFix] SMARTFLO_API_USER / SMARTFLO_API_PASSWORD not set")
        return None

    async with httpx.AsyncClient() as client:
        r = await client.post(SMARTFLO_LOGIN_URL,
            json={"email": user, "password": pwd},
            headers={"Accept": "application/json"}, timeout=10)
        if r.status_code == 200 and r.json().get("success"):
            _smartflo_token = r.json()["access_token"]
            _smartflo_token_ts = time.time()
            return _smartflo_token
    logger.error(f"[SarvamNameFix] Smartflo auth failed: {r.status_code}")
    return None


async def _fetch_recording_url(call_sid: str) -> Optional[str]:
    token = await _get_smartflo_token()
    if not token:
        return None

    async with httpx.AsyncClient() as client:
        r = await client.get(SMARTFLO_CDR_URL,
            params={"call_id": call_sid, "limit": "1"},
            headers={"Authorization": token, "Accept": "application/json"}, timeout=15)
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                return results[0].get("recording_url")
    return None


async def _download_recording(url: str) -> Optional[bytes]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(url, timeout=30)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
    return None


def _split_and_transcribe_sync(audio_bytes: bytes, sarvam_key: str) -> list[str]:
    """Split MP3 into 25s WAV chunks and transcribe each with Sarvam."""
    transcripts = []
    fd, tmp_in = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        with open(tmp_in, "wb") as f:
            f.write(audio_bytes)

        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", tmp_in],
            capture_output=True, text=True)
        duration = float(proc.stdout.strip()) if proc.stdout.strip() else 0
        if duration <= 0:
            return []

        hints_str = ", ".join(INDIAN_NAME_HINTS[:50])

        for start in range(0, int(duration), 25):
            fd, tmp_out = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", tmp_in, "-ss", str(start), "-t", "25",
                     "-ar", "16000", "-ac", "1", "-f", "wav", tmp_out],
                    capture_output=True, timeout=15)
                with open(tmp_out, "rb") as f:
                    chunk = f.read()
                if len(chunk) < 500:
                    continue

                data = {
                    "model": "saarika:v2.5",
                    "language_code": "en-IN",
                    "with_timestamps": "false",
                }

                r = httpx.post(SARVAM_STT_URL,
                    headers={"api-subscription-key": sarvam_key},
                    files={"file": ("chunk.wav", chunk, "audio/wav")},
                    data=data,
                    timeout=30)
                if r.status_code == 200:
                    txt = r.json().get("transcript", "").strip()
                    if txt:
                        transcripts.append(txt)
            finally:
                try:
                    os.unlink(tmp_out)
                except OSError:
                    pass
    finally:
        try:
            os.unlink(tmp_in)
        except OSError:
            pass
    return transcripts


async def _extract_name_with_llm(transcript: str, original_name: str) -> Optional[str]:
    """Extract the patient's real name from the call transcript (background job: accuracy over latency)."""
    from .config import get_config
    from .llm_roles import LLMRoles

    llm = LLMRoles(get_config()).background
    if llm is None:
        logger.warning("[SarvamNameFix] no LLM available, falling back to regex")
        return _regex_extract_name(transcript)

    prompt = f"""You are analyzing a medical clinic phone call transcript. The receptionist asked for the patient's name during the call.

The STT system originally captured the patient name as: "{original_name}"
This may be incorrect — STT often mishears Indian names (e.g. "Sonic" instead of "Sonika", "Arid Kalder" instead of "Sarika Kaldas").

Here is the full call transcript:
---
{transcript}
---

Extract the patient's ACTUAL full name from this transcript. The patient is the CALLER, not the receptionist or doctor. Look for where they state their name (e.g. "my name is...", "I am...", or answering when asked "your name?").

Rules:
- Return ONLY the name, nothing else
- If the name sounds like a mishearing of a common Indian name, correct it (e.g. "Arad Calder" → "Sarika Kaldas", "Adi Hoga" → "Adi Haldar")
- If you cannot confidently determine the patient's name, return exactly: UNKNOWN
- Do NOT return the receptionist's name, doctor's name, or any other non-patient name
- The name should be properly capitalized"""

    try:
        text = await llm.generate(
            prompt=prompt, history=[], max_tokens=60, tools=None,
            system_prompt="You extract a caller's name from a call transcript. Answer with the name only.")
    except Exception as exc:
        logger.warning(f"[SarvamNameFix] LLM extraction failed ({exc}), falling back to regex")
        return _regex_extract_name(transcript)
    if not isinstance(text, str):
        return _regex_extract_name(transcript)

    text = text.strip()
    if text == "UNKNOWN" or len(text) > 40 or len(text) < 2:
        return None
    name = text.split("\n")[0].strip().strip('"').strip("'")
    if name and name != "UNKNOWN":
        return name
    return _regex_extract_name(transcript)


def _regex_extract_name(full: str) -> Optional[str]:
    """Fallback regex extraction if no LLM is available."""
    m = re.search(
        r"(?:my name is|i am|i'm|name is|call me)\s+"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        full, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip().title()
        skip_first = {"The", "A", "An", "Not", "So", "Very", "Just", "Also",
                      "Clinic", "Doctor", "Receptionist", "Yeah", "Yes"}
        if candidate.split()[0] not in skip_first:
            return candidate

    m2 = re.search(
        r"(?:your (?:full )?name|what(?:'s| is) your name)[^.?]*[.?]\s*"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        full, re.IGNORECASE)
    if m2:
        candidate = m2.group(1).strip().title()
        skip = {"Yes", "No", "Ok", "Okay", "Thanks", "Thank", "Sure", "Hello", "Hi",
                "Headache", "Fever", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
        if candidate.split()[0] not in skip:
            return candidate
    return None


async def correct_appointment_name(
    appt_row_id: int,
    appt_id: str,
    call_sid: str,
    original_name: str,
):
    """Background task: fetch Smartflo recording, re-transcribe with Sarvam, extract name with an LLM."""
    try:
        await asyncio.sleep(45)

        from .config import get_config
        cfg = get_config()
        sarvam_key = cfg.stt.sarvam_api_key or os.getenv("SARVAM_API_KEY", "")
        if not sarvam_key:
            logger.info("[SarvamNameFix] No Sarvam API key, skipping")
            return

        rec_url = await _fetch_recording_url(call_sid)
        if not rec_url:
            logger.info(f"[SarvamNameFix] No recording URL for {call_sid}")
            return

        audio = await _download_recording(rec_url)
        if not audio:
            logger.warning(f"[SarvamNameFix] Failed to download recording for {call_sid}")
            return

        logger.info(f"[SarvamNameFix] Downloaded {len(audio)} bytes for {call_sid}, transcribing...")

        loop = asyncio.get_event_loop()
        transcripts = await loop.run_in_executor(
            None, _split_and_transcribe_sync, audio, sarvam_key)

        if not transcripts:
            logger.info(f"[SarvamNameFix] No transcripts for {call_sid}")
            return

        full_transcript = " ".join(transcripts)
        logger.info(f"[SarvamNameFix] Sarvam transcript: {full_transcript[:200]}...")

        corrected = await _extract_name_with_llm(full_transcript, original_name)
        if not corrected or corrected == original_name:
            logger.info(f"[SarvamNameFix] No correction needed for '{original_name}'")
            return

        from .database import async_session
        from .models import Appointment
        from .websocket_manager import dashboard_manager
        async with async_session() as db:
            appt = await db.get(Appointment, appt_row_id)
            if appt:
                old = appt.patient_name
                appt.patient_name = corrected
                await db.commit()
                logger.info(f"[SarvamNameFix] {appt_id}: '{old}' -> '{corrected}'")
                await dashboard_manager.broadcast({
                    "type": "appointment_updated",
                    "appointment": appt.to_dict(),
                })

    except Exception as e:
        logger.error(f"[SarvamNameFix] Failed for {appt_id}: {e}", exc_info=True)
