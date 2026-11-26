"""
e-Aadhaar PDF -> page images, so the (UIDAI-signed) Secure QR printed on it can be decoded.
The PDF is password protected by UIDAI: first 4 letters of the name in CAPITALS + birth year (YYYY).
The password is used only to open the file in memory; it is never logged or stored.
"""
from __future__ import annotations

import io
import logging
from typing import List

import numpy as np

log = logging.getLogger("kyc.pdf")

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PAGES = 2
SCALE = 4.0          # ~290 dpi: the Secure QR is small and very dense


class PdfError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def is_pdf(raw: bytes) -> bool:
    return raw[:5] == b"%PDF-"


def render_pages(raw: bytes, password: str | None) -> List[np.ndarray]:
    if len(raw) > MAX_PDF_BYTES:
        raise PdfError("unreadable_image")
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(io.BytesIO(raw), password=(password or None))
    except pdfium.PdfiumError as exc:
        msg = str(exc).lower()
        if "password" in msg:
            raise PdfError("pdf_password_wrong" if password else "pdf_password_needed")
        raise PdfError("unreadable_image")
    out: List[np.ndarray] = []
    try:
        for i in range(min(len(pdf), MAX_PAGES)):
            pil = pdf[i].render(scale=SCALE).to_pil().convert("RGB")
            out.append(np.asarray(pil)[:, :, ::-1].copy())   # BGR for OpenCV/zxing
    finally:
        pdf.close()
    if not out:
        raise PdfError("unreadable_image")
    return out
