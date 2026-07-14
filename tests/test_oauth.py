"""Tests for the OAuth login flow's pure/local logic (no GGG network).

The token exchange (_exchange_code) is monkeypatched, so these exercise PKCE
generation, the authorize URL, the local redirect listener, and state checks.
"""

import base64
import hashlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from pob_mcp import poe_oauth  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    poe_oauth.logout()
    yield
    poe_oauth.logout()


def test_pkce_is_valid_s256():
    verifier, challenge = poe_oauth._generate_pkce()
    # challenge must be base64url(sha256(verifier)) with no padding
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in verifier and "=" not in challenge


def test_start_login_builds_authorize_url():
    info = poe_oauth.start_login()
    parsed = urllib.parse.urlparse(info["authorize_url"])
    q = urllib.parse.parse_qs(parsed.query)
    assert parsed.netloc == "www.pathofexile.com"
    assert q["client_id"] == ["pob"]
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"][0].startswith("http://localhost:")
    assert q["redirect_uri"][0] == info["redirect_uri"]
    assert info["port"] in poe_oauth.REDIRECT_PORTS


def test_full_local_flow_with_mocked_token(monkeypatch):
    captured = {}

    def fake_exchange(code, verifier, redirect_uri):
        captured.update(code=code, verifier=verifier, redirect_uri=redirect_uri)
        return poe_oauth._Token(access_token="tok123", refresh_token="ref", expiry=9e18)

    monkeypatch.setattr(poe_oauth, "_exchange_code", fake_exchange)

    info = poe_oauth.start_login()
    pending = poe_oauth._pending
    # Simulate the browser hitting the redirect after finish_login starts serving.
    import threading

    def hit():
        url = f"{info['redirect_uri']}/?code=abc&state={pending.state}"
        for _ in range(50):
            try:
                urllib.request.urlopen(url, timeout=1).read()
                return
            except Exception:
                import time
                time.sleep(0.05)

    threading.Thread(target=hit, daemon=True).start()
    result = poe_oauth.finish_login(timeout=10)

    assert result["logged_in"] is True
    assert captured["code"] == "abc"
    assert poe_oauth.get_valid_token() == "tok123"


def test_state_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(poe_oauth, "_exchange_code",
                        lambda *a: pytest.fail("should not exchange on state mismatch"))
    info = poe_oauth.start_login()
    import threading

    def hit():
        url = f"{info['redirect_uri']}/?code=abc&state=WRONG"
        for _ in range(50):
            try:
                urllib.request.urlopen(url, timeout=1).read()
                return
            except Exception:
                import time
                time.sleep(0.05)

    threading.Thread(target=hit, daemon=True).start()
    with pytest.raises(poe_oauth.OAuthError, match="state mismatch"):
        poe_oauth.finish_login(timeout=10)


def test_get_token_without_login_errors():
    with pytest.raises(poe_oauth.OAuthError, match="not logged in"):
        poe_oauth.get_valid_token()
