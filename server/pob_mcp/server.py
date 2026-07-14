"""MCP server exposing Path of Building 2 build calculations to Claude.

Tools accept a PoB2 share ``code`` (the base64 blob players trade) OR raw build
``xml``. Share codes are decoded here in Python (base64 + zlib) since the
headless engine has those stubbed; raw XML is handed straight to the engine.

The engine is a single persistent, stateful process. This server remembers the
last-loaded build, so you can ``import_build`` once and then call the stat /
optimize / simulate tools without re-passing the code every time. Simulation
tools always restore the build afterwards, so the loaded build is never mutated
from the caller's point of view.
"""

from __future__ import annotations

import threading
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import optimizer, poe_api, poe_oauth
from .engine import PobEngine
from .fetch import FetchError, fetch_build_source, looks_like_url, looks_like_xml
from .share_code import ShareCodeError, decode_share_code

mcp = FastMCP("path-of-building-2")

_engine: PobEngine | None = None
_engine_lock = threading.Lock()
_current_xml: str | None = None  # XML of the build currently loaded in the engine


def _get_engine() -> PobEngine:
    """Start the shared engine lazily on first use (thread-safe)."""
    global _engine
    with _engine_lock:
        if _engine is None:
            eng = PobEngine()
            eng.start()
            _engine = eng
        return _engine


def _resolve_xml(code: str | None, xml: str | None) -> str:
    if xml and xml.strip():
        return xml
    if code and code.strip():
        source = code.strip()
        # A link (e.g. pobb.in/xxxx) is downloaded here since the engine has no
        # network; the fetched body is then either a share code or raw XML.
        if looks_like_url(source):
            try:
                source = fetch_build_source(source)
            except FetchError as exc:
                raise ValueError(f"could not fetch build URL: {exc}") from exc
        if looks_like_xml(source):
            return source
        try:
            return decode_share_code(source)
        except ShareCodeError as exc:
            raise ValueError(f"could not decode share code: {exc}") from exc
    raise ValueError("provide either a share 'code' or raw build 'xml'")


def _ensure_loaded(code: str | None, xml: str | None) -> str:
    """Load the build if code/xml is given (and remember it); else use the last one.

    Returns the XML of the now-current build (needed by simulation tools to
    restore). Raises if nothing is loaded and nothing was provided.
    """
    global _current_xml
    if (code and code.strip()) or (xml and xml.strip()):
        build_xml = _resolve_xml(code, xml)
        _get_engine().call("import_build", xml=build_xml)
        _current_xml = build_xml
    elif _current_xml is None:
        raise ValueError("no build loaded yet — pass a share 'code' or raw 'xml' first")
    return _current_xml


# --------------------------------------------------------------------------
# Load / read tools
# --------------------------------------------------------------------------

@mcp.tool()
def import_build(code: str | None = None, xml: str | None = None) -> dict[str, Any]:
    """Load a PoB2 build and make it the active build for later tool calls.

    Returns a summary (class, ascendancy, level, main skill). After this you can
    call the other tools without re-passing the code.

    Args:
        code: A PoB2 build reference — any of: a share link (e.g.
            "https://pobb.in/xxxx", downloaded automatically), a raw pobb.in
            "/raw" URL, or the base64 share code itself. Passing the link is
            preferred; it avoids copy/paste corruption of the long code.
        xml: Raw build XML, as an alternative to a share code.
    """
    _ensure_loaded(code, xml)
    return _get_engine().call("import_build", xml=_current_xml)


@mcp.tool()
def calc_stats(code: str | None = None, xml: str | None = None) -> dict[str, Any]:
    """Core stats of the active build: DPS, crit, life/mana/ES, resistances, EHP.

    Args:
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    _ensure_loaded(code, xml)
    return _get_engine().call("calc_stats")


@mcp.tool()
def get_defenses(code: str | None = None, xml: str | None = None) -> dict[str, Any]:
    """Defensive layer of the active build: pools, armour/evasion/block, max hit taken per damage type, EHP.

    Args:
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    _ensure_loaded(code, xml)
    return _get_engine().call("get_defenses")


@mcp.tool()
def list_skills(code: str | None = None, xml: str | None = None) -> dict[str, Any]:
    """List the build's skill (socket) groups, their gems, and which group is the main DPS skill.

    Args:
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    _ensure_loaded(code, xml)
    return _get_engine().call("list_skills")


@mcp.tool()
def list_items(code: str | None = None, xml: str | None = None) -> dict[str, Any]:
    """List the item equipped in each gear slot, with socket/rune info.

    Args:
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    _ensure_loaded(code, xml)
    return _get_engine().call("list_items")


@mcp.tool()
def get_equipped(slot: str, code: str | None = None, xml: str | None = None) -> dict[str, Any]:
    """Return the raw item text of whatever is equipped in a slot (use it as a template to edit).

    Args:
        slot: Slot name, e.g. "Weapon 1", "Body Armour", "Ring 1".
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    _ensure_loaded(code, xml)
    return _get_engine().call("get_equipped", slot=slot)


# --------------------------------------------------------------------------
# What-if / simulation tools (restore the build afterwards)
# --------------------------------------------------------------------------

@mcp.tool()
def set_config(
    custom_mods: str | list[str],
    code: str | None = None,
    xml: str | None = None,
) -> dict[str, Any]:
    """Apply custom modifiers to the active build and return the recalculated stats.

    Use PoB's config wording, e.g. "100% increased maximum Mana", "Enemy is
    Shocked", "+2 to Level of all Skills". This mutates the active build's config
    (it is not auto-restored) — reload to clear.

    Args:
        custom_mods: One mod string, or a list of mod strings.
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    _ensure_loaded(code, xml)
    return _get_engine().call("set_config", customMods=custom_mods)


@mcp.tool()
def simulate_item(
    slot: str,
    raw: str,
    code: str | None = None,
    xml: str | None = None,
) -> dict[str, Any]:
    """Equip a raw item in a slot and report the stat delta versus the current build.

    The build is restored afterwards, so this is a pure what-if. Paste the item
    text copied from the game or from PoB. Tip: call get_equipped to get the
    current item as a template.

    Args:
        slot: Slot name, e.g. "Weapon 1", "Body Armour", "Ring 1".
        raw: Raw item text to equip.
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    build_xml = _ensure_loaded(code, xml)
    return optimizer.simulate_item(_get_engine(), build_xml, slot, raw)


@mcp.tool()
def list_runes(slot: str, code: str | None = None, xml: str | None = None) -> dict[str, Any]:
    """List the runes / soul cores valid for the item equipped in a slot, and what's socketed now.

    Args:
        slot: Slot name, e.g. "Weapon 1", "Helmet".
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    _ensure_loaded(code, xml)
    return _get_engine().call("list_valid_runes", slot=slot)


# --------------------------------------------------------------------------
# Optimizers (scan-and-rank)
# --------------------------------------------------------------------------

@mcp.tool()
def optimize_supports(
    group: int | None = None,
    top: int = 12,
    include_lineage: bool = False,
    code: str | None = None,
    xml: str | None = None,
) -> dict[str, Any]:
    """Find the best support-gem upgrades for a skill, ranked by DPS gain.

    Measures each currently-socketed support's contribution to find the weakest
    slot, then tries every compatible support in that slot and ranks the gains.
    The build is restored afterwards.

    Args:
        group: Skill group index (from list_skills). Omit for the main DPS group.
        top: How many top replacements to return.
        include_lineage: Include unique/rare "lineage" supports (harder to get).
            Default False shows only common, buyable gems.
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    build_xml = _ensure_loaded(code, xml)
    return optimizer.optimize_supports(
        _get_engine(), build_xml, group=group, top=top, include_item_supports=include_lineage
    )


@mcp.tool()
def optimize_runes(
    slot: str,
    rune_index: int = 1,
    top: int = 12,
    code: str | None = None,
    xml: str | None = None,
) -> dict[str, Any]:
    """Try every valid rune / soul core in one socket of an equipped item, ranked by DPS gain.

    The build is restored afterwards.

    Args:
        slot: Slot name of the item to test, e.g. "Weapon 1".
        rune_index: Which socket (1-based) to vary.
        top: How many top runes to return.
        code: Optional share code to load first; omit to use the active build.
        xml: Optional raw XML to load first.
    """
    build_xml = _ensure_loaded(code, xml)
    return optimizer.optimize_runes(_get_engine(), build_xml, slot=slot, rune_index=rune_index, top=top)


# --------------------------------------------------------------------------
# Account login + live character import (OAuth, api.pathofexile.com)
# --------------------------------------------------------------------------

@mcp.tool()
def poe_login_start() -> dict[str, Any]:
    """Begin logging in to your pathofexile.com account (OAuth).

    Returns an ``authorize_url``. Open it in a browser and approve access; the
    login is captured by a local listener. Then call ``poe_login_finish`` to
    complete. (An assistant with browser automation can open the URL for you.)
    """
    info = poe_oauth.start_login()
    return {
        "authorize_url": info["authorize_url"],
        "instructions": "Open authorize_url in a browser, approve access, then "
                        "call poe_login_finish. The redirect is caught automatically.",
    }


@mcp.tool()
def poe_login_finish(timeout: float = 180.0) -> dict[str, Any]:
    """Finish the login started by ``poe_login_start`` (waits for the redirect).

    Args:
        timeout: Seconds to wait for you to approve in the browser.
    """
    try:
        return poe_oauth.finish_login(timeout=timeout)
    except poe_oauth.OAuthError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def poe_logout() -> dict[str, Any]:
    """Forget the current account session."""
    poe_oauth.logout()
    return {"logged_in": False}


@mcp.tool()
def list_characters() -> dict[str, Any]:
    """List the PoE2 characters on the logged-in account (name, level, class, league)."""
    try:
        return {"characters": poe_api.list_characters()}
    except (poe_api.PoEApiError, poe_oauth.OAuthError) as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
def import_character(name: str) -> dict[str, Any]:
    """Import a live character from your account and make it the active build.

    Requires a login (``poe_login_start`` / ``poe_login_finish``). Fetches the
    character off the API and hands it to the engine's native PoB importer.

    Args:
        name: Character name (see ``list_characters``).
    """
    global _current_xml
    try:
        raw = poe_api.fetch_character_raw(name)
    except (poe_api.PoEApiError, poe_oauth.OAuthError) as exc:
        raise ValueError(str(exc)) from exc
    result = _get_engine().call("import_character", json=raw)
    # The engine hands back the composed XML so later tools have a build to
    # restore to (optimizers) and we can round-trip to a share code.
    _current_xml = result.pop("xml", None)
    return result


@mcp.tool()
def compare_builds(code_a: str, code_b: str) -> dict[str, Any]:
    """Compare two builds side by side on their headline offensive and defensive numbers.

    Args:
        code_a: Share code (or raw XML) for the first build.
        code_b: Share code (or raw XML) for the second build.
    """
    engine = _get_engine()
    engine.call("import_build", xml=_resolve_xml(code_a, None))
    a = engine.call("calc_stats")
    engine.call("import_build", xml=_resolve_xml(code_b, None))
    b = engine.call("calc_stats")

    def pick(stats: dict[str, Any]) -> dict[str, Any]:
        off, deff = stats.get("offense", {}), stats.get("defense", {})
        return {
            "TotalDPS": off.get("TotalDPS"),
            "Life": deff.get("Life"),
            "Mana": deff.get("Mana"),
            "EnergyShield": deff.get("EnergyShield"),
            "TotalEHP": deff.get("TotalEHP"),
        }

    flat_a, flat_b = pick(a), pick(b)
    delta = {
        k: flat_b[k] - flat_a[k]
        for k in flat_a
        if isinstance(flat_a[k], (int, float)) and isinstance(flat_b[k], (int, float))
    }
    # leave the second build loaded; clear remembered state to avoid confusion
    global _current_xml
    _current_xml = _resolve_xml(code_b, None)
    return {"a": {"summary": a, "key": flat_a}, "b": {"summary": b, "key": flat_b}, "delta": delta}


def main() -> None:
    """Run the MCP server over stdio (default transport for Claude Code)."""
    try:
        mcp.run()
    finally:
        if _engine is not None:
            _engine.close()


if __name__ == "__main__":
    main()
