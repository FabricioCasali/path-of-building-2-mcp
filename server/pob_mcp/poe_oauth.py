"""OAuth (Authorization Code + PKCE) login against pathofexile.com.

This mirrors exactly what Path of Building 2 does (see the fork's
``src/Classes/PoEAPI.lua`` + ``src/LaunchServer.lua``):

* public client ``client_id=pob`` (no secret), PKCE ``S256``
* the browser is sent to ``/oauth/authorize`` and redirected back to a tiny
  local HTTP listener on ``http://localhost:<port>`` (ports 49082-49084, the
  same GGG allows for the ``pob`` client)
* the captured ``code`` is exchanged for a bearer token at ``/oauth/token``

The MCP server calls :func:`start_login` (returns the URL to open) and then
:func:`finish_login` (blocks until the redirect lands, then swaps the code for
a token). Whoever opens the URL — the user, or the assistant via browser
automation — the local listener catches the redirect the same way.

Only the Python stdlib is used. Tokens are held in memory for the process
lifetime (no on-disk persistence yet).

NOTE ON ``client_id=pob``: this is Path of Building's registered GGG OAuth
client. Reusing it is what makes the localhost redirect work without
registering our own app; treat it as "driving the same PoB integration". If GGG
ever gates it, register a dedicated client and swap CLIENT_ID + the allowed
redirect ports.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

__all__ = ["start_login", "finish_login", "get_valid_token", "logout", "OAuthError"]

CLIENT_ID = "pob"
AUTHORIZE_URL = "https://www.pathofexile.com/oauth/authorize"
TOKEN_URL = "https://www.pathofexile.com/oauth/token"
SCOPES = ["account:profile", "account:leagues", "account:characters"]
REDIRECT_PORTS = (49082, 49083, 49084)
# GGG's OAuth policy wants a descriptive UA with contact info.
USER_AGENT = "OAuth pob/1.0 (contact: pob2-mcp) StrictMode"


class OAuthError(RuntimeError):
    """Raised on any failure in the login / token flow."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# --------------------------------------------------------------------------
# Pending-login state (between start_login and finish_login) and token store
# --------------------------------------------------------------------------

@dataclass
class _Pending:
    verifier: str
    state: str
    redirect_uri: str
    server: "http.server.HTTPServer"
    result: dict = field(default_factory=dict)  # filled by the request handler
    event: threading.Event = field(default_factory=threading.Event)


@dataclass
class _Token:
    access_token: str
    refresh_token: str | None
    expiry: float  # epoch seconds


_pending: _Pending | None = None
_token: _Token | None = None
_lock = threading.Lock()


def _make_handler(pending: _Pending):
    class _RedirectHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):  # silence stderr spam
            pass

        def do_GET(self):  # noqa: N802
            query = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(query)
            pending.result = {
                "code": (params.get("code") or [None])[0],
                "state": (params.get("state") or [None])[0],
                "error": (params.get("error") or [None])[0],
            }
            body = (
                b"<!doctype html><html><head><meta charset='utf-8'>"
                b"<title>PoB2 MCP - login</title></head>"
                b"<body style='font-family:sans-serif;background:#121212;color:#fff;"
                b"display:flex;align-items:center;justify-content:center;height:100vh'>"
                b"<h2>Login complete &mdash; you can close this tab.</h2></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            pending.event.set()

    return _RedirectHandler


def _bind_listener() -> "http.server.HTTPServer":
    # Bind with a stub handler first; start_login swaps in the real handler once
    # it has the pending state. Same ports GGG allows for the "pob" client.
    last_err = None
    for port in REDIRECT_PORTS:
        try:
            return http.server.HTTPServer(("localhost", port), http.server.BaseHTTPRequestHandler)
        except OSError as exc:  # port in use
            last_err = exc
            continue
    raise OAuthError(f"could not bind a redirect listener on {REDIRECT_PORTS}: {last_err}")


def start_login() -> dict:
    """Begin an OAuth login. Returns the authorize URL to open in a browser.

    Binds a local redirect listener and stashes the PKCE/state so that a
    subsequent :func:`finish_login` can complete the exchange.
    """
    global _pending
    with _lock:
        if _pending is not None:
            try:
                _pending.server.server_close()
            except Exception:
                pass
            _pending = None

        server = _bind_listener()
        port = server.server_address[1]
        redirect_uri = f"http://localhost:{port}"
        verifier, challenge = _generate_pkce()
        state = secrets.token_hex(16)

        pending = _Pending(verifier=verifier, state=state, redirect_uri=redirect_uri, server=server)
        server.RequestHandlerClass = _make_handler(pending)
        _pending = pending

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "redirect_uri": redirect_uri,
    }
    authorize_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return {"authorize_url": authorize_url, "redirect_uri": redirect_uri, "port": port}


def finish_login(timeout: float = 180.0) -> dict:
    """Block until the browser redirect lands, then exchange the code for a token.

    Returns a small dict with the token's expiry. Raises ``OAuthError`` on
    timeout, state mismatch, or a token-endpoint failure.
    """
    global _pending, _token
    pending = _pending
    if pending is None:
        raise OAuthError("no login in progress — call start_login first")

    thread = threading.Thread(target=pending.server.serve_forever, daemon=True)
    thread.start()
    try:
        if not pending.event.wait(timeout):
            raise OAuthError(f"timed out after {timeout:.0f}s waiting for the browser redirect")
    finally:
        pending.server.shutdown()
        pending.server.server_close()

    res = pending.result
    _pending = None

    if res.get("error"):
        raise OAuthError(f"authorization denied: {res['error']}")
    if not res.get("code"):
        raise OAuthError("no authorization code received")
    if res.get("state") != pending.state:
        raise OAuthError("OAuth state mismatch (possible CSRF) — aborting")

    token = _exchange_code(res["code"], pending.verifier, pending.redirect_uri)
    with _lock:
        _token = token
    return {"logged_in": True, "expires_in": max(0, int(token.expiry - time.time()))}


def _post_form(fields: dict) -> dict:
    data = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            import json

            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise OAuthError(f"token endpoint HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OAuthError(f"token endpoint request failed: {exc}") from exc


def _exchange_code(code: str, verifier: str, redirect_uri: str) -> _Token:
    payload = _post_form({
        "client_id": CLIENT_ID,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "code_verifier": verifier,
    })
    if "access_token" not in payload:
        raise OAuthError(f"token response missing access_token: {payload}")
    return _Token(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expiry=time.time() + float(payload.get("expires_in", 600)),
    )


def _refresh(token: _Token) -> _Token:
    if not token.refresh_token:
        raise OAuthError("session expired and no refresh token — log in again")
    payload = _post_form({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
    })
    if "access_token" not in payload:
        raise OAuthError("refresh failed — log in again")
    return _Token(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", token.refresh_token),
        expiry=time.time() + float(payload.get("expires_in", 600)),
    )


def get_valid_token() -> str:
    """Return a currently-valid access token, refreshing if near expiry.

    Raises ``OAuthError`` if there is no session at all.
    """
    global _token
    with _lock:
        if _token is None:
            raise OAuthError("not logged in — run the login flow first")
        if _token.expiry - time.time() < 30:
            _token = _refresh(_token)
        return _token.access_token


def logout() -> None:
    global _token, _pending
    with _lock:
        _token = None
        if _pending is not None:
            try:
                _pending.server.server_close()
            except Exception:
                pass
            _pending = None
