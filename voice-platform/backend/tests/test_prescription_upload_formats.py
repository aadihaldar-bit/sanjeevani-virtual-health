"""A prescription sent through the upload link: a digital PDF is read from its text, a scanned PDF or a photo is read from the picture."""
import pytest

from app import prescription_pdf as pdf

READ = {"patient": {}, "prescription": {}, "items": [{"drug_name": "Crocin DS Syrup", "dose_text": "4 ml", "status": "confirmed"}]}
PDF = b"%PDF-1.4 " + b"x" * 200


@pytest.fixture
def seen(monkeypatch):
    calls = []

    def fake_fields(text, file=None):
        calls.append((text, file))
        return READ

    monkeypatch.setattr(pdf, "extract_prescription_fields", fake_fields)
    return calls


def test_a_digital_pdf_is_read_from_its_text_not_from_a_picture(seen, monkeypatch):
    monkeypatch.setattr(pdf, "extract_pdf_text", lambda data: "Crocin DS syrup 4 ml twice daily for 5 days")
    assert pdf.extract_prescription_from_file(PDF, "application/pdf") == READ
    assert seen == [("Crocin DS syrup 4 ml twice daily for 5 days", None)]


def test_a_pdf_is_recognised_by_what_it_is_whatever_the_browser_called_it(seen, monkeypatch):
    monkeypatch.setattr(pdf, "extract_pdf_text", lambda data: "text of the prescription")
    pdf.extract_prescription_from_file(PDF, "image/jpeg")                    # mislabelled
    pdf.extract_prescription_from_file(PDF, "")                              # unlabelled
    assert [c[1] for c in seen] == [None, None]


def test_a_scanned_pdf_falls_back_to_reading_the_pages(seen, monkeypatch):
    def scanned(data):
        raise pdf.PdfParseError("No readable text was found. This looks like a scanned prescription")

    monkeypatch.setattr(pdf, "extract_pdf_text", scanned)
    pdf.extract_prescription_from_file(PDF, "application/pdf")
    assert seen[0][1] == ("application/pdf", PDF)


def test_a_damaged_or_locked_pdf_is_refused_not_guessed_at(seen, monkeypatch):
    def locked(data):
        raise pdf.PdfParseError("The PDF is password-protected.")

    monkeypatch.setattr(pdf, "extract_pdf_text", locked)
    with pytest.raises(pdf.PdfParseError, match="password"):
        pdf.extract_prescription_from_file(PDF, "application/pdf")
    assert seen == []


def test_a_photo_is_read_from_the_picture(seen):
    jpg = b"\xff\xd8\xff" + b"0" * 50
    pdf.extract_prescription_from_file(jpg, "image/jpeg")
    assert seen[0][1] == ("image/jpeg", jpg)


def test_something_that_is_neither_a_pdf_nor_a_photo_is_refused(seen):
    with pytest.raises(pdf.PdfParseError, match="PDF"):
        pdf.extract_prescription_from_file(b"just some text", "text/plain")
    with pytest.raises(pdf.PdfParseError):
        pdf.extract_prescription_from_file(b"", "application/pdf")
