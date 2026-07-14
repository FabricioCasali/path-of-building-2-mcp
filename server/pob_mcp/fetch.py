"""Resolve a build reference (URL or paste) into a PoB2 share code / XML.

The headless engine has no network, but this Python server does — so when the
caller hands us a link instead of the raw code, we download it here. Supports
the common places players share PoB2 builds:

* ``pobb.in/<id>``           -> fetches ``pobb.in/<id>/raw``
* ``pastebin.com/<id>``      -> fetches ``pastebin.com/raw/<id>``
* any other ``http(s)`` URL  -> fetched as-is (assumed to serve the raw text)

Only the Python stdlib is used (``urllib``). The fetched body is whatever the
endpoint returns: a base64 share code (the usual case) or raw build XML — the
caller decides how to interpret it.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from urllib.parse import urlparse, urlunparse

__all__ = ["looks_like_url", "looks_like_xml", "fetch_build_source", "FetchError"]

_USER_AGENT = "pob2-mcp/1.0 (+https://github.com/PathOfBuildingCommunity)"
_TIMEOUT = 15.0
_MAX_BYTES = 4 * 1024 * 1024  # generous; a share code is a few KB


class FetchError(RuntimeError):
    """Raised when a build URL can't be fetched or looks wrong."""


def looks_like_url(text: str) -> bool:
    """True if ``text`` is an http(s) URL rather than a pasted code/XML."""
    text = text.strip()
    if "\n" in text or " " in text:
        return False
    return text.startswith("http://") or text.startswith("https://")


def looks_like_xml(text: str) -> bool:
    """True if ``text`` is already a raw PoB build XML document."""
    head = text.lstrip()[:200].lower()
    return head.startswith("<?xml") or "<pathofbuilding" in head


def _normalize_to_raw(url: str) -> str:
    """Map a human-facing build URL to its raw-content endpoint."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    if host.endswith("pobb.in"):
        # /<id> -> /<id>/raw ; leave an explicit /raw alone
        if not path.endswith("/raw"):
            path = f"{path}/raw"
    elif host.endswith("pastebin.com"):
        # /<id> -> /raw/<id> ; leave an existing /raw/<id> alone
        if not path.startswith("/raw/"):
            path = f"/raw{path}"

    return urlunparse(parsed._replace(path=path))


def fetch_build_source(url: str) -> str:
    """Download a build reference URL and return the raw body text.

    Normalizes known share hosts (pobb.in, pastebin) to their raw endpoint.
    Raises ``FetchError`` on any network/HTTP failure or an empty body.
    """
    raw_url = _normalize_to_raw(url.strip())
    request = urllib.request.Request(raw_url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as resp:
            body = resp.read(_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} fetching {raw_url}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError(f"could not fetch {raw_url}: {exc}") from exc

    if len(body) > _MAX_BYTES:
        raise FetchError(f"response from {raw_url} is suspiciously large (>4 MB)")

    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        raise FetchError(f"empty response from {raw_url}")
    return text
