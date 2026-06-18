"""
Fyers token management for back-testing.

Handles loading, expiry-checking, and auto-regeneration of the Fyers
access token stored in config/fyers_creds.json.

Token regen flow (mirrors D:\\trade genToken.py):
  1. Read client_id + secret_key from config/fyers_creds.json
  2. Generate the Fyers auth URL
  3. Open the browser and spin up a local HTTP callback server
  4. Catch the auth_code from the redirect
  5. Exchange it for a fresh access_token
  6. Write it back to config/fyers_creds.json
"""
from __future__ import annotations

import base64
import json
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from fyers_apiv3 import fyersModel
except ImportError:
    fyersModel = None  # Allows importing the module even without fyers installed

CREDS_PATH = Path("config") / "fyers_creds.json"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8080
REDIRECT_URI = f"http://{CALLBACK_HOST}:{CALLBACK_PORT}/callback"


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def load_creds() -> dict:
    """Load credentials from config/fyers_creds.json."""
    if not CREDS_PATH.exists():
        raise SystemExit(
            f"Fyers credentials not found at {CREDS_PATH}.\n"
            "Copy config/fyers_creds.json.example to config/fyers_creds.json "
            "and fill in your client_id and secret_key, then run:\n"
            "  python fetch_data.py ... (will auto-generate token)"
        )
    with open(CREDS_PATH) as f:
        return json.load(f)


def save_creds(creds: dict) -> None:
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(creds, indent=2))


# ---------------------------------------------------------------------------
# Token expiry
# ---------------------------------------------------------------------------

def token_expiry(access_token: str) -> datetime | None:
    """Decode the JWT exp claim without a crypto library."""
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return datetime.fromtimestamp(data["exp"], tz=timezone.utc)
    except Exception:
        return None


def is_token_valid(access_token: str) -> bool:
    expiry = token_expiry(access_token)
    if expiry is None:
        return True  # Can't decode — assume valid, let the API call fail if not
    return expiry > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Token regeneration
# ---------------------------------------------------------------------------

class _CallbackHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that captures the Fyers auth_code redirect."""

    def log_message(self, format, *args):
        return  # Suppress request logs

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get("auth_code") or query.get("code")
        if code:
            self.server._result["code"] = code[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Authorization complete.</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>No auth code found.</h2></body></html>")


def regenerate_token(creds: dict) -> str:
    """
    Open the Fyers login page in the browser, wait for the OAuth redirect,
    exchange the auth_code for a fresh access_token, persist it, and return it.
    """
    if fyersModel is None:
        raise SystemExit(
            "fyers-apiv3 is not installed.\n"
            "Run: pip install fyers-apiv3"
        )

    client_id  = creds.get("client_id")
    secret_key = creds.get("secret_key")
    if not client_id or not secret_key:
        raise SystemExit(
            "config/fyers_creds.json must contain 'client_id' and 'secret_key'.\n"
            "See config/fyers_creds.json.example."
        )

    session = fyersModel.SessionModel(
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code",
    )

    auth_url = session.generate_authcode()
    print(f"\nOpening Fyers login in browser...")
    print(f"If the browser doesn't open, visit:\n  {auth_url}\n")

    result: dict = {}
    server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CallbackHandler)
    server._result = result
    server_ready = threading.Event()

    def _serve():
        server_ready.set()
        server.serve_forever()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    server_ready.wait(timeout=5)
    webbrowser.open(auth_url)
    thread.join(timeout=300)

    if "code" not in result:
        raise SystemExit("Timed out waiting for Fyers redirect. Re-run and complete login within 5 minutes.")

    session.set_token(result["code"])
    token_response = session.generate_token()
    if not isinstance(token_response, dict):
        raise SystemExit(f"Unexpected token response: {token_response}")

    access_token = token_response.get("access_token")
    if not access_token:
        raise SystemExit(f"Failed to generate access token: {token_response}")

    creds["access_token"] = access_token
    save_creds(creds)
    print(f"Access token saved to {CREDS_PATH.resolve()}\n")
    return access_token


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_valid_token() -> tuple[str, str]:
    """
    Load credentials, check token validity, auto-regen if expired.
    Returns (client_id, access_token).
    """
    creds = load_creds()
    client_id    = creds.get("client_id")
    access_token = creds.get("access_token", "")

    if not client_id:
        raise SystemExit(
            "config/fyers_creds.json is missing 'client_id'.\n"
            "See config/fyers_creds.json.example."
        )

    if not access_token or not is_token_valid(access_token):
        expiry = token_expiry(access_token) if access_token else None
        if expiry:
            local = expiry.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
            print(f"Fyers token expired at {local}. Regenerating...")
        else:
            print("No valid Fyers token found. Generating...")
        access_token = regenerate_token(creds)

    return client_id, access_token
