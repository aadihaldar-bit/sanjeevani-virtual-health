"""Face engine (InsightFace ArcFace) + dense-QR decoding. CPU only."""
from __future__ import annotations

import io
import logging
import os
import threading
from typing import List, Optional

import cv2
import numpy as np
from PIL import Image

log = logging.getLogger("kyc.face")
MODEL_PACK = os.getenv("KYC_MODEL_PACK", "buffalo_l")


def pil_to_bgr(im: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)


def decode_image(raw: bytes) -> np.ndarray:
    """Bytes (JPEG/PNG/WEBP/JPEG2000/HEIC-as-jpeg) -> BGR array. Pillow first (handles JP2)."""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        try:  # honour EXIF rotation from phone cameras
            from PIL import ImageOps
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        return pil_to_bgr(im)
    except Exception:
        arr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise ValueError("unreadable image")
        return arr


class FaceEngine:
    def __init__(self) -> None:
        from insightface.app import FaceAnalysis

        self.app = FaceAnalysis(
            name=MODEL_PACK,
            allowed_modules=["detection", "recognition", "landmark_3d_68"],
            providers=["CPUExecutionProvider"],
        )
        self.app.prepare(ctx_id=-1, det_size=(640, 640), det_thresh=0.45)
        self.det = self.app.det_model
        self.lmk = self.app.models.get("landmark_3d_68")
        self.rec = self.app.models.get("recognition")
        self._lock = threading.Lock()  # keep CPU contention predictable on a small VM
        log.info("FaceEngine ready (%s): models=%s", MODEL_PACK, list(self.app.models.keys()))

    def _detect(self, img: np.ndarray, size=None):
        bboxes, kpss = self.det.detect(img, input_size=size, max_num=0, metric="default")
        if bboxes is None or len(bboxes) == 0:
            return None, 0
        areas = (bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1])
        best = int(np.argmax(areas * bboxes[:, 4]))
        others = int(np.sum((areas > 0.35 * areas[best]) & (np.arange(len(areas)) != best)))
        from insightface.app.common import Face
        face = Face(bbox=bboxes[best, :4], kps=None if kpss is None else kpss[best], det_score=float(bboxes[best, 4]))
        return face, others

    def analyze(self, img: np.ndarray, embed: bool = False, fast: bool = False) -> dict:
        """Largest face -> pose, size, quality and (optionally) the ArcFace embedding.
        fast=True uses a smaller detector input (selfie faces are large) — ~3x quicker."""
        with self._lock:
            face, others = self._detect(img, size=(320, 320) if fast else None)
            if face is None:
                return {"ok": False}
            if self.lmk is not None:
                self.lmk.get(img, face)
            if embed and self.rec is not None:
                self.rec.get(img, face)
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
            h, w = img.shape[:2]
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
            crop = img[y1:y2, x1:x2]
            if crop.size == 0:
                return {"ok": False}
            gray = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
            pose = face.get("pose") if hasattr(face, "get") else None
            out = {
                "ok": True,
                "width": float(x2 - x1),
                "det": float(face.det_score),
                "yaw": float(pose[1]) if pose is not None else 0.0,
                "pitch": float(pose[0]) if pose is not None else 0.0,
                "roll": float(pose[2]) if pose is not None else 0.0,
                "blur": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                "bright": float(gray.mean()),
                "multi_face": others > 0,
            }
            if embed and getattr(face, "embedding", None) is not None:
                out["emb"] = face.normed_embedding.astype(np.float32).tolist()
            return out

    def embed_id_photo(self, img: np.ndarray) -> Optional[List[float]]:
        """Aadhaar photos are tiny: upscale and pad so the detector sees context around the face."""
        h, w = img.shape[:2]
        scale = max(1.0, 320.0 / max(1, min(h, w)))
        if scale > 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        pad = int(0.4 * max(img.shape[:2]))
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        res = self.analyze(img, embed=True)
        return res.get("emb") if res.get("ok") else None


# ───────────────────────── dense QR decoding ─────────────────────────
def decode_qr_texts(img: np.ndarray) -> List[str]:
    """Aadhaar Secure QRs are very dense (~6,000 digits). Try several enhancements."""
    variants = []
    h, w = img.shape[:2]
    base = img
    if max(h, w) > 3600:
        s = 3600.0 / max(h, w)
        base = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
    variants.append(base)
    variants.append(gray)
    variants.append(cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray))
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(otsu)
    if max(base.shape[:2]) < 1800:
        variants.append(cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))

    found: List[str] = []
    try:
        import zxingcpp
        for v in variants:
            try:
                res = zxingcpp.read_barcodes(v, formats=zxingcpp.BarcodeFormat.QRCode, try_rotate=True, try_downscale=True)
            except TypeError:
                res = zxingcpp.read_barcodes(v)
            for r in res:
                if r.text and r.text not in found:
                    found.append(r.text)
            if found:
                break
    except Exception as exc:
        log.warning("zxing-cpp unavailable/failed: %s", exc)

    if not found:
        try:
            det = cv2.QRCodeDetector()
            for v in variants[:3]:
                txt, _, _ = det.detectAndDecode(v)
                if txt:
                    found.append(txt)
                    break
        except Exception:
            pass
    return found
