"""
End-to-end self-test run INSIDE the container:  python -m tests.selftest
Uses insightface's bundled sample photos. Validates everything except real-camera behaviour
(which needs a human): QR round-trip, signature verify + tamper, ID-photo embedding,
same-person vs different-person scoring, and the liveness decision logic.
"""
import io
import os
import sys
import time

os.environ.setdefault("KYC_DEMO_MODE", "true")
os.environ.setdefault("KYC_DEMO_KEY_PATH", "/tmp/selftest_demo_key.pem")
os.environ.setdefault("KYC_INTERNAL_KEY", "selftest")

import numpy as np
from PIL import Image

import aadhaar as aad
import demo as demo_mod
from face import FaceEngine, decode_image, decode_qr_texts, pil_to_bgr
from liveness import evaluate

FAILS = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


def main():
    from insightface.data import get_image as ins_get_image

    t0 = time.time()
    eng = FaceEngine()
    print(f"engine load {time.time()-t0:.1f}s")

    group = ins_get_image("t1")                      # 6 different people, full-res photo
    faces = sorted(eng.app.get(group), key=lambda f: -(f.bbox[2] - f.bbox[0]))
    check("detector finds all 6 people in the group photo", len(faces) == 6, f"{len(faces)} faces")
    A = faces[0]

    def head_crop(img, f):
        x1, y1, x2, y2 = f.bbox
        w, h = x2 - x1, y2 - y1
        H, W = img.shape[:2]
        return img[max(0, int(y1 - .55 * h)):min(H, int(y2 + .45 * h)), max(0, int(x1 - .45 * w)):min(W, int(x2 + .45 * w))].copy()

    selfie = head_crop(group, A)                     # single-person image = stand-in for a live selfie
    print("selfie crop", selfie.shape)

    # -- pose/analysis sanity + timing
    t0 = time.time()
    a = eng.analyze(selfie, embed=True)
    dt = time.time() - t0
    check("analyze finds the face", bool(a.get("ok")), f"yaw={a.get('yaw', 0):.1f} pitch={a.get('pitch', 0):.1f} w={a.get('width', 0):.0f} {dt:.2f}s")
    check("embedding is 512-d", len(a.get("emb", [])) == 512)
    tom = selfie

    # -- build a DEMO credential from Tom's face, render as QR, decode it back
    crop = selfie
    jp2 = demo_mod.photo_to_jp2(Image.fromarray(crop[:, :, ::-1]))
    key = demo_mod.load_or_create_key()
    digits = demo_mod.build_digits(key, "Rohan Mehta", "14-03-1991", "M", jp2)
    print(f"photo jp2={len(jp2)}B  digits={len(digits)}")
    check("digits fit one QR", len(digits) <= demo_mod.MAX_DIGITS)
    png = demo_mod.render_qr_png(digits)
    qr_img = decode_image(png)
    texts = decode_qr_texts(qr_img)
    check("dense QR decodes back to the exact digits", bool(texts) and texts[0].strip() == digits,
          f"decoded={len(texts[0]) if texts else 0}")

    # also decode a degraded, phone-like capture (downscale + blur + jpeg)
    import cv2
    small = cv2.resize(qr_img, None, fx=0.55, fy=0.55, interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (3, 3), 0)
    ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
    texts2 = decode_qr_texts(cv2.imdecode(jpg, 1))
    check("QR still decodes after blur/downscale/jpeg", bool(texts2) and texts2[0].strip() == digits)

    # -- parse + signature
    parsed = aad.parse_payload(aad.digits_to_payload(digits))
    certs = aad.load_certs(os.getenv("KYC_CERT_DIR", "/srv/certs"))
    check("fields parsed", parsed.fields["name"] == "Rohan Mehta" and parsed.versioned, str(aad.public_summary(parsed)))
    v = aad.verify_signature(parsed, certs, key.public_key())
    check("demo signature verifies as trust=demo", v.signature_valid and v.trust == "demo")
    v_no_demo = aad.verify_signature(parsed, certs, None)
    check("demo QR is REJECTED when demo key is not provided (UIDAI certs only)", not v_no_demo.signature_valid)
    check(f"loaded {len(certs)} UIDAI certs", len(certs) >= 3)

    # tamper: change the name in the signed region
    bad = bytearray(aad.digits_to_payload(digits))
    i = bytes(bad).find(b"Rohan")
    bad[i] = ord("S")
    parsed_bad = aad.parse_payload(bytes(bad))
    check("tampered payload fails signature", not aad.verify_signature(parsed_bad, certs, key.public_key()).signature_valid)

    # -- ID photo embedding vs live face
    id_img = pil_to_bgr(Image.open(io.BytesIO(parsed.photo)))
    id_emb = eng.embed_id_photo(id_img)
    check("tiny ID photo yields an embedding", id_emb is not None, f"photo={id_img.shape[1]}x{id_img.shape[0]}")
    live = np.asarray(a["emb"], dtype=np.float32)
    same = float(np.dot(live, np.asarray(id_emb)) / (np.linalg.norm(live) * np.linalg.norm(id_emb)))
    print(f"same person (live vs tiny ID photo) cosine = {same:.3f}")
    check("same person scores above threshold", same >= 0.35, f"{same:.3f}")

    # different people from the same group photo must score well below the threshold
    live_n = live / np.linalg.norm(live)
    others = [round(float(np.dot(f.normed_embedding, live_n)), 3) for f in faces[1:]]
    print(f"other 5 people vs person A (cosine) = {others}")
    check("different people score well below threshold", all(o < 0.30 for o in others), str(max(others)))

    # -- liveness decision logic on synthetic metrics
    def fr(phase, yaw=0, pitch=0, width=200, emb=None):
        return {"phase": phase, "ok": True, "yaw": yaw, "pitch": pitch, "width": width, "blur": 120, "emb": emb}
    e = (live / np.linalg.norm(live)).tolist()
    static = [fr("straight", emb=e) for _ in range(4)] + [fr("swing", yaw=1) for _ in range(8)] + [fr("extra", pitch=1) for _ in range(6)]
    r = evaluate(static, "nod", id_emb)
    check("static photo (no movement) is NOT live", not r["liveness_passed"] and "no_head_turn" in r["reasons"], str(r["reasons"]))
    moving = ([fr("straight", emb=e) for _ in range(4)]
              + [fr("swing", yaw=y) for y in (0, 10, 22, 30, 12, -8, -20, -28)]
              + [fr("extra", pitch=p) for p in (0, 8, 16, 6, -4, -10)])
    r = evaluate(moving, "nod", id_emb)
    check("real-looking movement + matching face verifies", r["verified"], str(r["detail"]))
    zoom = ([fr("straight", emb=e) for _ in range(4)]
            + [fr("swing", yaw=y) for y in (0, 10, 22, 30, 12, -8, -20, -28)]
            + [fr("extra", width=w) for w in (180, 200, 230, 260, 220, 190)])
    check("zoom challenge verifies", evaluate(zoom, "zoom", id_emb)["verified"])
    stranger = (-np.asarray(e)).tolist()
    r = evaluate(moving, "nod", stranger)
    check("moving but WRONG face is rejected", r["liveness_passed"] and not r["face_matched"] and "face_mismatch" in r["reasons"])

    print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
