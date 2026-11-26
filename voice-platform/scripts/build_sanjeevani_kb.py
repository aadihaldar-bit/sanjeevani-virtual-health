"""Write the two Sanjeevani knowledge bases that mirror the booking tool's data, so what the agent says and what it books cannot disagree.

    python scripts/build_sanjeevani_kb.py

  docs/sanjeevani_demo/knowledge/SANJEEVANI-KB-05_Doctors_and_Appointments.md   doctors, fees, hours, insurers, booking rules
  docs/sanjeevani_demo/knowledge/SANJEEVANI-KB-08_Symptom_and_Specialty_Guide.md  what each specialty treats, what to ask, emergency signs

Both come from backend/app/data/sanjeevani_care.json. Edit that file (not these) and run this again.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "backend", "app", "data", "sanjeevani_care.json")
OUT = os.path.join(ROOT, "docs", "sanjeevani_demo", "knowledge")

EMERGENCY = {
    "General Medicine": "a fever above 39.5 C that will not come down, confusion, severe dehydration, or difficulty breathing",
    "Pediatrics": "a baby under 3 months with a fever, a child who is very sleepy or hard to wake, difficulty breathing, a convulsion, or no urine for 8 hours",
    "Cardiology": "chest pain with sweating, pain spreading to the arm or jaw, fainting, or severe breathlessness",
    "Dermatology": "a rash with fever, swelling of the lips or face, or blisters over a large area of skin",
    "Orthopedics": "an obvious deformity, a bone showing through the skin, being unable to put weight on the leg after a fall, or numbness in the limb",
    "ENT": "sudden loss of hearing, difficulty breathing or swallowing, or a heavy nosebleed that will not stop",
    "Neurology": "sudden weakness on one side, slurred speech, the worst headache of their life, or a seizure",
    "Gastroenterology": "vomiting blood, black stools, severe constant stomach pain, or yellow eyes with fever",
    "Ophthalmology": "sudden loss or sharp drop in vision, a chemical splash in the eye, an injury or something stuck in the eye, or severe eye pain with a headache or nausea",
    "Dentistry": "swelling of the face that spreads toward the eye or neck, trouble swallowing or breathing, or bleeding that will not stop",
}


def day_names(rules):
    return "; ".join(rules)


def doctors_kb(d):
    cur = d["currency"]
    lines = [
        "# Sanjeevani Doctors, Fees and Appointments", "",
        "DEMO VALUES. The doctors, fees and hours below are made up for the recording. Replace them with Sanjeevani's real directory "
        "(edit backend/app/data/sanjeevani_care.json and run scripts/build_sanjeevani_kb.py) before any real patient call.", "",
        "## How booking works",
        "The booking tools are the only source of doctors, fees and free times. Find the right specialty from the symptoms, offer a senior "
        "consultant or a standard doctor, offer two free times, ask about insurance, say what the caller pays, read it back, and book. "
        "Never say a time or a reference number that a tool did not give. Payment is made at the visit. Ask the patient to arrive ten "
        "minutes early with their Emirates ID and insurance card.", "",
        "## Senior consultant or standard doctor",
        "A senior consultant has more years of experience and a higher fee. A standard doctor (a specialist, general practitioner or "
        "family physician) sees the same kinds of problems at a lower fee. Both are fully qualified. Let the caller choose.", "",
        "## Insurance",
        "Accepted insurers and the share of the fee they pay (demo values): " +
        ", ".join(f"{k} {int(v * 100)} percent" for k, v in d["insurers"].items()) +
        ". The patient pays the rest at the visit. An insurer that is not on this list is not guessed at: the full fee is payable. "
        "Insurance details are checked at the front desk on arrival.", "",
        "## Cancelling or changing",
        "A visit can be moved or cancelled up to two hours before the time. For a change, say the clinic team will confirm it, "
        "and do not promise a new time that the booking tool has not given.", "",
        "## The doctors",
    ]
    by_spec = {}
    for doc in d["doctors"]:
        by_spec.setdefault(doc["specialty"], []).append(doc)
    for spec, docs in by_spec.items():
        lines.append(f"### {spec}")
        for doc in docs:
            lines.append(f"- {doc['name']}, {doc['title']} ({'senior consultant' if doc['seniority'] == 'senior' else 'standard doctor'}), "
                         f"{doc['experience_years']} years of experience. Fee {doc['fee']} {cur}. Hours: {day_names(doc['availability'])} (Gulf time).")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def guide_kb(d):
    lines = [
        "# Sanjeevani Symptom and Specialty Guide", "",
        "DEMO CONTENT, for routing a caller to the right kind of doctor. It is not medical advice, and the agent never gives medical advice. "
        "The agent listens with sympathy, asks the related questions below one at a time, and then uses the doctor search.", "",
        "## How to talk about it",
        "Say one short sentence of real sympathy (for example, \"I'm sorry you're not feeling well\"). Do not say what the problem is, do not "
        "reassure that it is nothing, and do not suggest any medicine. Ask two or three of the questions below, one per turn, then search for a doctor.", "",
        "## Emergencies come first",
        "If any emergency sign below is mentioned, stop the booking: tell the caller to call 998 or go to the nearest emergency department "
        "now, and offer to stay on the line.", "",
    ]
    for spec, info in d["specialties"].items():
        lines.append(f"## {spec}")
        lines.append("What callers describe: " + ", ".join(info["keywords"]) + ".")
        lines.append("Ask about:")
        lines += [f"- {q}" for q in info["ask_about"]]
        lines.append(f"Emergency signs (call 998): {EMERGENCY.get(spec, 'any sudden severe symptom')}.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main():
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    for name, text in (("SANJEEVANI-KB-05_Doctors_and_Appointments.md", doctors_kb(data)),
                       ("SANJEEVANI-KB-08_Symptom_and_Specialty_Guide.md", guide_kb(data))):
        with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("wrote", name)


if __name__ == "__main__":
    main()
