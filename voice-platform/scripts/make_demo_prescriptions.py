"""Make the demo prescriptions: digital PDFs laid out the way an outpatient prescription is, each by a doctor from the Sanjeevani directory
(backend/app/data/sanjeevani_care.json) and each for one of the demo patients.

    python scripts/make_demo_prescriptions.py            # writes docs/sanjeevani_demo/demo-prescriptions/pdf/*.pdf

They have a real text layer, so the agent reads them the way it will read a hospital's PDF. The medicines are common ones with ordinary
doses, chosen to match the pharmacy list (so most are priced) and to show the cases that matter: one medicine the list does not have (the
pharmacist prices it), a syrup and a cream next to tablets, a course that must be finished (antibiotic), and "if needed" doses. Every page says
it is a demo document. The doses are ordinary textbook ones for the demo; a pharmacist should still review them before any real use.
"""
import argparse
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "sanjeevani_demo", "demo-prescriptions", "pdf")
NAVY = colors.HexColor("#14325c")
GREY = colors.HexColor("#5b6472")
RULE = colors.HexColor("#c9d1dc")

# name, qualifications, title, department, licence (made up)
DOCTORS = {
    "karim": ("Dr. Karim Haddad", "MBBS, MRCP (UK)", "Senior Consultant", "General Medicine", "DHA-P-0481726"),
    "samir": ("Dr. Samir Barakat", "MBBS, MS (Orthopaedics)", "Senior Consultant", "Orthopedics", "DHA-P-0392054"),
    "sonia": ("Dr. Sonia", "MBBS, MD (Paediatrics)", "Senior Consultant", "Pediatrics", "DHA-P-0517338"),
    "rana": ("Dr. Rana Saleh", "MBBS, MD (Dermatology)", "Senior Consultant", "Dermatology", "DHA-P-0446912"),
    "yusuf": ("Dr. Yusuf Qasim", "MBBS, MS (ENT)", "Senior Consultant", "ENT", "DHA-P-0429781"),
}

PATIENTS = {
    "aadi": {"name": "Aadi Haldar", "age": "31 years", "sex": "Male", "weight": "74 kg", "mrn": "SNJ-100231", "mobile": "+91 90000 00001"},
    "aarit": {"name": "Aarit Haldar", "age": "30 years", "sex": "Male", "weight": "78 kg", "mrn": "SNJ-100232", "mobile": "+91 90000 00002"},
    "aarav": {"name": "Aarav Haldar", "age": "3 years", "sex": "Male", "weight": "13 kg", "mrn": "SNJ-100233", "mobile": "+91 90000 00001",
              "guardian": "Aadi Haldar (father)"},
}

# Each medicine: (heading, dose, frequency, duration, instructions)
PRESCRIPTIONS = [
    {
        "file": "rx-1-aadi-fever-and-sore-throat", "said": "I have had a fever and a sore throat for two days", "doctor": "karim", "patient": "aadi", "no": "RX-SNJ-2026-04711",
        "visit": "Outpatient consultation", "allergies": "No known drug allergies",
        "vitals": "Temp 38.4 C   Pulse 92/min   BP 118/76 mmHg   SpO2 98 percent",
        "diagnosis": "Acute upper respiratory tract infection (viral), with fever and sore throat",
        "medicines": [
            ("Tab. PARACETAMOL 500 mg (Panadol)", "1 tablet", "every 6 hours if fever or pain, maximum 4 tablets in 24 hours", "3 days", "After food, with water"),
            ("Tab. CETIRIZINE 10 mg (Zyrtec)", "1 tablet", "once daily at night", "5 days", "May cause drowsiness; avoid driving"),
            ("Oral Rehydration Salts (ORS) sachet", "1 sachet in 200 ml of clean water", "after every loose stool or if feeling dehydrated", "as needed for 3 days", "Use the prepared solution within 24 hours"),
        ],
        "advice": ["Rest and drink plenty of fluids.", "Warm saline gargles 3 to 4 times a day.", "Avoid cold drinks and ice cream until the throat settles."],
        "follow_up": "Review in 5 days, or earlier if the fever lasts beyond 3 days, breathing becomes difficult, or you cannot drink.",
    },
    {
        "file": "rx-2-aarit-lower-back-pain", "said": "I have a lower back pain since I lifted something heavy", "doctor": "samir", "patient": "aarit", "no": "RX-SNJ-2026-04712",
        "visit": "Outpatient consultation", "allergies": "No known drug allergies",
        "vitals": "BP 124/80 mmHg   Pulse 78/min   Weight 78 kg",
        "diagnosis": "Acute mechanical lower back pain (lumbar strain); no neurological deficit",
        "medicines": [
            ("Tab. IBUPROFEN 400 mg (Brufen)", "1 tablet", "twice daily", "5 days", "Always after food; stop and call if stomach pain or black stools"),
            ("Cap. OMEPRAZOLE 20 mg (Losec)", "1 capsule", "once daily, 30 minutes before breakfast", "5 days", "Protects the stomach while taking ibuprofen"),
            ("Tab. PARACETAMOL 500 mg (Panadol)", "1 tablet", "every 6 hours only if pain is not controlled, maximum 4 tablets in 24 hours", "5 days", "After food"),
            ("Diclofenac 1 percent gel (Voltaren Emulgel)", "a 2 cm strip of gel", "3 times a day on the painful area of the lower back", "7 days", "Rub in gently; do not apply on broken skin"),
        ],
        "advice": ["Stay active; avoid prolonged bed rest.", "Hot compress for 15 minutes, twice a day.", "Avoid lifting heavy weights and sudden twisting.", "Physiotherapy referral given."],
        "follow_up": "Review in 2 weeks. Come sooner if there is leg weakness, numbness, or difficulty passing urine.",
    },
    {
        "file": "rx-3-aarav-child-fever", "said": "my three year old son has a fever and a runny nose", "doctor": "sonia", "patient": "aarav", "no": "RX-SNJ-2026-04713",
        "visit": "Paediatric consultation", "allergies": "No known drug allergies",
        "vitals": "Temp 38.9 C   Weight 13 kg   Pulse 118/min   Resp 26/min",
        "diagnosis": "Viral fever with runny nose in a 3 year old; hydration adequate",
        "medicines": [
            ("Syp. PARACETAMOL 250 mg/5 ml (Crocin DS)", "4 ml", "every 8 hours if fever, maximum 3 doses in 24 hours", "3 days", "Use the measuring syringe in the pack; never give any other medicine containing paracetamol"),
            ("Syp. CETIRIZINE 5 mg/5 ml", "2.5 ml", "once daily at bedtime", "5 days", "For the runny nose; may cause sleepiness"),
            ("Oral Rehydration Salts (ORS) sachet", "1 sachet in 200 ml of clean water", "small sips often, and after every loose stool", "as needed for 3 days", "Use the prepared solution within 24 hours"),
        ],
        "advice": ["Offer plenty of fluids and light food.", "Keep a temperature chart, morning and evening.", "Sponge with lukewarm water if the fever is high; do not use cold water.", "Keep home from nursery until fever-free for 24 hours."],
        "follow_up": "Review in 3 days. Come immediately if the child is very sleepy or hard to wake, is breathing fast, has a fit, or passes no urine for 8 hours.",
    },
    {
        "file": "rx-4-aadi-eczema-flare", "said": "my eczema is back and both my arms are itching", "doctor": "rana", "patient": "aadi", "no": "RX-SNJ-2026-04714",
        "visit": "Outpatient consultation", "allergies": "No known drug allergies",
        "vitals": "BP 120/78 mmHg   Weight 74 kg",
        "diagnosis": "Atopic dermatitis (eczema), flare of both forearms",
        "medicines": [
            ("Betamethasone valerate 0.1 percent cream", "a thin layer (about one fingertip unit per hand-sized area)", "twice daily on the affected skin", "7 days", "Not on the face, neck or skin folds; wash hands after use"),
            ("Tab. CETIRIZINE 10 mg (Zyrtec)", "1 tablet", "once daily at night", "7 days", "For itching; may cause drowsiness"),
            ("Emollient lotion (fragrance-free)", "apply liberally", "at least twice daily, and straight after a bath", "ongoing", "Apply on the whole body, even when the skin is clear"),
        ],
        "advice": ["Short lukewarm showers; use a soap-free wash.", "Cotton clothing; avoid wool next to the skin.", "Keep nails short; avoid scratching.", "Note any food or product that seems to trigger the rash."],
        "follow_up": "Review in 2 weeks. Stop the steroid cream when the skin is clear and continue the emollient.",
    },
    {
        "file": "rx-5-aarit-tonsillitis", "said": "I have a painful throat with swollen tonsils and a fever", "doctor": "yusuf", "patient": "aarit", "no": "RX-SNJ-2026-04715",
        "visit": "Outpatient consultation", "allergies": "No known drug allergies (penicillin tolerated before)",
        "vitals": "Temp 38.7 C   Pulse 96/min   BP 122/78 mmHg",
        "diagnosis": "Acute bacterial tonsillitis",
        "medicines": [
            ("Cap. AMOXICILLIN 500 mg", "1 capsule", "three times a day, every 8 hours", "7 days", "Complete the full course even if you feel better; take after food"),
            ("Tab. PARACETAMOL 500 mg (Panadol)", "1 tablet", "every 6 hours if fever or throat pain, maximum 4 tablets in 24 hours", "3 days", "After food"),
            ("Throat lozenges (Strepsils)", "1 lozenge", "every 3 hours if needed, maximum 8 a day", "3 days", "Allow to dissolve slowly in the mouth"),
        ],
        "advice": ["Warm saline gargles 3 to 4 times a day.", "Soft, warm foods; plenty of fluids.", "Rest your voice and avoid smoking."],
        "follow_up": "Review in 1 week. Come sooner if you cannot swallow, drool, or have difficulty opening the mouth or breathing.",
    },
]


def wrap(c, text, font, size, width):
    words, line, out = text.split(), "", []
    for w in words:
        trial = (line + " " + w).strip()
        if c.stringWidth(trial, font, size) <= width:
            line = trial
        else:
            out.append(line)
            line = w
    if line:
        out.append(line)
    return out


def draw(path, rx, on):
    doc = DOCTORS[rx["doctor"]]
    pt = PATIENTS[rx["patient"]]
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle(f"Prescription {rx['no']}")
    c.setAuthor(doc[0])
    W, H = A4
    left, right = 18 * mm, W - 18 * mm
    y = H - 16 * mm

    # letterhead
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(left, y, "SANJEEVANI HOLDINGS")
    c.setFont("Helvetica", 9)
    c.setFillColor(GREY)
    c.drawString(left, y - 6 * mm, f"Outpatient Clinic  |  Department of {doc[3]}")
    c.drawRightString(right, y, "Demo Medical Centre, Sheikh Zayed Road, Dubai, UAE")
    c.drawRightString(right, y - 4.5 * mm, "Tel +971 4 000 0000   |   clinic@sanjeevani-demo.example")
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.6)
    c.line(left, y - 9 * mm, right, y - 9 * mm)
    y -= 16 * mm

    # doctor
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(left, y, f"{doc[0]}, {doc[1]}")
    c.setFont("Helvetica", 9.5)
    c.setFillColor(GREY)
    c.drawString(left, y - 5 * mm, f"{doc[2]}, {doc[3]}   |   DHA Licence No. {doc[4]}")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9.5)
    c.drawRightString(right, y, f"Prescription No: {rx['no']}")
    c.drawRightString(right, y - 5 * mm, f"Date: {on.strftime('%d %B %Y')}")
    y -= 12 * mm

    # patient box
    box_h = 27 * mm if pt.get("guardian") else 22 * mm
    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.roundRect(left, y - box_h, right - left, box_h, 2 * mm, stroke=1, fill=0)
    rows = [
        (f"Patient name: {pt['name']}", f"MRN: {pt['mrn']}"),
        (f"Age / Sex: {pt['age']} / {pt['sex']}", f"Weight: {pt['weight']}"),
        (f"Mobile: {pt['mobile']}", f"Visit type: {rx['visit']}"),
    ]
    if pt.get("guardian"):
        rows.append((f"Parent / guardian: {pt['guardian']}", ""))
    yy = y - 5.5 * mm
    for a, b in rows:
        c.setFont("Helvetica", 10)
        c.drawString(left + 4 * mm, yy, a)
        c.drawString(left + 105 * mm, yy, b)
        yy -= 5 * mm
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(left + 4 * mm, yy, f"Allergies: {rx['allergies']}")
    y -= box_h + 6 * mm

    # clinical
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Vitals:")
    c.setFont("Helvetica", 10)
    c.drawString(left + 14 * mm, y, rx["vitals"])
    y -= 6 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Diagnosis:")
    c.setFont("Helvetica", 10)
    for i, ln in enumerate(wrap(c, rx["diagnosis"], "Helvetica", 10, right - left - 20 * mm)):
        c.drawString(left + 20 * mm, y - i * 4.6 * mm, ln)
    y -= 10 * mm

    # Rx
    c.setFillColor(NAVY)
    c.setFont("Times-BoldItalic", 26)
    c.drawString(left, y - 4 * mm, "Rx")
    c.setFillColor(colors.black)
    y -= 12 * mm
    for n, (head, dose, freq, dur, note) in enumerate(rx["medicines"], 1):
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(left + 6 * mm, y, f"{n}.  {head}")
        y -= 5 * mm
        c.setFont("Helvetica", 9.8)
        for label, value in (("Dose", dose), ("Frequency", freq), ("Duration", dur), ("Instructions", note)):
            lines = wrap(c, value, "Helvetica", 9.8, right - left - 42 * mm)
            c.setFont("Helvetica-Bold", 9.2)
            c.drawString(left + 12 * mm, y, f"{label}:")
            c.setFont("Helvetica", 9.8)
            for i, ln in enumerate(lines):
                c.drawString(left + 36 * mm, y - i * 4.4 * mm, ln)
            y -= max(1, len(lines)) * 4.4 * mm
        y -= 3 * mm

    # advice and follow-up
    c.setStrokeColor(RULE)
    c.line(left, y + 1 * mm, right, y + 1 * mm)
    y -= 5 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Advice")
    y -= 5 * mm
    c.setFont("Helvetica", 9.8)
    for a in rx["advice"]:
        for i, ln in enumerate(wrap(c, a, "Helvetica", 9.8, right - left - 10 * mm)):
            c.drawString(left + 4 * mm, y, ("-  " if i == 0 else "   ") + ln)
            y -= 4.5 * mm
    y -= 2 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Follow-up")
    y -= 5 * mm
    c.setFont("Helvetica", 9.8)
    for ln in wrap(c, rx["follow_up"], "Helvetica", 9.8, right - left - 6 * mm):
        c.drawString(left + 4 * mm, y, ln)
        y -= 4.5 * mm

    # signature and stamp (kept clear of the text above; the generator refuses to draw over it)
    sy = 33 * mm
    if y < sy + 22 * mm:
        raise SystemExit(f"{rx['file']}: too much text, it would run into the signature (y={y / mm:.0f} mm)")
    c.setStrokeColor(colors.HexColor("#1f3b8a"))
    c.setLineWidth(1.3)
    p = c.beginPath()
    x0 = right - 62 * mm
    p.moveTo(x0, sy + 7 * mm)
    for dx, dy in ((8, 9), (14, -7), (21, 10), (27, -6), (34, 8), (44, -3), (56, 6)):
        p.curveTo(x0 + dx * mm - 4 * mm, sy + 7 * mm + dy * mm, x0 + dx * mm - 1 * mm, sy + 7 * mm + dy * mm, x0 + dx * mm, sy + 7 * mm)
    c.drawPath(p, stroke=1, fill=0)
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.6)
    c.line(right - 64 * mm, sy, right, sy)
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(colors.black)
    c.drawRightString(right, sy - 4.5 * mm, doc[0])
    c.setFont("Helvetica", 8.5)
    c.setFillColor(GREY)
    c.drawRightString(right, sy - 8.5 * mm, f"{doc[2]}, {doc[3]}  |  DHA Licence No. {doc[4]}")
    c.setFillColor(colors.HexColor("#8a1f1f"))
    c.setStrokeColor(colors.HexColor("#8a1f1f"))
    c.setLineWidth(1.1)
    cx, cy = left + 20 * mm, sy + 3 * mm
    c.circle(cx, cy, 14 * mm, stroke=1, fill=0)
    c.circle(cx, cy, 12.4 * mm, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 6.6)
    c.drawCentredString(cx, cy + 4.5 * mm, "SANJEEVANI CLINIC")
    c.drawCentredString(cx, cy - 0.5 * mm, "DUBAI")
    c.setFont("Helvetica", 6.2)
    c.drawCentredString(cx, cy - 5 * mm, on.strftime("%d.%m.%Y"))
    c.setFillColor(colors.black)

    # footer
    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(GREY)
    c.drawCentredString(W / 2, 14 * mm, "Valid for 30 days from the date of issue. Not valid for controlled drugs. Keep out of reach of children.")
    c.drawCentredString(W / 2, 10 * mm, "DEMO DOCUMENT created for testing the Sanjeevani voice assistant. It is not a real medical record or prescription.")
    c.showPage()
    c.save()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD printed on the prescriptions (default: today)")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    on = date.fromisoformat(a.date) if a.date else date.today()
    os.makedirs(a.out, exist_ok=True)
    for rx in PRESCRIPTIONS:
        path = os.path.join(a.out, rx["file"] + ".pdf")
        draw(path, rx, on)
        print("wrote", os.path.relpath(path, ROOT))


if __name__ == "__main__":
    main()
