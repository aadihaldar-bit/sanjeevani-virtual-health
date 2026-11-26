# kyc-ml — patient identity verification

Isolated container used by the medical voice agent. The agent will not book an appointment until the
patient is verified. Heavy/risky work (face models, QR decode, signature checks) lives here so the live
voice backend is never affected.

## What is verified, and how

| Document | Method | Trust | Status |
|---|---|---|---|
| **Aadhaar** (card, e-Aadhaar PDF, mAadhaar) | **Secure QR**: payload is signed by UIDAI; we verify RSA-SHA256 against UIDAI's published certificates (bundled in `certs/`). Tampered/forged QRs fail. | cryptographic | ✅ implemented |
| Face ↔ ID | ArcFace embedding of the Aadhaar photo vs. a live selfie | biometric | ✅ implemented |
| Liveness | Server-checked head-pose challenge (turn left/right + random nod or zoom) + same-person-across-frames | demo-grade PAD, **not certified** | ✅ implemented |
| **PAN, Driving Licence, Passport, Voter ID** | Need verification **at source** (DigiLocker, or a licensed KYC provider). A photo + OCR is forgeable and is **not** treated as verified. | — | ⛔ not implemented (needs onboarding — see below) |
| Aadhaar Offline e-KYC (zip + share code) | UIDAI-signed XML | cryptographic | ⏳ not yet built |

> Not yet proven on a real card: the parser follows UIDAI's format and is tested with synthetic QRs, but
> the first real Aadhaar scan is the definitive test. See **Testing with a real card**.

## Flow
1. Agent collects the name, then speaks a one-time 6-digit code + the page address (`…/kyc`).
2. Patient opens the page on their phone, enters the code (binds the web session to the live call), consents,
   photographs the Aadhaar QR, then does a short face check.
3. Agent checks status every turn; on `verified` it continues (doctor tiers → insurance → payment → booking).
4. Booking uses the **name from the Aadhaar record**, and Health System shows the KYC badge.

## Switches (backend `.env` unless noted)
| Variable | Meaning |
|---|---|
| `KYC_ENABLED=true/false` | Master switch. `false` = agent books without KYC (instant rollback: edit + recreate backend). |
| `KYC_FAIL_MODE=open/closed` | If the ML service is down: `open` books unverified (availability), `closed` refuses to book (strict). |
| `KYC_DEMO_MODE=true/false` (**both** backend `.env` and `kyc_service/kyc.env`) | `true` accepts **demo-signed** credentials (labelled DEMO). **Must be `false` for real patients** — with it on, anyone can mint a demo credential. |
| `KYC_INTERNAL_KEY` | Shared secret backend↔kyc-ml. Same value in both files. Never commit. |
| `KYC_MATCH_THRESHOLD` (sidecar, default `0.35`) | Face-match cosine threshold. **Not calibrated on Indian ID photos** — tune with real data. |
| `KYC_SWING_DEG`, `KYC_NOD_DEG`, `KYC_ZOOM_RATIO` (sidecar) | Liveness strictness. |
| `KYC_RETENTION_DAYS` (default `90`) | After this, personal fields are stripped from old sessions (audit skeleton kept). |
| `KYC_CODE_TTL_MIN` (default `15`) | Code/session lifetime. |

## Privacy (design)
Stored: verified name, birth **year**, gender, **last 4** of Aadhaar, scores, consent (time/IP/UA).
Never stored: Aadhaar number, QR payload, Aadhaar photo, selfie/video frames. The ID-photo embedding is held
only until the session ends, then wiped. The sidecar persists nothing.
Compliance (DPDP Act 2023, Aadhaar offline-verification rules) must be reviewed by your legal/compliance team
before processing real patients — including whether registration as an offline-verification entity applies.

## Testing with a real card
1. Set `KYC_DEMO_MODE=false` (both files), recreate `kyc-ml` and `backend`.
2. Start a medical call/simulator chat, get the code, open `/kyc`, scan **your own** Aadhaar QR.
3. Watch the stages: `docker logs -f stl-voicebot-kyc-ml` — lines `aadhaar[1/4]`…`[4/4]` show where a card fails
   (QR found? numeric length? parsed? signature valid + which cert? photo decoded?). Numbers only, no PII.
4. Liveness numbers (turn angles, match score) are logged as `liveness …` for threshold tuning.

## Operations
```
cd /opt/stl-voicebot
docker compose up -d --build --no-deps kyc-ml      # rebuild/restart just this service (first build ~6 min)
docker logs -f stl-voicebot-kyc-ml
docker exec -i stl-voicebot-kyc-ml python -m tests.selftest   # 19-check self-test
python kyc_service/tests/e2e_remote.py <face.jpg>             # full live E2E (needs demo mode ON)
```
Memory: ~1.1 GB resident (capped at 1.8 GB). If it is OOM-killed only KYC is affected.

## Known limits
- Liveness is not certified PAD and not injection-attack proof (virtual cameras, deepfakes). For regulated claims use a
  certified vendor's liveness.
- Old XML-based Aadhaar QRs (pre-2018) are unsigned and are rejected.
- Face threshold and liveness angles need calibration on real cameras/real Aadhaar photos.
