"""
End-to-end KYC test against the LIVE deployment (run from a dev machine with ssh access):

  python e2e_remote.py <face_photo.jpg>

Drives the real stack: medical voice flow (simulator) -> spoken code -> /api/kyc/* -> sidecar.
What it can NOT do is a real head-turn on camera, so the final "human completes the selfie"
step is simulated by running the real backend function (apply_liveness_result) on the server.
Everything else — QR decode, UIDAI-signature path, face embedding, liveness rejection of a
static photo, status polling by the voice agent, booking, Health System — is exercised for real.
"""
import io
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "https://voicebot-agent.stldigitaltech.com"
SSH = ["ssh", "-i", "C:/Users/aarit/.ssh/gcp_voicebot", "-o", "StrictHostKeyChecking=no", "aarit@34.71.164.93"]
WF = 9
FAILS = []
NAME = "Kyc Tester"          # obviously-fake patient so test bookings are easy to spot
FIRST = NAME.split()[0]


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


def http(method, path, body=None, headers=None, raw=False, timeout=120):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return r.status, (data if raw else json.loads(data or b"{}"))
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data or b"{}")
        except Exception:
            return e.code, {"raw": data[:200]}


def jpost(path, obj):
    return http("POST", path, json.dumps(obj).encode(), {"Content-Type": "application/json", "Authorization": "Bearer demo_token"})


def multipart(fields, files):
    b = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, fn, data, ct in files:
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: {ct}\r\n\r\n'.encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), {"Content-Type": f"multipart/form-data; boundary={b}"}


class Chat:
    def __init__(self, sid):
        _, g = http("GET", f"/api/simulator/greeting?workflow_id={WF}", headers={"Authorization": "Bearer demo_token"})
        self.node, self.sid = g.get("node_id"), sid
        self.hist = [{"role": "assistant", "content": g.get("text")}]

    def say(self, text):
        _, r = jpost("/api/simulator/chat", {"session_id": self.sid, "user_message": text, "history": self.hist,
                                             "current_node_id": self.node, "workflow_id": WF})
        rep = r.get("text") or ""
        self.hist += [{"role": "user", "content": text}, {"role": "assistant", "content": rep}]
        print(f"   U: {text}\n   A: {rep[:230]}")
        return rep, r.get("tool_called")


def server_py(code: str) -> str:
    p = subprocess.run(SSH + ["sudo docker exec -i stl-voicebot-backend python -"], input=code.encode(), capture_output=True, timeout=120)
    return (p.stdout + p.stderr).decode(errors="ignore")


def main(photo_path):
    photo = open(photo_path, "rb").read()
    sid = f"kyc-e2e-{int(time.time())}"
    print(f"== session {sid} ==")

    # ---------------- conversation until the KYC gate ----------------
    c = Chat(sid)
    c.say("I have a toothache")
    rep, tool = c.say(f"My name is {NAME}")
    check("agent asks to verify identity and speaks a one-time code", "verify your identity" in rep and tool == "create_kyc_session")
    m = re.search(r"code: ((?:\d ){5}\d)", rep)
    code = m.group(1).replace(" ", "") if m else None
    check("6-digit code is spoken digit-by-digit", bool(code), code or "")
    check("page address is spoken", "slash kyc" in rep)

    rep, _ = c.say("done")
    check("saying 'done' too early is NOT accepted (no booking yet)", "don't see the verification started" in rep, rep[:60])
    rep, _ = c.say("can you repeat the code")
    check("agent can repeat the code", code in rep.replace(" ", ""))

    # ---------------- patient side on the phone ----------------
    st, _ = http("POST", "/api/kyc/exchange", json.dumps({"code": "000000"}).encode(), {"Content-Type": "application/json"})
    check("wrong code rejected", st == 404)
    st, sess = http("POST", "/api/kyc/exchange", json.dumps({"code": code}).encode(), {"Content-Type": "application/json"})
    check("correct code exchanges for a session", st == 200 and sess.get("token"), f"extra={sess.get('challenge', {}).get('extra')}")
    tok = sess["token"]
    body, hd = multipart({}, [("image", "x.jpg", photo, "image/jpeg")])
    st, r = http("POST", f"/api/kyc/s/{tok}/aadhaar", body, hd)
    check("QR step is blocked until consent is given", st == 403)
    st, _ = http("POST", f"/api/kyc/s/{tok}/consent", b"{}", {"Content-Type": "application/json"})
    check("consent recorded", st == 200)

    st, r = http("POST", f"/api/kyc/s/{tok}/aadhaar", *multipart({}, [("image", "x.jpg", photo, "image/jpeg")]))
    check("a plain face photo (no QR) is rejected cleanly", st == 200 and r.get("ok") is False and r.get("reason") == "qr_not_found", str(r.get("reason")))

    # demo credential made from the presenter's own photo, then scanned
    body, hd = multipart({"name": NAME, "dob": "14-03-1991", "gender": "M"}, [("photo", "me.jpg", photo, "image/jpeg")])
    st, png = http("POST", "/api/kyc/demo/credential", body, hd, raw=True)
    check("demo credential generated as a PNG QR", st == 200 and png[:4] == b"\x89PNG", f"{len(png) if st == 200 else png}")
    import os, tempfile
    open(os.path.join(tempfile.gettempdir(), "kyc_demo_qr.png"), "wb").write(png if st == 200 else b"")

    t0 = time.time()
    body, hd = multipart({}, [("image", "qr.png", png, "image/png")])
    st, r = http("POST", f"/api/kyc/s/{tok}/aadhaar", body, hd)
    check("Aadhaar Secure QR verified (signature + ID photo)", st == 200 and r.get("ok") is True, f"{time.time()-t0:.1f}s")
    ident = r.get("identity") or {}
    check("identity returned is minimal and labelled demo", ident.get("name") == NAME and ident.get("demo") is True and "aadhaar" not in json.dumps(r).lower().replace("aadhaar_last4", ""), str(ident))

    # liveness must REJECT a static photo
    phases = ["straight"] * 4 + ["swing"] * 9 + ["extra"] * 7
    t0 = time.time()
    body, hd = multipart({"meta": json.dumps({"phases": phases})}, [("frames", f"f{i}.jpg", photo, "image/jpeg") for i in range(len(phases))])
    st, r = http("POST", f"/api/kyc/s/{tok}/selfie", body, hd)
    check("static photo held to the camera is NOT accepted as live", st == 200 and r.get("ok") is False, f"reasons={r.get('reasons')} {time.time()-t0:.1f}s")
    check("liveness failure gives a friendly message + attempts left", bool(r.get("messages")) and r.get("attempts_left") == 2, str(r.get("attempts_left")))

    # while the selfie is pending the agent keeps waiting
    rep, _ = c.say("done")
    check("agent says the face check isn't finished yet", "face check isn't finished" in rep, rep[:70])

    # ---------------- simulate the human completing the selfie (real server function) ----------------
    out = server_py(f"""
import asyncio
from app import kyc_core
from app.database import async_session
from app.models import KycSession, Call
from sqlalchemy import select
async def m():
    async with async_session() as db:
        call=(await db.execute(select(Call).where(Call.conversation_id=="{sid}"))).scalars().first()
        s=(await db.execute(select(KycSession).where(KycSession.call_id==call.id).order_by(KycSession.id.desc()))).scalars().first()
        print("call_id", call.id, "session", s.id, s.status)
        r=await kyc_core.apply_liveness_result(s.id, {{"verified": True, "liveness_passed": True, "face_matched": True, "match_score": 0.672}})
        print("result", r)
asyncio.run(m())
""")
    print("   server:", out.strip().replace("\n", " | ")[:200])
    check("session marked verified", "'ok': True" in out)

    # ---------------- agent continues to booking ----------------
    rep, _ = c.say("okay done")
    check("agent confirms identity and offers doctor tiers", "identity is verified" in rep and "two options" in rep and FIRST in rep, rep[:90])
    c.say("senior")
    c.say("I'll pay directly")
    rep, tool = c.say("paid")
    check("appointment booked only after verification", tool == "schedule_appointment" and f"You're all set, {NAME}" in rep and "identity is already verified" in rep, rep[:100])

    # ---------------- dashboard ----------------
    _, hc = http("GET", "/api/health/calls?limit=50", headers={"Authorization": "Bearer demo_token"})
    mine = [x for x in hc.get("calls", []) if x.get("conversation_id") == sid]
    k = (mine[0].get("kyc") if mine else None) or {}
    check("Health System lists the call with KYC verified", k.get("status") == "verified" and k.get("verified_name") == NAME, str(k))
    check("KYC summary holds no Aadhaar number / photo", "embedding" not in json.dumps(k) and len(k.get("aadhaar_last4") or "") == 4)
    appt = (mine[0].get("latest_appointment") or {}) if mine else {}
    check("appointment note records the verification", "Identity verified" in (appt.get("notes") or ""), appt.get("notes"))

    st, r2 = http("POST", f"/api/kyc/s/{tok}/consent", b"{}", {"Content-Type": "application/json"})
    check("finished session cannot be reused", st == 409, str(st))
    st, r2 = http("POST", "/api/kyc/exchange", json.dumps({"code": code}).encode(), {"Content-Type": "application/json"})
    check("code is single-use (wiped after completion)", st == 404)

    print("\n" + ("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main(sys.argv[1])
