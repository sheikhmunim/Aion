"""Google OAuth login flow — opens browser, captures redirect on localhost."""

from __future__ import annotations

import asyncio
import secrets
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpx

from aion.config import get_config, save_config, save_tokens

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly"


class _CallbackHandler(BaseHTTPRequestHandler):
    auth_code: str | None = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                b"<h2>Logged in!</h2><p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body><h2>Error: {error}</h2></body></html>".encode())

    def log_message(self, format, *args):
        pass  # suppress HTTP logs


async def login() -> bool:
    """Run the full OAuth login flow. Returns True on success."""
    cfg = get_config()
    client_id = cfg.get("google_client_id")
    client_secret = cfg.get("google_client_secret")

    if not client_id or not client_secret:
        raise ValueError(
            "Google OAuth credentials not configured.\n"
            "Set AION_GOOGLE_CLIENT_ID and AION_GOOGLE_CLIENT_SECRET\n"
            "as environment variables or in ~/.aion/config.json"
        )

    state = secrets.token_urlsafe(32)
    auth_params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    _CallbackHandler.auth_code = None
    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    server_thread = Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    webbrowser.open(auth_url)

    # Wait up to 2 minutes for the callback
    for _ in range(240):
        if _CallbackHandler.auth_code is not None:
            break
        await asyncio.sleep(0.5)

    server.server_close()

    if _CallbackHandler.auth_code is None:
        raise TimeoutError("OAuth login timed out — no callback received within 2 minutes.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": _CallbackHandler.auth_code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        tokens = resp.json()

    save_tokens({
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_in": tokens.get("expires_in", 3600),
        "token_type": tokens.get("token_type", "Bearer"),
    })

    # Auto-detect user's timezone from Google Calendar
    try:
        async with httpx.AsyncClient() as client:
            cal_resp = await client.get(
                "https://www.googleapis.com/calendar/v3/calendars/primary",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if cal_resp.status_code == 200:
                tz = cal_resp.json().get("timeZone")
                if tz:
                    cfg = get_config()
                    cfg["timezone"] = tz
                    save_config(cfg)
    except Exception:
        pass  # non-critical — falls back to existing config timezone

    return True
