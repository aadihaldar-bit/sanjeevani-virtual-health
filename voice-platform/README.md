# Voice platform

The phone half of Sanjeevani Virtual Health: a multi-tenant voice agent platform. A front desk routes each caller to a desk agent (appointments, pharmacy, feedback), a scheduler places care calls after a prescription, and every action goes through tools behind an identity gate.

## Run it

```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.local.example frontend/.env.local
docker compose up --build
python scripts/setup_sanjeevani.py --api http://127.0.0.1:8001 --token <your sign-in token>
```

| Service | Address |
|---|---|
| API | `http://localhost:8001` |
| Console | `http://localhost:3001` |

## Test it

```bash
cd backend
pytest -q
```

## Layout

| Path | Role |
|---|---|
| `backend/app/` | FastAPI: calls, media bridge, agents and desks, tool executor, tenants, scheduler, KYC orchestration |
| `backend/tests/` | Tenancy, isolation, desks, tools, scheduler, KYC and API tests |
| `frontend/` | Next.js console: calls, patients, bookings, prescriptions and the KYC page |
| `kyc_service/` | Isolated service for Aadhaar QR, liveness and face match |
| `kpi_dashboard/` | Call KPI dashboard |
| `scripts/` | Workspace setup, knowledge-base build and pre-deploy checks |
| `docs/sanjeevani_demo/knowledge/` | Hospital documents loaded into the workspace |

More in the repository's [docs](../docs/voice-agent.md).
