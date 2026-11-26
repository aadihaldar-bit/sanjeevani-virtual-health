"""The tools of the Sanjeevani Appointment Desk and Feedback Desk, for a patient who phones in.

Each handler is a method of ToolExecutor (this is a mixin) and returns a plain sentence or a few plain lines for the model to
say. Rules that hold throughout:
 - a doctor, a time, a fee or a reference is only ever said if a handler returned it (sanjeevani_care decides what is free);
 - a caller is recognised by phone number (the patient directory) and, when we hold a date of birth for them, asked for it
   before anything is shared about their visits;
 - everything is scoped to the call's company.
"""
import logging
import random
import re
from datetime import datetime
from typing import Any, List, Optional

from . import sanjeevani_care as care

logger = logging.getLogger("sanjeevani.desk")


def digits(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def iso_dob(text: str, parse) -> Optional[str]:
    """A spoken or written date of birth as 1995-08-15, or None when it cannot be read as a whole date."""
    got = parse(text or "")
    if not got:
        return None
    d, m, y = got
    if y < 100:
        y += 2000 if y <= datetime.utcnow().year % 100 else 1900
    try:
        return datetime(y, m, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


async def patient_by_phone(db, tenant_id, phone: Optional[str]):
    """The one patient of this company whose number this is, else None (also None when two patients share the number)."""
    from sqlalchemy import select
    from .models import Patient
    number = digits(phone)
    if len(number) < 7 or tenant_id is None:
        return None
    rows = (await db.execute(select(Patient).where(
        Patient.tenant_id == tenant_id, Patient.phone_e164.like(f"%{number[-9:]}")))).scalars().all()
    rows = [p for p in rows if digits(p.phone_e164)[-9:] == number[-9:]]
    return rows[0] if len(rows) == 1 else None


class SanjeevaniDeskMixin:
    def _register_sanjeevani_desk_tools(self) -> None:
        reg = self.register_tool
        reg(name="find_doctors", handler=self._desk_find_doctors,
            description="Find the right doctors for what the caller has described, with fees and the next free times. "
                        "Pass everything the caller has said about the problem, including the other symptoms. "
                        "Leave seniority empty to get both a senior consultant and a standard doctor.",
            parameters={"problem": {"type": "string", "description": "The problem and all symptoms the caller described, in the caller's words"},
                        "confirmed_with_caller": {"type": "string", "description": "yes only after you repeated the problem back to the caller and they agreed it is right"},
                        "seniority": {"type": "string", "description": "senior, standard, or empty for both", "optional": True}})
        reg(name="quote_visit_cost", handler=self._desk_quote,
            description="Work out what the visit costs the caller: the fee, what the insurer pays and what the caller pays. "
                        "Use insurer none for self-pay.",
            parameters={"doctor": {"type": "string", "description": "The doctor's name"},
                        "insurer": {"type": "string", "description": "The insurer the caller named, or none", "optional": True}})
        reg(name="book_doctor_appointment", handler=self._desk_book,
            description="Book the appointment once the caller has chosen a doctor and a time and agreed the cost. "
                        "slot must be exactly the slot value find_doctors returned. For a caller we do not know yet, also pass "
                        "their date of birth. Returns the booking reference to read out.",
            parameters={"doctor": {"type": "string", "description": "The doctor's name"},
                        "slot": {"type": "string", "description": "The slot value from find_doctors, like 2026-10-26T10:30"},
                        "patient_name": {"type": "string", "description": "The patient's full name"},
                        "problem": {"type": "string", "description": "The reason for the visit", "optional": True},
                        "insurer": {"type": "string", "description": "The insurer, or none", "optional": True},
                        "date_of_birth": {"type": "string", "description": "The patient's date of birth, for a new patient", "optional": True}})
        reg(name="check_feedback_eligibility", handler=self._desk_feedback_eligibility,
            description="Check whether the caller has had a completed visit with a doctor, which is what makes them eligible "
                        "to give feedback. Call it before asking anything about the visit.",
            parameters={})
        reg(name="record_visit_feedback", handler=self._desk_record_feedback,
            description="Save the caller's feedback about a completed visit. meeting_id is the number check_feedback_eligibility gave.",
            parameters={"meeting_id": {"type": "string", "description": "The visit number from check_feedback_eligibility"},
                        "rating": {"type": "string", "description": "Overall experience from 1 to 5"},
                        "medicine_helped": {"type": "string", "description": "yes, partly or no", "optional": True},
                        "comments": {"type": "string", "description": "What the caller said, in their own words", "optional": True},
                        "wants_doctor_followup": {"type": "string", "description": "yes if the caller wants another meeting with the doctor", "optional": True}})
        reg(name="search_knowledge", handler=self._desk_search,
            description="Look up an answer in this desk's own reference documents. Answer only from what it returns.",
            parameters={"question": {"type": "string", "description": "What to look up"}})

    # ── helpers ────────────────────────────────────────────────────────────────────────────
    async def _desk_call(self, db, call_id: Any):
        """(call, meta, tenant_id)"""
        call, meta = await self._sanjeevani_call_meta(db, call_id)
        return call, meta, getattr(call, "tenant_id", None)

    async def _desk_patient(self, db, call, meta, tenant_id, caller_number: str = ""):
        """The patient this call is from: the one the call was placed for, else the one whose number is calling."""
        from .models import Patient
        if meta.get("patient_id"):
            p = await db.get(Patient, int(meta["patient_id"]))
            if p is not None:
                return p
        return await patient_by_phone(db, tenant_id, caller_number or getattr(call, "caller_number", ""))

    async def _desk_gate(self, call_id: Any, patient) -> Optional[str]:
        """None when we may talk about this patient's visits: no date of birth on file to check, or already confirmed."""
        if patient is None or not (patient.identity_dob or "").strip():
            return None
        return await self._sanjeevani_identity_gate(call_id)

    async def _desk_taken(self, db, tenant_id, doctor_names: List[str]) -> dict:
        from sqlalchemy import select
        from .models import DoctorMeetingRequest
        rows = (await db.execute(select(DoctorMeetingRequest.doctor_name, DoctorMeetingRequest.slot_start).where(
            DoctorMeetingRequest.tenant_id == tenant_id, DoctorMeetingRequest.status == "booked",
            DoctorMeetingRequest.slot_start.isnot(None), DoctorMeetingRequest.doctor_name.in_(doctor_names)))).all()
        out: dict = {}
        for name, start in rows:
            out.setdefault(name, set()).add(start)
        return out

    @staticmethod
    def _desk_doctor_line(d: dict, slots) -> str:
        times = "; ".join(f"{s.label} [slot={s.key}]" for s in slots) or "no opening in the next three weeks"
        return (f"{d['name']}, {d['title']} in {d['specialty']}, {d['experience_years']} years of experience, "
                f"fee {care.money(d['fee'])}. Next openings: {times}.")

    # ── booking ────────────────────────────────────────────────────────────────────────────
    async def _desk_find_doctors(self, problem: str = "", seniority: str = "", confirmed_with_caller: str = "",
                                 call_id: Any = None, **kwargs) -> str:
        from .database import async_session
        if not (problem or "").strip():
            return "What problem or symptoms should I find a doctor for?"
        if not self._as_bool(confirmed_with_caller):
            # A phone line mishears ("eye pain" heard as "hip pain"): the caller confirms the problem before it picks a doctor.
            return (f"Do not search yet. Say back, in one short sentence, what you understood: the problem is \"{problem.strip()}\". "
                    "Ask the caller if that is right. If they correct you, use their words. Then call this again with "
                    "confirmed_with_caller set to yes.")
        specialty, _ = care.match_specialty(problem)
        docs = care.doctors_for(specialty, seniority) or care.doctors_for(specialty)
        async with async_session() as db:
            _, _, tenant_id = await self._desk_call(db, call_id)
            taken = await self._desk_taken(db, tenant_id, [d["name"] for d in docs])
        now = datetime.utcnow()
        lines = [f"Best-fit specialty: {specialty}."]
        for d in docs:
            lines.append(self._desk_doctor_line(d, care.next_slots(d, now, taken.get(d["name"]), count=2)))
        if not seniority:
            lines.append("Ask the caller whether they prefer the senior consultant or the standard doctor, then offer that doctor's two times.")
        lines.append("Say each time in words. When booking, pass the slot value back exactly as shown. Never offer a time that is not listed here.")
        return "\n".join(lines)

    async def _desk_quote(self, doctor: str = "", insurer: str = "", **kwargs) -> str:
        d = care.find_doctor(doctor)
        if d is None:
            return "I could not find that doctor. Please tell me the doctor's name again."
        q = care.quote(d["fee"], insurer)
        if not q["insurer"]:
            return f"{d['name']}'s fee is {care.money(q['fee'])}. With no insurance the caller pays {care.money(q['payable'])}."
        if not q["accepted"]:
            return (f"{q['insurer']} is not on our list of accepted insurers, so the caller pays the full fee of "
                    f"{care.money(q['fee'])}. The insurers we accept are {', '.join(care.directory()['insurers'])}.")
        return (f"{d['name']}'s fee is {care.money(q['fee'])}. {q['insurer']} covers {int(q['coverage'] * 100)} percent, "
                f"which is {care.money(q['covered'])}. The caller pays {care.money(q['payable'])}.")

    async def _desk_book(self, doctor: str = "", slot: str = "", patient_name: str = "", problem: str = "",
                         insurer: str = "", date_of_birth: str = "", call_id: Any = None, caller_number: str = "", **kwargs) -> str:
        from .database import async_session
        from .models import DoctorMeetingRequest, Patient
        d = care.find_doctor(doctor)
        if d is None:
            return "I could not find that doctor. Please tell me the doctor's name again."
        name = (patient_name or "").strip()
        async with async_session() as db:
            call, meta, tenant_id = await self._desk_call(db, call_id)
            if tenant_id is None:
                return "I'm sorry, I can't book on this call right now."
            patient = await self._desk_patient(db, call, meta, tenant_id, caller_number)
            if patient is None and not name:
                return "Before I book, may I have the patient's full name?"
            now = datetime.utcnow()
            taken = (await self._desk_taken(db, tenant_id, [d["name"]])).get(d["name"], set())
            chosen = care.slot_from_key(d, slot, now, taken)
            if chosen is None:
                again = care.next_slots(d, now, taken, count=2)
                times = "; ".join(f"{s.label} [slot={s.key}]" for s in again) or "none in the next three weeks"
                return f"That time is not available for {d['name']}. The next openings are: {times}. Ask the caller which they prefer."
            if patient is None:
                first, _, last = name.partition(" ")
                dob = iso_dob(date_of_birth, self._parse_dob)
                patient = Patient(tenant_id=tenant_id, first_name=first or name, last_name=last or None,
                                  phone_e164=caller_number or getattr(call, "caller_number", None), identity_dob=dob,
                                  preferred_language="en", consent_calls=True, consent_recorded_at=now,
                                  extra_json={"source": "inbound_booking"})
                db.add(patient)
                await db.flush()
            q = care.quote(d["fee"], insurer)
            meeting = DoctorMeetingRequest(
                tenant_id=tenant_id, patient_id=patient.id, call_id=self._cid(call_id), doctor_name=d["name"],
                reason=(problem or "").strip() or "Appointment", status="booked", booked_slot=chosen.label,
                source="inbound", specialty=d["specialty"], slot_start=chosen.start_utc, patient_name=name or patient.first_name,
                phone=patient.phone_e164, insurer=q["insurer"] or None, fee_amount=q["fee"], copay_amount=q["payable"],
                payment_status="to_pay_at_visit")
            db.add(meeting)
            await db.flush()
            meeting.external_ref = f"BJ-{meeting.id:03d}"
            ref, mid = meeting.external_ref, meeting.id
            await db.commit()
        who = f" for {name}" if name else ""
        pay = (f"{care.money(q['payable'])} is payable at the visit" if q["payable"] else "nothing is payable at the visit")
        return (f"Booked{who} with {d['name']} on {chosen.label}. The booking reference is {' '.join(ref)}; read it out slowly. "
                f"{pay[0].upper()}{pay[1:]}. Ask the caller to arrive ten minutes early with their Emirates ID and insurance card.")

    # ── feedback ───────────────────────────────────────────────────────────────────────────
    async def _desk_feedback_eligibility(self, call_id: Any = None, caller_number: str = "", **kwargs) -> str:
        from sqlalchemy import select
        from .database import async_session
        from .models import DoctorMeetingRequest, MedicineFeedback
        async with async_session() as db:
            call, meta, tenant_id = await self._desk_call(db, call_id)
            patient = await self._desk_patient(db, call, meta, tenant_id, caller_number)
        if patient is None:
            return ("NOT ELIGIBLE: there is no patient record for this number, so there is no visit to give feedback on. "
                    "Offer to book an appointment instead.")
        blocked = await self._desk_gate(call_id, patient)
        if blocked:
            return blocked
        async with async_session() as db:
            meetings = (await db.execute(select(DoctorMeetingRequest).where(
                DoctorMeetingRequest.patient_id == patient.id, DoctorMeetingRequest.tenant_id == tenant_id)
                .order_by(DoctorMeetingRequest.id.desc()))).scalars().all()
            given = {m for (m,) in (await db.execute(select(MedicineFeedback.meeting_id).where(
                MedicineFeedback.meeting_id.isnot(None), MedicineFeedback.tenant_id == tenant_id))).all()}
        done = [m for m in meetings if m.status == "done"]
        waiting = [m for m in done if m.id not in given]
        if waiting:
            m = waiting[0]
            when = m.booked_slot or "recently"
            return (f"ELIGIBLE: completed visit with {m.doctor_name} ({when}). meeting_id={m.id}. "
                    "Ask how the visit went, whether the medicine they were given worked well, and their overall experience from 1 to 5.")
        if done:
            return "NOT ELIGIBLE: feedback for the completed visit was already given. Thank them; do not ask again."
        booked = [m for m in meetings if m.status == "booked"]
        if booked:
            m = booked[0]
            return (f"NOT ELIGIBLE YET: the appointment with {m.doctor_name} ({m.booked_slot or 'upcoming'}) has not been marked completed. "
                    "Feedback opens after the visit; the clinic also calls two days after the visit to ask. Offer to help with something else.")
        return "NOT ELIGIBLE: no completed visit on file. Offer to book an appointment."

    async def _desk_record_feedback(self, meeting_id: Any = "", rating: Any = "", medicine_helped: str = "",
                                    comments: str = "", wants_doctor_followup: Any = "", call_id: Any = None,
                                    caller_number: str = "", **kwargs) -> str:
        from .database import async_session
        from .models import DoctorMeetingRequest, MedicineFeedback
        async with async_session() as db:
            call, meta, tenant_id = await self._desk_call(db, call_id)
            patient = await self._desk_patient(db, call, meta, tenant_id, caller_number)
        if patient is None:
            return "I'm sorry, I can't find a record for this number to save feedback against."
        blocked = await self._desk_gate(call_id, patient)
        if blocked:
            return blocked
        mid = None
        try:
            mid = int(meeting_id) if str(meeting_id).strip() not in ("", "null", "none") else meta.get("meeting_id")
        except (TypeError, ValueError):
            mid = meta.get("meeting_id")
        sat = self._as_int(rating) or None
        helped = (medicine_helped or "").strip().lower() or None
        wants = self._as_bool(wants_doctor_followup)
        text = (comments or "").strip() or None
        async with async_session() as db:
            meeting = await db.get(DoctorMeetingRequest, int(mid)) if mid else None
            if meeting is None or meeting.patient_id != patient.id or meeting.status != "done":
                return "I can only save feedback for a visit that the clinic has marked completed."
            db.add(MedicineFeedback(tenant_id=tenant_id, meeting_id=meeting.id, call_id=self._cid(call_id), helped=helped,
                                    satisfaction=sat, notes=text, wants_doctor=wants))
            await db.commit()
            unhappy = wants or helped in ("no", "partly") or (sat is not None and sat <= 3)
            if not unhappy:
                return "Thank you. Your feedback has been saved. Say you are glad it went well."
            call, _, _ = await self._desk_call(db, call_id)
            reason = text or (f"Low rating ({sat})" if sat is not None else "Patient asked for a follow-up after the visit")
            await self._sanjeevani_create_meeting_and_ticket(
                db, tenant_id=tenant_id, call=call, patient=patient, prescription=None, doctor=meeting.doctor_name,
                reason=f"After the visit on {meeting.booked_slot or 'a recent date'}: {reason}",
                priority=self._sanjeevani_care_priority(helped, sat, text if helped in ("no",) else None, False),
                caller_number=caller_number or patient.phone_e164 or "")
        return ("Thank you for telling us. I'm sorry it was not better. I've asked the care team to look into it, and the clinic "
                "will confirm a follow-up meeting with the doctor within one working day. Give no medical advice.")

    # ── reference documents ─────────────────────────────────────────────────────────────────
    async def _desk_search(self, question: str = "", call_id: Any = None, node_docs: Any = None, **kwargs) -> str:
        """Answers from the documents attached to the desk the caller is speaking with (node_docs, set by the call), within the company."""
        from .database import async_session
        if not (question or "").strip():
            return "What would you like me to look up?"
        async with async_session() as db:
            _, _, tenant_id = await self._desk_call(db, call_id)
        nothing = "I don't have that in my reference documents. Say so, and offer to arrange a call with the clinic."
        docs = [str(x) for x in (node_docs or []) if x] if isinstance(node_docs, (list, tuple)) else []
        if tenant_id is None or not docs:
            return nothing
        try:
            from . import knowledge_routes
            found = await knowledge_routes.rag_engine.search(question, node_docs=docs, tenant_id=tenant_id, top_k=3)
        except Exception as exc:
            logger.warning("[Sanjeevani] desk knowledge look-up failed: %s", exc)
            return nothing
        if not found or not found.strip() or found.startswith("No relevant"):
            return nothing
        return found.strip()[:1800]
