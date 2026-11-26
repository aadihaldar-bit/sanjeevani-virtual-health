"""The Sanjeevani doctor directory, symptom matching, slots and insurance sums."""
from datetime import datetime, timedelta

from app import sanjeevani_care as care

# Sunday 25 October 2026, 06:00 UTC = 10:00 in Dubai
NOW = datetime(2026, 10, 25, 6, 0)


def test_symptoms_go_to_the_right_specialty():
    assert care.match_specialty("I have chest pain and I am short of breath")[0] == "Cardiology"
    assert care.match_specialty("a skin rash that itches")[0] == "Dermatology"
    assert care.match_specialty("my knee hurts after a fall")[0] == "Orthopedics"
    assert care.match_specialty("toothache since yesterday")[0] == "Dentistry"


def test_a_child_goes_to_pediatrics_whatever_the_symptom():
    assert care.match_specialty("my 3 year old has a fever")[0] == "Pediatrics"
    assert care.match_specialty("my daughter has a rash")[0] == "Pediatrics"


def test_something_unrecognised_goes_to_general_medicine():
    assert care.match_specialty("I just feel strange")[0] == "General Medicine"


def test_senior_and_standard_doctors_are_told_apart():
    both = care.doctors_for("Cardiology")
    assert [d["seniority"] for d in both] == ["senior", "standard"]
    assert [d["name"] for d in care.doctors_for("Cardiology", "senior")] == ["Dr. Faisal Al Mansoori"]
    assert [d["name"] for d in care.doctors_for("Cardiology", "standard")] == ["Dr. Mariam Khoury"]


def test_a_doctor_is_found_by_name_however_it_is_said():
    assert care.find_doctor("Dr. Sonia")["name"] == "Dr. Sonia"
    assert care.find_doctor("sonia")["name"] == "Dr. Sonia"
    assert care.find_doctor("Dr Karim Haddad")["id"] == "BJ-03"
    assert care.find_doctor("nobody") is None


def test_slots_are_inside_hours_in_the_future_and_on_different_days():
    doc = care.find_doctor("Dr. Karim Haddad")        # Sun-Thu 09:00-13:00 Dubai
    slots = care.next_slots(doc, NOW, count=3)
    assert len(slots) == 3
    assert len({s.key[:10] for s in slots}) == 3        # three different days
    for s in slots:
        local = s.start_utc + timedelta(hours=4)
        assert 9 <= local.hour < 13 and local.minute in (0, 30)
        assert s.start_utc > NOW
    # it is 10:00 in Dubai on Sunday and the lead time is 2 hours, so today's first offer is 12:00 or later
    assert slots[0].key == "2026-10-25T12:00"
    assert slots[0].label == "Sunday 25 October at 12:00 PM"


def test_a_booked_slot_is_not_offered_again():
    doc = care.find_doctor("Dr. Karim Haddad")
    first = care.next_slots(doc, NOW, count=1)[0]
    again = care.next_slots(doc, NOW, taken={first.start_utc}, count=1, spread=False)[0]
    assert again.key != first.key


def test_a_chosen_slot_must_be_one_of_the_doctors_free_slots():
    doc = care.find_doctor("Dr. Karim Haddad")
    good = care.next_slots(doc, NOW, count=1)[0]
    assert care.slot_from_key(doc, good.key, NOW).label == good.label
    assert care.slot_from_key(doc, "2026-10-25T03:00", NOW) is None            # outside hours
    assert care.slot_from_key(doc, "2026-10-24T10:00", NOW) is None            # in the past
    assert care.slot_from_key(doc, "2026-10-30T10:00", NOW) is None            # a Friday: no clinic
    assert care.slot_from_key(doc, "2026-10-26T10:10", NOW) is None            # not on the half hour
    assert care.slot_from_key(doc, "tomorrow morning", NOW) is None
    assert care.slot_from_key(doc, good.key, NOW, taken={good.start_utc}) is None


def test_insurance_pays_its_share_and_the_patient_the_rest():
    q = care.quote(500, "Daman")
    assert q["accepted"] and q["covered"] == 400 and q["payable"] == 100
    assert care.quote(500, "daman")["insurer"] == "Daman"


def test_no_insurance_or_an_unknown_insurer_is_never_guessed_at():
    assert care.quote(500, "")["payable"] == 500
    assert care.quote(500, "no insurance")["payable"] == 500
    q = care.quote(500, "Made Up Health")
    assert q["accepted"] is False and q["payable"] == 500 and q["covered"] == 0


def test_money_is_said_in_dirhams():
    assert care.money(450) == "450 dirhams"
    assert care.money(99.6) == "100 dirhams"


def test_a_medicine_is_priced_only_if_the_form_and_strength_match_the_list():
    assert care.catalogue_entry("Paracetamol", "tablet", "500 mg")["price"] == 8
    assert care.catalogue_entry("Tab. Paracetamol 500 mg")["price"] == 8
    assert care.catalogue_entry("Crocin DS Syrup", "syrup", "250 mg/5 ml")["price"] == 18
    assert care.catalogue_entry("Cetirizine", "syrup", "5 mg/5 ml") is None            # the list has the tablet, not the syrup
    assert care.catalogue_entry("Cetirizine", "tablet", "10 mg")["price"] == 12
    assert care.catalogue_entry("Paracetamol", "tablet", "650 mg") is None             # a different strength is a different product
    assert care.catalogue_entry("Ibuprofen", "gel") is None
    assert care.catalogue_entry("Zorvix Forte") is None


def test_an_eye_complaint_goes_to_an_eye_specialist_and_a_hip_complaint_does_not():
    for said in ("I have eye pain", "my eyes are red and itchy", "I have blurred vision", "a stye on my eyelid", "headache and eye pain"):
        assert care.match_specialty(said)[0] == "Ophthalmology", said
    assert care.match_specialty("I have hip pain")[0] == "Orthopedics"
    assert [d["name"] for d in care.doctors_for("Ophthalmology", "senior")] == ["Dr. Hessa Al Suwaidi"]
    assert [d["name"] for d in care.doctors_for("Ophthalmology", "standard")] == ["Dr. Bilal Karimi"]
