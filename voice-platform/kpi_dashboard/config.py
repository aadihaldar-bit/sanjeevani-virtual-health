"""
Enterprise Voice Bot KPI Dashboard - Configuration
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

DEFAULT_GCS_BUCKET = os.getenv("GCS_LOG_BUCKET", "stld-bkt-data-prod-voicebot-log-uscentral01-001").strip()
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "voicebot-support").strip()
PORT = int(os.getenv("PORT", "8080"))
HOST = os.getenv("HOST", "0.0.0.0")

CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 5 minutes

CREDENTIAL_PATHS = [
    os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
    str(PROJECT_ROOT / "secrets" / "gcs-credentials.json"),
    str(BASE_DIR / "secrets" / "gcs-credentials.json"),
    "/app/secrets/gcs-credentials.json",
    "/opt/stl-voicebot/secrets/gcs-credentials.json",
    "secrets/gcs-credentials.json",
]
