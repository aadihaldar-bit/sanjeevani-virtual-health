"""Find the face in a frame and average the skin colour over forehead + both cheeks (MediaPipe, CPU)."""
from __future__ import annotations

import os

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_landmarker.task")

FOREHEAD = [103, 67, 109, 10, 338, 297, 332, 334, 296, 336, 9, 107, 66, 105]
LEFT_CHEEK = [116, 117, 118, 119, 100, 36, 205, 187]
RIGHT_CHEEK = [345, 346, 347, 348, 329, 266, 425, 411]
REGIONS = {"forehead": FOREHEAD, "left_cheek": LEFT_CHEEK, "right_cheek": RIGHT_CHEEK}
NOSE_TIP = 1
UPPER_LIP, LOWER_LIP = 13, 14
# Eye corners and lids (outer, upper, upper, inner, lower, lower) for the eye aspect ratio: lid gap / eye width.
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
# Small skin patches centred on landmarks; each is scored on its own so shadowed or glary patches count less.
PATCH_LANDMARKS = [
    10, 151, 9, 108, 337, 67, 297, 69, 299, 104, 333,          # forehead
    50, 101, 118, 117, 123, 205, 36, 187,                      # left cheek
    280, 330, 347, 346, 352, 425, 266, 411,                    # right cheek
    6, 197,                                                    # nose bridge
]
PATCH_HALF = 0.035                                             # half-size as a fraction of face width


def eye_openness(pts: np.ndarray) -> float:
    """Mean eye aspect ratio of both eyes (Soukupova & Cech 2016): about 0.25-0.35 open, under 0.1 closed."""
    ears = []
    for p1, p2, p3, p4, p5, p6 in (LEFT_EYE, RIGHT_EYE):
        width = np.linalg.norm(pts[p1] - pts[p4])
        ears.append((np.linalg.norm(pts[p2] - pts[p6]) + np.linalg.norm(pts[p3] - pts[p5])) / (2 * max(width, 1e-6)))
    return float(np.mean(ears))


class FaceSampler:
    def __init__(self, model_path: str = MODEL):
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"face model not found: {model_path}")
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=vision.RunningMode.IMAGE, num_faces=1,
            output_face_blendshapes=False, output_facial_transformation_matrixes=False)
        self._lm = vision.FaceLandmarker.create_from_options(opts)

    def _detect(self, rgb_frame: np.ndarray):
        return self._lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb_frame)))

    def sample(self, frame_bgr: np.ndarray):
        """Returns None when no face, else dict(rgb=(3,) mean skin colour, nose=(x, y) in 0..1, polys=pixel polygons)."""
        h, w = frame_bgr.shape[:2]
        rgb_frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        res = self._detect(rgb_frame)
        if not res.face_landmarks and rgb_frame.mean() < 80:
            # A dark room hides the face from the detector; look on a brightened copy but keep the real colours for the signal.
            gain = min(4.0, 110.0 / max(float(rgb_frame.mean()), 1.0))
            res = self._detect(cv2.convertScaleAbs(rgb_frame, alpha=gain))
        if not res.face_landmarks:
            return None
        lm = res.face_landmarks[0]
        pts = np.array([(p.x * w, p.y * h) for p in lm], dtype=np.float32)
        polys = [pts[idx].astype(np.int32) for idx in REGIONS.values()]
        mask = np.zeros((h, w), np.uint8)
        for poly in polys:
            cv2.fillPoly(mask, [poly], 255)
        if int(mask.sum() // 255) < 400:
            return None
        mean = cv2.mean(rgb_frame, mask=mask)[:3]
        gray = float(np.mean(mean))
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        half = max(2, int(PATCH_HALF * (x1 - x0)))
        patches, boxes = np.full((len(PATCH_LANDMARKS), 3), np.nan), []
        for i, idx in enumerate(PATCH_LANDMARKS):
            cx, cy = int(pts[idx, 0]), int(pts[idx, 1])
            ya, yb, xa, xb = max(0, cy - half), min(h, cy + half), max(0, cx - half), min(w, cx + half)
            if yb - ya >= 2 and xb - xa >= 2:
                patches[i] = rgb_frame[ya:yb, xa:xb].reshape(-1, 3).mean(axis=0)
            boxes.append((xa, ya, xb, yb))
        return {"rgb": np.array(mean, float), "nose": (lm[NOSE_TIP].x, lm[NOSE_TIP].y), "polys": polys, "face_brightness": gray,
                "bbox": (float(x0), float(y0), float(x1), float(y1)), "face_y": float(pts[:, 1].mean() / h),
                "patches": patches, "patch_boxes": boxes,
                "mouth": float(np.linalg.norm(pts[UPPER_LIP] - pts[LOWER_LIP]) / max(y1 - y0, 1.0)),
                "face_size": float((x1 - x0) / w), "eye": eye_openness(pts)}
