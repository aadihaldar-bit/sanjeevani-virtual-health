"""
Enterprise Voice Bot KPI Dashboard - Local Launcher
Run locally on http://localhost:8080
"""

import sys
import os
import uvicorn
from pathlib import Path

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kpi_dashboard.config import PORT, HOST

def main():
    print("=" * 70)
    print("  STL DIGITAL — ENTERPRISE VOICE BOT KPI DASHBOARD")
    print("=" * 70)
    print(f"  Local URL : http://localhost:{PORT}")
    print(f"  API Docs  : http://localhost:{PORT}/docs")
    print(f"  Health    : http://localhost:{PORT}/health")
    print("=" * 70)
    
    uvicorn.run(
        "kpi_dashboard.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info"
    )

if __name__ == "__main__":
    main()
