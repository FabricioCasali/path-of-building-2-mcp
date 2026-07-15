"""Path of Exile 2 trade search — the site's ``/api/trade2`` API.

This is NOT the official OAuth developer API (that has no item search). It's the
trade *site's* own API, the same one Awakened PoE Trade / PoE Overlay use:

* static data (public):    GET  /api/trade2/data/{leagues,items,stats,static}
* search (needs POESESSID): POST /api/trade2/search/poe2/<league>  -> result ids
* fetch  (needs POESESSID): GET  /api/trade2/fetch/<ids>?query=<id> -> listings

Auth is the browser **POESESSID** cookie (OAuth tokens do NOT work here); pass it
via the ``POE_SESSID`` env var or the ``sessid`` argument.

ToS-safe usage: manual-triggered price checks only. NO auto-whisper, NO auto-buy,
NO bulk scraping. GGG rate-limits hard and revokes access on abuse, so every
request goes through a limiter that honors the ``X-Rate-Limit-*`` headers and
``Retry-After``. Keep it to what a human price-checking would do.

Demo::

    set POE_SESSID=<your cookie>
    python -m pob_mcp.trade --league "Runes of Aldur" --type "Twin Crossbow" --top 5
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = ["TradeError", "TradeClient"]

BASE = "https://www.pathofexile.com/api/trade2"
REALM = "poe2"
# GGG wants an identifiable UA; a browser-ish one is needed to clear Cloudflare.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "pob2-mcp/1.0 (+price-check; contact: pob2-mcp)")


class TradeError(RuntimeError):
    """Search/fetch/auth/network failure."""


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
@dataclass
class _Bucket:
    """One GGG rate-limit rule: at most ``hits`` per ``period`` seconds."""

    hits: int
    period: int
    restrict: int

    @classmethod
    def parse(cls, spec: str) -> "_Bucket":
        h, p, r = (int(x) for x in spec.split(":"))
        return cls(h, p, r)


class _RateLimiter:
    """Honors GGG's dynamic ``X-Rate-Limit-*`` headers plus a hard floor.

    Strategy: keep a timestamp log per rule, and before each request sleep until
    the tightest rule would allow one more hit (staying one under the cap for
    safety). A 429 with ``Retry-After`` overrides everything.
    """

    def __init__(self, min_interval: float = 1.3) -> None:
        self._rules: list[_Bucket] = []
        self._hits: list[float] = []       # monotonic timestamps of past requests
        self._min_interval = min_interval  # floor even if headers are generous
        self._blocked_until = 0.0

    def update_from_headers(self, headers: Any) -> None:
        # e.g. X-Rate-Limit-Ip: "8:10:60,15:60:120"
        rules = headers.get("X-Rate-Limit-Ip") or headers.get("X-Rate-Limit-Account")
        if rules:
            try:
                self._rules = [_Bucket.parse(s) for s in rules.split(",")]
            except ValueError:
                pass

    def note_retry_after(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)

    def acquire(self) -> None:
        now = time.monotonic()
        # honor an active 429 penalty
        if now < self._blocked_until:
            time.sleep(self._blocked_until - now)
            now = time.monotonic()
        # min-interval floor
        if self._hits and now - self._hits[-1] < self._min_interval:
            time.sleep(self._min_interval - (now - self._hits[-1]))
            now = time.monotonic()
        # per-rule: stay strictly under the cap within each window
        for rule in self._rules:
            window_start = now - rule.period
            recent = [t for t in self._hits if t >= window_start]
            if len(recent) >= rule.hits - 1:
                # wait until the oldest in-window hit ages out
                wait = self._hits and (self._hits[0] + rule.period - now) or 0
                if wait > 0:
                    time.sleep(wait)
                    now = time.monotonic()
        self._hits.append(now)
        # keep the log bounded to the longest window we know about
        longest = max((r.period for r in self._rules), default=120)
        self._hits = [t for t in self._hits if t >= now - longest]


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------
@dataclass
class Listing:
    """One trade result, trimmed to what matters for price-checking."""

    price: Optional[str]
    account: Optional[str]
    whisper: Optional[str]
    item_name: str
    raw: dict = field(repr=False, default_factory=dict)


class TradeClient:
    def __init__(self, sessid: Optional[str] = None, min_interval: float = 1.3) -> None:
        self.sessid = sessid or os.environ.get("POE_SESSID")
        self._rl = _RateLimiter(min_interval=min_interval)

    # -- low-level HTTP -----------------------------------------------------
    def _request(self, method: str, url: str, body: Optional[dict] = None,
                 need_auth: bool = False) -> dict:
        if need_auth and not self.sessid:
            raise TradeError("POESESSID required — set POE_SESSID env or pass sessid=")
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.sessid:
            headers["Cookie"] = f"POESESSID={self.sessid}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        self._rl.acquire()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._rl.update_from_headers(resp.headers)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            self._rl.update_from_headers(exc.headers)
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 429:
                retry = float(exc.headers.get("Retry-After", "10"))
                self._rl.note_retry_after(retry)
                raise TradeError(f"rate limited (429) — retry after {retry:g}s") from exc
            if exc.code in (401, 403):
                raise TradeError(f"auth/forbidden ({exc.code}) — POESESSID missing/expired "
                                 f"or Cloudflare block: {detail}") from exc
            raise TradeError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TradeError(f"request failed: {exc}") from exc

    # -- static data (public, no auth) --------------------------------------
    def leagues(self) -> list[dict]:
        return self._request("GET", f"{BASE}/data/leagues")["result"]

    def stats(self) -> list[dict]:
        """The stat vocabulary (explicit.*/implicit.* ids) used to build filters."""
        return self._request("GET", f"{BASE}/data/stats")["result"]

    def items(self) -> list[dict]:
        """Item/base-type vocabulary."""
        return self._request("GET", f"{BASE}/data/items")["result"]

    # -- search + fetch (need POESESSID) ------------------------------------
    def search(self, query: dict, league: str) -> dict:
        """POST a query; returns {id, result:[listingId...], total}."""
        url = f"{BASE}/search/{REALM}/{urllib.parse.quote(league)}"
        return self._request("POST", url, body=query, need_auth=True)

    def fetch(self, ids: list[str], query_id: str) -> list[Listing]:
        """GET listings for up to 10 result ids at a time."""
        out: list[Listing] = []
        for chunk_start in range(0, len(ids), 10):
            chunk = ids[chunk_start:chunk_start + 10]
            url = (f"{BASE}/fetch/{','.join(chunk)}"
                   f"?query={urllib.parse.quote(query_id)}&realm={REALM}")
            res = self._request("GET", url, need_auth=True)
            for r in res.get("result") or []:
                out.append(_to_listing(r))
        return out

    # -- convenience --------------------------------------------------------
    def search_type(self, base_type: str, league: str, top: int = 5,
                    online_only: bool = True) -> list[Listing]:
        """Cheapest listings of a base type — the simplest useful query."""
        query = {
            "query": {
                "status": {"option": "online" if online_only else "any"},
                "type": base_type,
            },
            "sort": {"price": "asc"},
        }
        res = self.search(query, league)
        ids = (res.get("result") or [])[:top]
        if not ids:
            return []
        return self.fetch(ids, res["id"])


def _to_listing(r: dict) -> Listing:
    listing = r.get("listing") or {}
    price = listing.get("price") or {}
    price_str = None
    if price:
        price_str = f"{price.get('amount')} {price.get('currency')}"
    item = r.get("item") or {}
    return Listing(
        price=price_str,
        account=(listing.get("account") or {}).get("name"),
        whisper=listing.get("whisper"),
        item_name=(item.get("name") or item.get("typeLine") or "?").strip(),
        raw=r,
    )


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="PoE2 trade price-check (manual, rate-limited).")
    ap.add_argument("--league", default="Runes of Aldur")
    ap.add_argument("--type", dest="base_type", help="base type to search, e.g. 'Twin Crossbow'")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--leagues", action="store_true", help="just list leagues (public, no auth)")
    args = ap.parse_args()

    client = TradeClient()
    if args.leagues or not args.base_type:
        print("Ligas PoE2:")
        for lg in client.leagues():
            print(" -", lg["id"])
        if not args.base_type:
            return
    print(f"\nBuscando '{args.base_type}' em {args.league} (mais baratos)...")
    try:
        for i, lst in enumerate(client.search_type(args.base_type, args.league, top=args.top), 1):
            print(f" {i}. {lst.price or '?':>18}  {lst.item_name}  (@{lst.account})")
    except TradeError as exc:
        print("ERRO:", exc)


if __name__ == "__main__":
    _main()
