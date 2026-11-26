"""
Aadhaar Secure QR: decode, parse and verify the UIDAI digital signature.

Format (UIDAI Secure QR spec, cross-checked against the open-source decoders):
  QR text  = base-10 integer
  bytes    = big-endian bytes of that integer
  payload  = gzip(bytes)
  fields   = text fields separated by 0xFF, then the JPEG2000 photo,
             then (legacy QRs only) 32-byte SHA-256 hashes of email / mobile
  signature= LAST 256 bytes of the payload (RSA-2048). Signed message = everything before it.
             Verified as RSA PKCS#1 v1.5 + SHA-256 against UIDAI's published certificates.

We never persist the photo or any full address; callers keep only name, birth year,
gender, state and the last four digits of the Aadhaar number (from the reference id).
"""
from __future__ import annotations

import re

import datetime as _dt
import glob
import io
import logging
import os
import sys
import zlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

log = logging.getLogger("kyc.aadhaar")

# A real Secure QR is ~6,000-7,000 decimal digits; CPython >=3.11 refuses int<->str above 4,300
# digits by default. Raise the cap (still bounded) and reject anything longer than a QR can hold.
if hasattr(sys, "set_int_max_str_digits"):
    sys.set_int_max_str_digits(10000)
MAX_QR_DIGITS = 8000

FIELDS = [
    "email_mobile_status", "reference_id", "name", "dob", "gender", "care_of", "district",
    "landmark", "house", "location", "pincode", "post_office", "state", "street",
    "sub_district", "vtc",
]
SEP = b"\xff"
JP2_MAGIC = b"\x00\x00\x00\x0cjP  "
J2K_MAGIC = b"\xff\x4f\xff\x51"


class QrError(Exception):
    """Raised for payloads that are not a valid Aadhaar Secure QR."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParsedQr:
    fields: dict
    photo: bytes
    signed: bytes
    signature: bytes
    versioned: bool

    @property
    def last4(self) -> str:
        ref = (self.fields.get("reference_id") or "").strip()
        return ref[:4] if ref[:4].isdigit() else ""


@dataclass
class Verification:
    signature_valid: bool
    trust: str                    # "uidai" | "demo" | "none"
    signer: str = ""
    signer_valid_until: str = ""
    signer_expired: bool = False


def digits_to_payload(text: str) -> bytes:
    """Decimal QR text -> decompressed payload bytes."""
    text = (text or "").strip()
    if not text.isdigit() or len(text) < 500 or len(text) > MAX_QR_DIGITS:
        raise QrError("not_secure_qr", "QR is not an Aadhaar Secure QR (expected a long numeric code).")
    n = int(text)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    try:
        return zlib.decompress(raw, 16 + zlib.MAX_WBITS)
    except zlib.error:
        raise QrError("not_secure_qr", "QR payload could not be decompressed.")


def _split_fields(signed: bytes, order: List[str]) -> Tuple[dict, int]:
    parts, pos = [], 0
    for _ in order:
        idx = signed.find(SEP, pos)
        if idx < 0:
            raise QrError("malformed", "QR fields are incomplete.")
        parts.append(signed[pos:idx])
        pos = idx + 1
    return {k: v.decode("iso-8859-1").strip() for k, v in zip(order, parts)}, pos


def parse_payload(payload: bytes) -> ParsedQr:
    if len(payload) < 300:
        raise QrError("malformed", "QR payload too short.")
    signature, signed = payload[-256:], payload[:-256]
    versioned = len(signed) > 2 and signed[0:1] == b"V" and signed[1:2].isdigit()
    order = (["version"] + FIELDS + ["last4_mobile"]) if versioned else list(FIELDS)

    fields, photo_start = _split_fields(signed, order)
    photo = signed[photo_start:]

    if not versioned:  # legacy: trailing SHA-256 hashes of email / mobile before the signature
        ind = fields.get("email_mobile_status", "")
        hashes_len = {"3": 64, "1": 32, "2": 32}.get(ind, 0)
        if hashes_len:
            photo = photo[:-hashes_len]

    if not (photo.startswith(JP2_MAGIC) or photo.startswith(J2K_MAGIC[:2])):
        # layout drift safety net: locate the JPEG2000 stream by magic bytes
        for magic in (JP2_MAGIC, J2K_MAGIC):
            i = signed.find(magic, 20)
            if i > 0:
                photo = signed[i:]
                break
    return ParsedQr(fields=fields, photo=photo, signed=signed, signature=signature, versioned=versioned)


def load_certs(cert_dir: str) -> List[Tuple[str, object, _dt.datetime]]:
    out = []
    for path in sorted(glob.glob(os.path.join(cert_dir, "*.cer")) + glob.glob(os.path.join(cert_dir, "*.pem"))):
        try:
            raw = open(path, "rb").read()
            try:
                cert = x509.load_pem_x509_certificate(raw)
            except ValueError:
                cert = x509.load_der_x509_certificate(raw)
            out.append((os.path.basename(path), cert.public_key(), cert.not_valid_after_utc))
        except Exception as exc:  # a bad file must not take the service down
            log.warning("could not load certificate %s: %s", path, exc)
    return out


def verify_signature(parsed: ParsedQr, certs, demo_pubkey=None) -> Verification:
    """Try every bundled UIDAI certificate, then (only if provided) the demo key."""
    now = _dt.datetime.now(_dt.timezone.utc)
    for name, pub, not_after in certs:
        try:
            pub.verify(parsed.signature, parsed.signed, padding.PKCS1v15(), hashes.SHA256())
            return Verification(True, "uidai", name, not_after.date().isoformat(), now > not_after)
        except InvalidSignature:
            continue
        except Exception as exc:
            log.debug("cert %s verify error: %s", name, exc)
    if demo_pubkey is not None:
        try:
            demo_pubkey.verify(parsed.signature, parsed.signed, padding.PKCS1v15(), hashes.SHA256())
            return Verification(True, "demo", "demo-key", "", False)
        except InvalidSignature:
            pass
    return Verification(False, "none")


def parse_legacy_xml(text: str) -> dict | None:
    """Pre-2018 / DigiLocker Aadhaar QR: plain, UNSIGNED XML (<PrintLetterBarcodeData uid= name= yob= gender= .../>).
    Regex only (no XML parser, so nothing to exploit). Returns the same field names as public_summary."""
    t = text.strip()
    if not t.startswith("<") or "name=" not in t:
        return None
    def attr(k: str) -> str:
        m = re.search(r'(?<![A-Za-z])' + k + r'\s*=\s*"([^"]{0,120})"', t, re.I)
        return m.group(1).strip() if m else ""
    name = attr("name")
    if not name:
        return None
    year = ""
    for src in (attr("yob"), attr("dob")):
        for tok in src.replace("/", "-").split("-"):
            if len(tok) == 4 and tok.isdigit():
                year = tok
    uid = re.sub(r"\D", "", attr("uid"))
    return {"name": name, "birth_year": year, "gender": attr("gender")[:1].upper(),
            "state": attr("state"), "aadhaar_last4": uid[-4:] if len(uid) >= 4 else ""}


def public_summary(parsed: ParsedQr) -> dict:
    """The minimum we keep: no full address, no number, no photo."""
    dob = parsed.fields.get("dob", "")
    year = ""
    for tok in dob.replace("/", "-").split("-"):
        if len(tok) == 4 and tok.isdigit():
            year = tok
    return {
        "name": parsed.fields.get("name", ""),
        "birth_year": year,
        "gender": parsed.fields.get("gender", ""),
        "state": parsed.fields.get("state", ""),
        "aadhaar_last4": parsed.last4,
    }
