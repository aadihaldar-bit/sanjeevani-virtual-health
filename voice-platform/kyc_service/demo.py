"""
DEMO credential generator.

Builds a Secure-QR-format payload for a *fictional/own* identity and signs it with a local
DEMO key (never a UIDAI key). It is accepted only while KYC_DEMO_MODE=true, and every result
is labelled trust="demo" so nobody mistakes it for a UIDAI-verified document.
Lets a presenter show the full flow (QR -> liveness -> face match) without a real Aadhaar.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import io
import os
import re
import secrets
import sys

import segno
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from PIL import Image, ImageDraw, ImageFont

if hasattr(sys, "set_int_max_str_digits"):  # QR payloads are ~6-7k digits; see aadhaar.py
    sys.set_int_max_str_digits(10000)

KEY_PATH = os.getenv("KYC_DEMO_KEY_PATH", "/data/demo_key.pem")
SEP = b"\xff"
MAX_DIGITS = 7000  # QR v40-L numeric capacity is 7089


def load_or_create_key():
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    os.makedirs(os.path.dirname(KEY_PATH), exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with open(KEY_PATH, "wb") as f:
        f.write(pem)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    return key


def photo_to_jp2(im: Image.Image, budget: int = 1900) -> bytes:
    """Aadhaar-style tiny JPEG2000 photo that fits inside a QR code."""
    im = im.convert("RGB").resize((96, 120), Image.LANCZOS)
    for ratio in (22, 28, 34, 42, 52, 64, 80, 110, 150):
        buf = io.BytesIO()
        im.save(buf, "JPEG2000", quality_mode="rates", quality_layers=[ratio], irreversible=True)
        if buf.tell() <= budget:
            return buf.getvalue()
    return buf.getvalue()


def _clean(s: str, n: int) -> str:
    s = re.sub(r"[^A-Za-z .'-]", "", s or "").strip()[:n]
    return s or "Demo Patient"


def build_digits(key, name: str, dob: str, gender: str, photo_jp2: bytes) -> str:
    last4 = f"{secrets.randbelow(10000):04d}"
    ref = last4 + _dt.datetime.utcnow().strftime("%Y%m%d%H%M%S") + "000"
    fields = [
        "V2", "0", ref, _clean(name, 40), dob, (gender or "M")[:1].upper(), "", "DEMO DISTRICT", "", "", "",
        "000000", "", "DEMO STATE", "", "", "", "0000",
    ]
    signed = SEP.join(f.encode("iso-8859-1") for f in fields) + SEP + photo_jp2
    sig = key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    gz = gzip.compress(signed + sig, 9)
    return str(int.from_bytes(gz, "big"))


def render_qr_png(digits: str, caption: str = "DEMO CREDENTIAL — not a real Aadhaar") -> bytes:
    if len(digits) > MAX_DIGITS:
        raise ValueError(f"payload too large for a QR ({len(digits)} digits)")
    qr = segno.make(digits, error="l", mode="numeric")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=9, border=5)
    qr_im = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    band = 70
    canvas = Image.new("RGB", (qr_im.width, qr_im.height + band), "white")
    canvas.paste(qr_im, (0, 0))
    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=34)
    except TypeError:
        font = ImageFont.load_default()
    d.text((20, qr_im.height + 14), caption, fill=(180, 0, 0), font=font)
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()
