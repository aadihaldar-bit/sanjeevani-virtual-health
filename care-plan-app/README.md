# Care-plan web app

The patient- and staff-facing half of Sanjeevani Virtual Health. It turns a prescription into a plain-language care plan and keeps the patient on track afterwards: a text assistant that answers from the plan, medicine delivery, doctor booking, a vitals check, lab tests at home and care alerts. It asks the voice platform to phone the patient when a call helps.

## Run it

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # add GEMINI_API_KEY and MONGODB_URI
python main.py
```

- App: `http://localhost:8000`
- API documentation: `http://localhost:8000/docs`

## Test it

```bash
pip install -r requirements-test.txt
pytest -q
```

## Layout

| Path | Role |
|---|---|
| `main.py` | FastAPI app, routes and the prescription-to-plan endpoint |
| `ai_brain/` | Prescription reading and care-plan planning, with model and rule-based fallbacks |
| `chat.py` | Text assistant that answers from the plan only |
| `alerts.py` | Refill and vitals alerts; staff press Call now |
| `voicebot.py`, `webhook.py`, `events.py` | Talking to the voice platform and receiving call outcomes |
| `vitals_api.py`, `vitals/` | One-minute face-video vitals check (CPU) |
| `audit.py`, `access_gate.py` | Audit log and optional password gate |
| `static/` | The web app |

More in the repository's [docs](../docs/care-plan.md).
