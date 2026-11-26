"""The demo prescription PDFs: each is by a doctor in the directory, has a readable text layer with every medicine, and prices as intended."""
import importlib.util
import os

import pytest

from app import sanjeevani_care as care
from app.prescription_pdf import extract_pdf_text

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PDF_DIR = os.path.join(ROOT, "docs", "sanjeevani_demo", "demo-prescriptions", "pdf")


def _generator():
    spec = importlib.util.spec_from_file_location("make_demo_prescriptions", os.path.join(ROOT, "scripts", "make_demo_prescriptions.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GEN = _generator()
# which medicines the pharmacy list prices (the rest are priced by the pharmacist)
PRICED = {
    "rx-1-aadi-fever-and-sore-throat": [True, True, True],
    "rx-2-aarit-lower-back-pain": [True, True, True, False],
    "rx-3-aarav-child-fever": [True, False, True],
    "rx-4-aadi-eczema-flare": [False, True, False],
    "rx-5-aarit-tonsillitis": [True, True, False],
}


def test_there_are_five_prescriptions_and_each_has_its_pdf():
    assert len(GEN.PRESCRIPTIONS) == 5
    for rx in GEN.PRESCRIPTIONS:
        assert os.path.exists(os.path.join(PDF_DIR, rx["file"] + ".pdf")), f"run scripts/make_demo_prescriptions.py: {rx['file']} is missing"


@pytest.mark.parametrize("rx", GEN.PRESCRIPTIONS, ids=lambda r: r["file"])
def test_the_doctor_is_in_the_directory_and_treats_this_kind_of_problem(rx):
    name, _, _, department, _ = GEN.DOCTORS[rx["doctor"]]
    doc = care.find_doctor(name)
    assert doc is not None and doc["specialty"] == department, f"{name} is not a {department} in the directory"
    # and a caller describing it in their own words is sent to this doctor's specialty
    assert care.match_specialty(rx["said"])[0] == department, rx["said"]
    assert doc in care.doctors_for(department)


@pytest.mark.parametrize("rx", GEN.PRESCRIPTIONS, ids=lambda r: r["file"])
def test_the_text_layer_names_the_doctor_the_patient_and_every_medicine_with_its_dose(rx):
    text = extract_pdf_text(open(os.path.join(PDF_DIR, rx["file"] + ".pdf"), "rb").read())
    assert GEN.DOCTORS[rx["doctor"]][0] in text and GEN.PATIENTS[rx["patient"]]["name"] in text and rx["no"] in text
    for head, dose, freq, duration, _ in rx["medicines"]:
        assert head in text and dose in text and duration in text
    assert "DEMO DOCUMENT" in text                                         # never mistaken for a real prescription


@pytest.mark.parametrize("rx", GEN.PRESCRIPTIONS, ids=lambda r: r["file"])
def test_each_medicine_is_priced_only_if_the_pharmacy_list_really_has_it(rx):
    got = [care.catalogue_entry(head) is not None for head, *_ in rx["medicines"]]
    assert got == PRICED[rx["file"]]
