"""A shared password in front of the whole app, for when it is shared over a public link.

Off unless ACCESS_PASSWORD is set. When on, every page and API asks for the password once and then remembers it in a
cookie for a week. The voice bot's server-to-server endpoint keeps its own API-key check and is left open."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

COOKIE = "bvh_access"
MAX_AGE_S = 7 * 24 * 3600
OPEN_PATHS = ("/login", "/static/css/", "/static/js/icons.js", "/favicon.ico", "/api/v1/prescriptions",
              "/api/v1/voice/callback/")      # the voicebot posts here; the secret in the URL is its credential


def _password() -> str:
    return os.getenv("ACCESS_PASSWORD", "").strip()


def _token(password: str) -> str:
    return hmac.new(password.encode(), b"sanjeevani-virtual-health-access", hashlib.sha256).hexdigest()


def _allowed(request: Request) -> bool:
    password = _password()
    if not password or request.url.path.startswith(OPEN_PATHS):
        return True
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _token(password))


async def access_middleware(request: Request, call_next):
    if _allowed(request):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Sign in first."}, status_code=401)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target)}", status_code=303)


def _safe_next(target: str) -> str:
    return target if target.startswith("/") and not target.startswith("//") else "/"


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sanjeevani Virtual Health</title><link rel="stylesheet" href="/static/css/sanjeevani.css?v=202611021030"></head>
<body class="ambient" style="min-height:100vh;display:grid;place-items:center;padding:22px">
<form method="post" action="/login" class="glass" style="width:100%;max-width:380px;border-radius:28px;padding:34px 30px">
  <a class="brand" href="/" style="margin-bottom:26px"><span class="brand-mark"><svg viewBox="0 0 24 24"><path d="M3 12.5h4l2-5 3.5 10 2.5-5H21"/></svg></span><b>Sanjeevani</b><span>Virtual Health</span></a>
  <h1 style="font-size:28px">Enter the password</h1>
  <p class="muted" style="margin:8px 0 22px;font-size:15px">This preview is shared privately.</p>
  <input type="hidden" name="next" value="{next}">
  <label class="sr" for="pw">Password</label>
  <input class="input" id="pw" name="password" type="password" autocomplete="current-password" autofocus required>
  {error}
  <button class="btn btn-primary btn-block" style="margin-top:16px">Continue</button>
</form></body></html>"""


def login_page(next: str = "/", error: str = "") -> HTMLResponse:
    from html import escape
    err = f'<p class="small" style="color:var(--bad);margin-top:10px">{escape(error)}</p>' if error else ""
    return HTMLResponse(LOGIN_PAGE.replace("{next}", escape(_safe_next(next))).replace("{error}", err))


async def login_submit(request: Request):
    form = await request.form()
    password, target = _password(), _safe_next(str(form.get("next") or "/"))
    if not password:
        return RedirectResponse(target, status_code=303)
    if not hmac.compare_digest(str(form.get("password") or ""), password):
        await asyncio.sleep(1)                     # slows down guessing
        return login_page(target, "That password is not right.")
    response = RedirectResponse(target, status_code=303)
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, _token(password), max_age=MAX_AGE_S, httponly=True, samesite="lax", secure=https)
    return response
