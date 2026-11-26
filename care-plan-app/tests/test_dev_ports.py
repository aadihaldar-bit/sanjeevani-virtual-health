"""What was brought in from dev (Sonika's work): JSON repair, PDF parts, the Sanjeevani branches, stored patient documents and the
multi-file care plan call, checked together with this branch's audit log and limits."""
import io
import json

import pytest
from fastapi.testclient import TestClient

import db
import main
import store
from ai_brain import reasoner
import pharmacy_finder


# ---------------------------------------------------------------- JSON repair
def test_json_cut_off_mid_answer_is_closed_and_read():
    cut = '{"clinical_summary": {"primary_condition": "Fever", "medications": [{"drug_name": "Crocin", "dose_text": "5 ml"'
    out = reasoner.repair_and_parse_json(cut)
    assert out["clinical_summary"]["medications"][0]["drug_name"] == "Crocin"


def test_fences_and_trailing_commas_are_tolerated():
    assert reasoner.repair_and_parse_json('```json\n{"a": [1, 2,], "b": "x"}\n```') == {"a": [1, 2], "b": "x"}


def test_text_with_no_json_is_an_error():
    with pytest.raises(ValueError):
        reasoner.repair_and_parse_json("sorry, I cannot help")


# ---------------------------------------------------------------- PDFs and images as model input
def test_a_pdf_becomes_a_pdf_part():
    part = reasoner.prepare_multimodal_part(b"%PDF-1.4 fake", "application/pdf")
    assert part.inline_data.mime_type == "application/pdf"


def test_an_image_becomes_an_image():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(buf, "PNG")
    assert reasoner.prepare_multimodal_part(buf.getvalue(), "image/png").size == (10, 10)


# ---------------------------------------------------------------- Sanjeevani pharmacy branches
def test_branches_are_ranked_nearest_first():
    out = pharmacy_finder.recommend_sanjeevani_branches(24.49537, 54.40524)       # Al Reem Island
    distances = [b["distance_m"] for b in out]
    assert distances == sorted(distances) and out[0]["is_nearest"] and len(out) >= 5


# ---------------------------------------------------------------- stored documents
@pytest.fixture(autouse=True)
def records_in_a_temp_folder(tmp_path, monkeypatch):
    """No test in this file may touch the real patient_records folder."""
    monkeypatch.setattr(db, "PATIENT_RECORDS_BASE_DIR", str(tmp_path / "patient_records"))
    monkeypatch.setattr(main, "PATIENT_RECORDS_BASE_DIR", str(tmp_path / "patient_records"))


@pytest.fixture
def web(monkeypatch):
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    return TestClient(main.app)


def put_doc(patient, name, content=b"scan bytes"):
    return db.save_patient_document_dedup(patient, name, content)


def test_a_stored_document_can_be_listed_viewed_and_deleted(web):
    put_doc("SNJ-1", "rx1.png")
    assert web.get("/api/v1/patient-records/SNJ-1/documents").json()["count"] == 1
    r = web.get("/api/v1/patient-records/SNJ-1/documents/rx1.png")
    assert r.status_code == 200 and r.content == b"scan bytes"
    assert web.delete("/api/v1/patient-records/SNJ-1/documents/rx1.png").status_code == 200
    assert web.get("/api/v1/patient-records/SNJ-1/documents/rx1.png").status_code == 404


def test_identical_files_are_stored_once():
    assert put_doc("SNJ-1", "a.png")["is_duplicate"] is False
    assert put_doc("SNJ-1", "copy-of-a.png")["is_duplicate"] is True


@pytest.mark.parametrize("name", ["..%2F..%2Fetc%2Fpasswd", "..%5C..%5Cwindows%5Cwin.ini", "%2E%2E%2Fsecret.txt"])
def test_a_document_path_cannot_leave_the_patients_folder(web, tmp_path, name):
    (tmp_path / "secret.txt").write_text("top secret")
    put_doc("SNJ-1", "rx1.png")
    r = web.get(f"/api/v1/patient-records/SNJ-1/documents/{name}")
    assert r.status_code == 404 and "top secret" not in r.text


def test_a_patient_id_cannot_point_at_another_folder(web):
    put_doc("SNJ-2", "theirs.png")
    r = web.get("/api/v1/patient-records/..%2FBUR-2/documents/theirs.png")
    assert r.status_code == 404


def test_viewing_and_deleting_documents_is_audited_without_file_names(web):
    put_doc("SNJ-1", "private-name.png")
    web.get("/api/v1/patient-records/SNJ-1/documents/private-name.png")
    web.delete("/api/v1/patient-records/SNJ-1/documents/private-name.png")
    log = store.list_audit()
    assert {"document.viewed", "document.deleted"} <= {e["action"] for e in log}
    assert "private-name" not in json.dumps(log)


# ---------------------------------------------------------------- the multi-file care plan call
@pytest.fixture
def trigger(web, monkeypatch):
    seen = {}

    def fake_analyze(**kw):
        seen.update(kw)
        return {"clinical_summary": {"medications": []}, "patient_history": {}}

    async def no_push(*a, **k):
        return None
    monkeypatch.setattr(main, "analyze_prescription_and_plan_journey", fake_analyze)
    monkeypatch.setattr(main, "push_prescription_to_voicebot", no_push)

    def call(files, data=None, **kw):
        form = {"mobile_number": "+10000000000", "identity_dob": "2020-01-01", "patient_id": "SNJ-9", **(data or {})}
        return web.post("/api/v1/journey/prescription-trigger", data=form, files=files), seen
    return call


def test_several_prescription_files_reach_the_model_once_each(trigger):
    r, seen = trigger([("prescription_files", ("a.png", b"AAA", "image/png")), ("prescription_files", ("b.png", b"BBB", "image/png")),
                       ("prescription_files", ("a2.png", b"AAA", "image/png"))])
    assert r.status_code == 200
    assert seen["prescription_images"] == [b"AAA", b"BBB"]                          # the repeated file counted once
    assert db.scan_local_patient_documents("SNJ-9") and len(db.scan_local_patient_documents("SNJ-9")) == 2


def test_stored_documents_are_offered_as_history_up_to_the_limit(trigger, monkeypatch):
    for i in range(9):
        put_doc("SNJ-9", f"old{i}.png", f"old-{i}".encode())
    monkeypatch.setattr(main, "MAX_HISTORY_DOCS", 4)
    r, seen = trigger([("prescription_files", ("new.png", b"NEW", "image/png"))])
    assert r.status_code == 200 and len(seen["history_images"]) == 4


def test_only_the_chosen_stored_documents_are_used_as_history(trigger):
    put_doc("SNJ-9", "keep.png", b"KEEP")
    put_doc("SNJ-9", "skip.png", b"SKIP")
    r, seen = trigger([("prescription_files", ("new.png", b"NEW", "image/png"))], {"included_history_documents": json.dumps(["keep.png"])})
    assert seen["history_images"] == [b"KEEP"]


def test_a_text_only_prescription_still_works(trigger):
    r, seen = trigger([], {"prescription_text": "Crocin 5 ml for 3 days"})
    assert r.status_code == 200 and seen["prescription_images"] == []


def test_no_prescription_at_all_is_refused(trigger):
    r, _ = trigger([])
    assert r.status_code == 400
