# Getting started

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer (voice platform console)
- Docker and Docker Compose (voice platform)
- MongoDB (care-plan app)
- A Google Gemini API key; telephony keys (Tata Smartflo or Twilio) for real calls

## Care-plan web app

```bash
cd care-plan-app
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Open `http://localhost:8000` for the app and `http://localhost:8000/docs` for the API. Key settings in `.env`:

| Setting | Purpose |
|---|---|
| `GEMINI_API_KEY` | Reading prescriptions and the text assistant |
| `MONGODB_URI`, `MONGODB_DB_NAME` | Where care plans and alerts are stored |
| `VOICEBOT_URL`, `VOICEBOT_API_KEY` | The voice platform to call; leave empty to run without calls |
| `ACCESS_PASSWORD` | Optional password gate for a demo deployment |

## Voice platform

```bash
cd voice-platform
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.local.example frontend/.env.local
docker compose up --build
```

| Service | Address |
|---|---|
| API | `http://localhost:8001` (container port 8055) |
| Console | `http://localhost:3001` |
| PostgreSQL, Redis, Qdrant, KYC service | internal to the compose network |

Then create the hospital workspace, desks and demo doctors:

```bash
python scripts/setup_sanjeevani.py --api http://127.0.0.1:8001 --token <your sign-in token>
```

The script can be re-run safely; each step is skipped if it is already done.

## Running without Docker (back end only)

```bash
cd voice-platform/backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

## Tests

```bash
cd care-plan-app && pytest -q
cd voice-platform/backend && pytest -q
```

Some voice-platform test files need optional packages for live model diagnostics; install `backend/requirements.txt` in full to run them.
