"""Set up the Sanjeevani Holdings patient-care workspace through the console's own API, so it can be re-run safely.

    python scripts/setup_sanjeevani.py --api http://127.0.0.1:8055 --token aadi@stl.test
    python scripts/setup_sanjeevani.py --api https://<console api> --token <sign-in token> --handover +971500000000 --patient-phone +971500000001

What it does, in order (each step is skipped if it is already done):
  1. creates the company "Sanjeevani Holdings" (or finds it) and sets its time zone to Gulf time and, if given, its handover number;
  2. uploads the knowledge bases from docs/sanjeevani_demo/knowledge/ into that company;
  3. saves the agent and makes it live: a front desk that hands an incoming caller to the Appointment, Pharmacy or Feedback desk (each
     with its own prompt, tools and documents), and the step that makes the calls we place;
  4. with --patient-phone, adds one made-up demo patient and prescription, which schedules the two follow-up calls;
  5. with --demo-directory, adds the demo patient directory (Aadi Haldar and Aarit Haldar), so an uploaded prescription
     that names one of them is called on the stored number;
  6. with --bucket-region, creates the company's own storage bucket in that region (call records are then kept there);
  7. with --calling-hours 00:00-23:59, sets the hours calls may go out (the default is 09:00-20:00 in the company's zone).

It needs a system administrator's token. It never touches another company. The prompts below are the same ones the
brief (docs/sanjeevani_agent_prompt.md, section 14) describes.
"""
import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sanjeevani_prompts as P  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_FOLDER = os.path.join(ROOT, "docs", "sanjeevani_demo", "knowledge")
COMPANY = "Sanjeevani Holdings"
AGENT_NAME = "Sanjeevani patient care"

# The demo directory: the people a demo prescription can name. Dates of birth are made up; say them when the agent asks.
DEMO_DIRECTORY = [
    {"username": "aadi.haldar", "first_name": "Aadi", "last_name": "Haldar", "phone_e164": "+919000000001", "identity_dob": "1995-08-15"},
    {"username": "aarit.haldar", "first_name": "Aarit", "last_name": "Haldar", "phone_e164": "+919000000002", "identity_dob": "1996-01-26"},
]

PATIENT_CARE_TOOLS = ["confirm_identity", "get_prescription_context", "look_up_care_information", "record_understanding",
                      "record_feedback", "record_visit_feedback", "request_doctor_meeting", "schedule_callback", "mark_do_not_call"]
APPOINTMENT_TOOLS = ["find_doctors", "quote_visit_cost", "book_doctor_appointment"]
PHARMACY_TOOLS = ["start_medicine_order", "send_prescription_link", "check_prescription_upload", "confirm_order_details",
                  "send_payment_link", "check_payment", "schedule_delivery"]
FEEDBACK_TOOLS = ["confirm_identity", "check_feedback_eligibility", "record_visit_feedback", "request_doctor_meeting", "mark_do_not_call"]

OPENING = ("Hello, this is the Sanjeevani Holdings patient care assistant, an automated assistant. This call may be recorded for quality and "
           "safety. Do you prefer English or Arabic?")

# Each desk reads only its own documents (matched by the number in the file name).
DESK_KBS = {"outbound": None,                                  # None: all of them (it uses the company-wide care look-up)
            "orchestrator": ("03",),
            "appointments": ("03", "05", "08"),
            "pharmacy": ("01", "03", "06", "07"),
            "feedback": ("03", "04")}


def docs_for(names, desk):
    wanted = DESK_KBS[desk]
    if wanted is None:
        return list(names)
    return [n for n in names if any(n.startswith(f"SANJEEVANI-KB-{w}_") for w in wanted)]

PROMPT = P.OUTBOUND          # kept under its old name



def call(method, url, headers, body=None, content_type=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", content_type or "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read()
            return res.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {"detail": raw[:200].decode("utf-8", "replace")}


def multipart(filename, data):
    boundary = "----sanjeevanisetup91c2"
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/markdown\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def need(status, out, what):
    if status >= 400:
        sys.exit(f"{what} failed (HTTP {status}): {out.get('detail', out)}")
    return out


def build_graph_definition(names, voice=""):
    """The steps and lines of the Sanjeevani agent: a front desk that hands an incoming caller to a desk, and the step that makes our calls."""
    def agent(id_, name, y, role, prompt, tools, desk, greeting="", voice=voice):
        return {"id": id_, "type": "agentNode", "position": {"x": 440, "y": y},
                "data": {"name": name, "role": role, "system_prompt": prompt, "voice": voice, "llm": "", "use_rag": True,
                         "kb_docs": docs_for(names, desk), "tools": tools, "greeting": greeting}}

    nodes = [
        {"id": "start", "type": "startNode", "position": {"x": 80, "y": 300}, "data": {"name": "Start"}},
        agent("front", "Front desk", 40, "orchestrator", P.ORCHESTRATOR, ["mark_do_not_call"], "orchestrator", OPENING),
        agent("appointments", "Appointment Desk", 200, "specialist", P.APPOINTMENTS, APPOINTMENT_TOOLS, "appointments"),
        agent("pharmacy", "Pharmacy Desk", 360, "specialist", P.PHARMACY, PHARMACY_TOOLS, "pharmacy"),
        agent("feedback", "Feedback Desk", 520, "specialist", P.FEEDBACK, FEEDBACK_TOOLS, "feedback"),
        agent("care", "Patient care assistant", 700, "outbound", PROMPT, PATIENT_CARE_TOOLS, "outbound"),
        {"id": "handover", "type": "toolNode", "position": {"x": 860, "y": 640},
         "data": {"name": "Hand over to a person", "toolName": "transfer_to_human"}},
        {"id": "end", "type": "endNode", "position": {"x": 860, "y": 780}, "data": {}},
    ]

    def edge(src, dst, label, cond=""):
        return {"id": f"e-{src}-{dst}", "source": src, "target": dst, "label": label, "condition": cond}

    edges = [
        edge("start", "front", "A patient phones us"),
        edge("start", "care", "We placed the call", "Used for the calls we place: the explanation after a prescription and the feedback calls."),
        edge("front", "appointments", "Book a doctor",
             "The caller wants to see a doctor, has symptoms, or wants to book, change or ask about an appointment."),
        edge("front", "pharmacy", "Order medicine", "The caller wants to order or reorder medicines, send a prescription, or ask about a delivery."),
        edge("front", "feedback", "Give feedback", "The caller wants to give feedback about a doctor visit, a medicine or their experience."),
        edge("appointments", "front", "Back to the front desk", "The caller needs something this desk cannot do, or wants to start again."),
        edge("appointments", "pharmacy", "Order medicine", "After booking, the caller also wants to order medicines."),
        edge("appointments", "feedback", "Give feedback", "The caller wants to give feedback about an earlier visit."),
        edge("pharmacy", "appointments", "Book a doctor", "The caller is unsure about a medicine, has new or worsening symptoms, or needs a doctor."),
        edge("pharmacy", "front", "Back to the front desk", "The caller needs something this desk cannot do."),
        edge("feedback", "appointments", "Book a doctor", "The caller is unhappy and wants a meeting, or is not yet eligible and wants to book."),
        edge("feedback", "front", "Back to the front desk", "The caller needs something this desk cannot do."),
        edge("front", "end", "Call finished", "The caller has no more questions."),
        edge("care", "handover", "Emergency or asks for a person", "The patient describes an emergency symptom, or asks to speak to a person."),
        edge("care", "end", "Call finished", "The call is complete or the patient wants to end it."),
    ]

    return nodes, edges


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--company", default=COMPANY)
    ap.add_argument("--handover", help="Sanjeevani's own number for hand-overs to a person, e.g. +971500000000")
    ap.add_argument("--patient-phone", help="adds one made-up demo patient reachable on this phone (use your own test phone)")
    ap.add_argument("--voice", default="", help="agent voice; empty uses the provider default")
    ap.add_argument("--demo-directory", action="store_true", help="add the demo patient directory (Aadi Haldar, Aarit Haldar)")
    ap.add_argument("--bucket-region", help="create the company's own bucket in this region, for example me-central1")
    ap.add_argument("--calling-hours", help="HH:MM-HH:MM, in the company time zone, for example 00:00-23:59 for a demo")
    args = ap.parse_args()
    api = args.api.rstrip("/")
    base = {"Authorization": f"Bearer {args.token}"}

    # 1. the company
    status, out = call("GET", f"{api}/api/tenants", base)
    need(status, out, "Listing companies")
    rows = out if isinstance(out, list) else out.get("orgs") or out.get("tenants") or []
    company = next((t for t in rows if (t.get("name") or "").lower() == args.company.lower()), None)
    if company is None:
        company = need(*call("POST", f"{api}/api/tenants", base, {"name": args.company}), "Creating the company")
        print(f"created   {args.company} (id {company['id']})")
    else:
        print(f"found     {args.company} (id {company['id']})")
    tid = company["id"]
    scoped = {**base, "X-Tenant-Id": str(tid)}
    need(*call("PUT", f"{api}/api/tenants/{tid}/timezone", base, {"timezone": "Asia/Dubai"}), "Setting the time zone")
    print("set       time zone Gulf (Asia/Dubai)")
    if args.handover:
        need(*call("PUT", f"{api}/api/tenants/{tid}/handover", base, {"number": args.handover}), "Setting the handover number")
        print(f"set       handover number {args.handover}")
    else:
        print("note      no --handover given: the agent will take a message instead of transferring")

    if args.calling_hours:
        start, _, end = args.calling_hours.partition("-")
        need(*call("PUT", f"{api}/api/tenants/{tid}/calling-hours", base, {"start": start, "end": end}), "Setting the calling hours")
        print(f"set       calling hours {start} to {end}")
    if args.bucket_region:
        need(*call("PUT", f"{api}/api/tenants/{tid}/bucket-region", base, {"region": args.bucket_region}), "Choosing the bucket region") \
            if not company.get("gcs_bucket") else None
        status, out = call("POST", f"{api}/api/tenants/{tid}/bucket", base)
        need(status, out, "Creating the bucket")
        st = out.get("storage") or {}
        print(f"bucket    {st.get('status')} {st.get('bucket') or ''} {st.get('location') or ''} {st.get('error') or ''}".rstrip())

    # 2. knowledge bases
    status, listing = call("GET", f"{api}/api/knowledge/documents", scoped)
    need(status, listing, "Reading the knowledge list")
    have = {d.get("name") for d in listing.get("documents", [])}
    names = []
    for path in sorted(glob.glob(os.path.join(KB_FOLDER, "SANJEEVANI-KB-*.md"))):
        name = os.path.basename(path)
        names.append(name)
        if name in have:
            print(f"present   {name}")
            continue
        with open(path, "rb") as f:
            body, ctype = multipart(name, f.read())
        status, out = call("POST", f"{api}/api/knowledge/upload", scoped, body, ctype)
        need(status, out, f"Uploading {name}")
        print(f"uploaded  {name}")
    if not names:
        sys.exit(f"No knowledge files found in {KB_FOLDER}")

    # 3. the agent
    status, existing = call("GET", f"{api}/api/workflows/", scoped)
    need(status, existing, "Reading the agents")
    mine = next((w for w in existing if w.get("name") == AGENT_NAME), None) if isinstance(existing, list) else None
    nodes, edges = build_graph_definition(names, args.voice)
    # Keep what an administrator already chose in the builder. A first save starts the agent on patient turn-taking:
    # people give dates and numbers slowly, and the default ends the caller's turn after a short pause.
    kept = {}
    if mine:
        full = need(*call("GET", f"{api}/api/workflows/{mine['id']}", scoped), "Reading the agent")
        kept = dict(full.get("settings") or {})
    kept.setdefault("gemini_live", {"turn_end": "patient", "turn_start": "sensitive", "transcript_languages": ["en-US", "ar-XA"]})
    payload = {"id": mine["id"] if mine else None, "name": AGENT_NAME, "is_active": True, "settings": kept,
               "description": "Answers patients who phone in (book a doctor, order medicine, give feedback) and makes the explanation and feedback calls.", "nodes": nodes, "edges": edges}
    saved = need(*call("POST", f"{api}/api/workflows/", scoped, payload), "Saving the agent")
    print(f"{'updated' if mine else 'created':9} agent '{AGENT_NAME}' (id {saved.get('id')}, live)")
    for w in saved.get("warnings") or []:
        print("warning  ", w)

    if args.demo_directory:
        for person in DEMO_DIRECTORY:
            need(*call("POST", f"{api}/api/sanjeevani/patients", scoped, person), f"Adding {person['first_name']}")
            print(f"directory {person['first_name']} {person['last_name']} ({person['username']})")

    # 4. a demo patient
    if args.patient_phone:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        record = [{
            "patient": {"external_id": "SNJ-DEMO-0001", "first_name": "Aarav", "guardian_name": "Rahul", "phone_e164": args.patient_phone,
                        "identity_dob": "2023-03-14", "preferred_language": "en"},
            "prescription": {"external_id": "RX-SNJ-DEMO-0001", "doctor_name": "Dr. Sonia", "facility": "Sanjeevani (demo)", "course_days": 5,
                             "extra_instruction": "Keep a temperature chart."},
            "items": [
                {"drug_name": "Crocin DS Syrup", "form": "syrup", "dose_text": "4 ml", "frequency_per_day": 2, "timing": "morning and evening",
                 "start_date": today, "duration_days": 5, "status": "confirmed",
                 "plain_summary_en": "Crocin is the syrup for the fever and any aches. Give 4 millilitres in the morning and 4 in the evening, using the syringe from the bottle.",
                 "plain_summary_ar": "كروسين شراب لخفض الحرارة وتخفيف الألم. أعطِ 4 مل صباحاً و4 مل مساءً باستخدام المحقنة المرفقة.",
                 "warnings": "Contains paracetamol. Do not give any other paracetamol medicine."},
                {"drug_name": "Meftal-P Syrup", "form": "syrup", "dose_text": "4 ml", "frequency_per_day": 2,
                 "timing": "morning and evening, after some food or milk", "start_date": today, "duration_days": 5, "status": "confirmed",
                 "plain_summary_en": "Meftal-P is the second syrup for pain and fever. Give 4 millilitres twice a day, after some food or milk.",
                 "plain_summary_ar": "ميفتال-بي شراب ثانٍ للألم والحرارة. أعطِ 4 مل مرتين يومياً بعد تناول بعض الطعام أو الحليب.",
                 "warnings": "Also contains paracetamol, like Crocin."},
                {"drug_name": "Ephodrex Syrup", "form": "syrup", "frequency_per_day": 2, "start_date": today, "duration_days": 5,
                 "status": "needs_clarification", "open_question": "The note shows 3.5 ml and 4 ml. Which is the intended dose?",
                 "plain_summary_en": "The third syrup is Ephodrex, for the cough and chest symptoms. I want to be careful with this one, because the dose on the note was not completely clear, so our pharmacist will confirm it with you.",
                 "plain_summary_ar": "الشراب الثالث هو إيفودريكس للسعال وأعراض الصدر. سأكون حذراً معه لأن الجرعة المكتوبة لم تكن واضحة تماماً، وسيؤكدها لك الصيدلي."},
            ],
        }]
        body, ctype = multipart_json("demo-prescription.json", json.dumps(record).encode())
        status, out = call("POST", f"{api}/api/sanjeevani/prescriptions/upload", scoped, body, ctype)
        need(status, out, "Adding the demo prescription")
        print("added     demo patient Aarav and prescription:", json.dumps(out.get("results")))
    print("\nDone. Open the console, choose the company, and look under Records.")


def multipart_json(filename, data):
    boundary = "----sanjeevanisetup77aa"
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: application/json\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


if __name__ == "__main__":
    main()
