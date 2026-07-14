"""Fetch character data from the PoE2 OAuth API (api.pathofexile.com).

Endpoints mirror the fork's ``src/Classes/PoEAPI.lua``:

* ``GET /character/poe2``          -> the account's PoE2 character list
* ``GET /character/poe2/<name>``   -> one character (equipment + passives + jewels)

Auth is a bearer token from :mod:`pob_mcp.poe_oauth`. The raw character body is
handed straight to the engine's ``import_character`` command, which reuses PoB's
own importer — we don't parse the build here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import poe_oauth

__all__ = ["list_characters", "fetch_character_raw", "PoEApiError"]

API_BASE = "https://api.pathofexile.com"
REALM = "poe2"
USER_AGENT = poe_oauth.USER_AGENT


class PoEApiError(RuntimeError):
    """Raised on an API/network failure."""


def _get(path: str) -> bytes:
    token = poe_oauth.get_valid_token()
    req = urllib.request.Request(
        API_BASE + path,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise PoEApiError("unauthorized (401) — session invalid, log in again") from exc
        if exc.code == 403:
            raise PoEApiError("forbidden (403) — profile private or missing scope") from exc
        if exc.code == 404:
            raise PoEApiError("not found (404) — wrong character/realm") from exc
        if exc.code == 429:
            raise PoEApiError("rate limited (429) — wait a bit and retry") from exc
        raise PoEApiError(f"API HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise PoEApiError(f"API request failed: {exc}") from exc


def list_characters() -> list[dict]:
    """Return the account's PoE2 characters as ``[{name, level, class, league}, ...]``."""
    body = _get(f"/character/{REALM}")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise PoEApiError(f"could not parse character list: {exc}") from exc
    chars = data.get("characters", data if isinstance(data, list) else [])
    out = []
    for c in chars:
        out.append({
            "name": c.get("name"),
            "level": c.get("level"),
            "class": c.get("class"),
            "league": c.get("league"),
        })
    return out


def fetch_character_raw(name: str) -> str:
    """Return the raw JSON body for one character (fed as-is to the engine)."""
    if not name or not name.strip():
        raise PoEApiError("character name is required")
    body = _get(f"/character/{REALM}/{urllib.parse.quote(name.strip())}")
    return body.decode("utf-8")
