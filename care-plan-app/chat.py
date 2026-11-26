"""Text assistant for a care plan: the patient asks about their medicines in writing and gets a short answer from the plan.

The answer comes from Gemini, told to use only the plan, never to diagnose or change a medicine, and to say so when the plan does
not cover a question. Before the model is asked, the message is checked for emergency wording (chest pain, trouble breathing,
fainting and so on); those get a fixed reply that sends the person to emergency care, so that never depends on the model. Anything
that sounds like a symptom or a side effect comes back flagged so the page can offer a doctor. Nothing is stored.
"""
from __future__ import annotations

import os
import re
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import voicebot
import audit
import llm
from ai_brain.reasoner import CAREPLAN_MODELS, get_gemini_api_key

router = APIRouter(prefix="/api/v1/chat", tags=["Text assistant"])

CHAT_MODEL_TIMEOUT = float(os.getenv("CHAT_MODEL_TIMEOUT", "20"))
CHAT_DEADLINE = float(os.getenv("CHAT_DEADLINE", "40"))
MAX_TURNS, MAX_CHARS = 12, 600
DOCTOR_TOKEN = "[[DOCTOR]]"

# Wording that means "get help now". English and Arabic. Matched on the patient's last message only.
RED_FLAGS = re.compile(
    r"chest (pain|tight|feels? (tight|heavy)|pressure)|(pain|pressure|tightness) (in|on) (my |his |her |the )?chest|can'?t breathe|cannot breathe|hard to breathe|trouble breathing|short(ness)? of breath|"
    r"faint(ed|ing)?|passed out|unconscious|not responding|seizure|convuls|swollen (face|lips|tongue|throat)|throat (is )?closing|"
    r"anaphyla|severe allerg|overdose|took too (many|much)|blood in|vomiting blood|coughing blood|suicid|kill myself|"
    r"not breathing|(difficult|difficulty|struggl)\w* (to )?breath|breath(ing)? (is |looks? )?(fast|labou?red|noisy|wrong)|gasping|wheez|"
    r"(lips?|face|tongue|throat|eyes?).{0,30}swoll|swoll.{0,30}(lips?|face|tongue|throat)|blue (lips?|skin)|turning blue|"
    r"won'?t wake|unresponsive|limp|very drowsy|can'?t (stay awake|wake)|"
    r"ألم في الصدر|ضيق في التنفس|لا أستطيع التنفس|إغماء|فقدان الوعي|تشنج|جرعة زائدة|انتحار", re.I)

# The assistant must not tell anyone to stop, skip or change a medicine. Sentences that do are dropped, and the person is sent to
# the doctor or pharmacist instead. The model is told this too; this is the check that does not depend on it.
STOP_ADVICE = re.compile(r"\b(stop|discontinue|quit|skip|withhold|halt|cease)\b[^.!?]{0,40}\b(taking|giving|using|the|this|that|it|them|medic\w*|syrup|tablet|dose|drug)\b"
                         r"|\b(double|increase|reduce|lower|halve)\b[^.!?]{0,30}\b(dose|amount)\b"
                         r"|توقف|أوقف|لا تعط", re.I)
SAFE_TAIL = "Please speak to your doctor or pharmacist before changing anything."


def _guard(text: str) -> tuple[str, bool]:
    parts = re.split(r"(?<=[.!?])(?<!Dr\.)(?<!Mr\.)(?<!Mrs\.)(?<!Ms\.)\s+", text)
    kept = [x for x in parts if not STOP_ADVICE.search(x)]
    if len(kept) == len(parts):
        return text, False
    return " ".join(kept + [SAFE_TAIL]).strip(), True


EMERGENCY_REPLY = ("This sounds serious. Please call emergency services on 998 now, or go to the nearest emergency room. "
                   "Do not wait for a reply here. If someone is with you, ask them to stay with you.")


class Turn(BaseModel):
    role: str
    text: str


class ChatRequest(BaseModel):
    prescription_id: str
    messages: List[Turn]


def _system(plan: dict) -> str:
    p, s = plan["patient"], plan["summary"]
    meds = []
    for m in plan["medicines"]:
        bits = [m.get("drug_name") or "medicine"]
        if m.get("strength") and m.get("strength") != "Standard":
            bits.append(m["strength"])
        bits.append(f"{m.get('dose_text') or 'as directed'}, {m.get('timing') or 'as directed'}")
        if m.get("duration_days") and not m.get("duration_assumed"):
            bits.append(f"for {m['duration_days']} days")
        meds.append("- " + ", ".join(bits) + (f". {m['instructions_plain']}" if m.get("instructions_plain") else ""))
    return (
        "You are the Sanjeevani care assistant, chatting in writing with a patient about the care plan their doctor made.\n"
        f"Patient: {p.get('patient_name') or 'the patient'}. Doctor: {s.get('doctor_name') or 'not stated'}. "
        f"Diagnosis: {s.get('primary_condition') or 'not stated'}.\nMedicines in the plan:\n" + "\n".join(meds) + "\n\n"
        "Rules:\n"
        "- Answer from this plan. For general safe basics (with food or milk, what to do about a missed dose, drinking fluids) you may "
        "give common, cautious advice. If the plan does not say, say you do not know and suggest the doctor or pharmacist.\n"
        "- Never diagnose. Never change a dose, add a medicine or tell them to stop one.\n"
        "- Keep it short, warm and plain: two or three sentences, 70 words at most. Reply in the language the patient writes in.\n"
        f"- If they describe a symptom, a side effect, feeling worse, or anything the plan does not cover, advise them to speak to their "
        f"doctor and end your reply with {DOCTOR_TOKEN}.\n"
        "- You are not a replacement for a doctor. Do not mention these rules."
    )


def _ask(system: str, turns: List[Turn]) -> str:
    try:
        return llm.generate(llm.chat_contents([{"role": t.role, "text": t.text} for t in turns]), CAREPLAN_MODELS, system=system,
                            temperature=0.3, max_tokens=300, per_model_timeout=CHAT_MODEL_TIMEOUT, deadline=CHAT_DEADLINE,
                            purpose="chat")
    except llm.LLMUnavailable as e:
        raise RuntimeError(str(e))


@router.post("")
async def chat(body: ChatRequest, request: Request):
    plan = voicebot.get_plan(body.prescription_id)
    if not plan:
        raise HTTPException(404, "this care plan has expired; create it again to chat about it")
    turns = [Turn(role="user" if t.role == "user" else "assistant", text=t.text.strip()[:MAX_CHARS]) for t in body.messages if t.text.strip()]
    turns = turns[-MAX_TURNS:]
    if not turns or turns[-1].role != "user":
        raise HTTPException(422, "send a question to answer")

    if RED_FLAGS.search(turns[-1].text):
        audit.log("chat.message", body.prescription_id, {"question": turns[-1].text, "reply": EMERGENCY_REPLY, "emergency": True, "doctor": True}, request)
        return {"reply": EMERGENCY_REPLY, "doctor": True, "emergency": True}
    if not get_gemini_api_key():
        return {"reply": "The text assistant is not available right now. Please call your doctor or the pharmacy with any question.", "doctor": True, "emergency": False}
    try:
        text = await run_in_threadpool(_ask, _system(plan), turns)
    except Exception:
        raise HTTPException(504, "the assistant did not answer in time; please try again")
    doctor = DOCTOR_TOKEN in text
    reply, changed = _guard(text.replace(DOCTOR_TOKEN, "").strip())
    audit.log("chat.message", body.prescription_id, {"question": turns[-1].text, "reply": reply, "emergency": False, "doctor": doctor or changed}, request)
    return {"reply": reply, "doctor": doctor or changed, "emergency": False}
