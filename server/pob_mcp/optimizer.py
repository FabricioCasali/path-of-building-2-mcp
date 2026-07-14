"""DPS optimization and what-if simulation on top of the PoB2 engine.

The engine holds a single, stateful build. Every simulation follows the same
save-mutate-measure-restore shape: we snapshot by keeping the original build XML
and re-import it to roll back to a clean state after each trial. Re-import is
cheap (~0.1-0.5s) relative to how much certainty it buys.

These helpers take an already-loaded `engine` (a PobEngine) plus the `xml` used
to load it, so they can restore precisely.
"""

from __future__ import annotations

from typing import Any, Callable

from .engine import EngineError, PobEngine


def _restore(engine: PobEngine, xml: str) -> None:
    engine.call("import_build", xml=xml)


def _main_dps(engine: PobEngine) -> float:
    return engine.call("list_skills").get("mainDPS") or 0.0


def optimize_supports(
    engine: PobEngine,
    xml: str,
    group: int | None = None,
    top: int = 12,
    include_item_supports: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Find the best support-gem swaps for a skill group, ranked by DPS gain.

    Strategy: measure each currently-socketed support's own contribution
    (remove it, see the drop) to find the weakest slot, then try every
    compatible support the build isn't already using in that slot and rank the
    result by DPS delta versus the starting build.

    Returns the weakest-slot analysis plus a ranked list of replacements.
    """
    _restore(engine, xml)
    base = _main_dps(engine)
    skills = engine.call("list_skills")
    grp = group or skills.get("mainSocketGroup") or 1
    group_info = next((g for g in skills["groups"] if g["index"] == grp), None)
    if group_info is None:
        raise EngineError(f"group {grp} not found")

    supports = [(i + 1, g["name"]) for i, g in enumerate(group_info["gems"]) if g.get("support")]

    # 1) contribution of each socketed support
    contributions = []
    for gem_index, name in supports:
        _restore(engine, xml)
        after = engine.call("remove_gem", group=grp, gemIndex=gem_index)["mainDPS"]
        contributions.append({"gemIndex": gem_index, "name": name, "contribution": base - after})
    _restore(engine, xml)

    # weakest currently-socketed slot (lowest, possibly negative, contribution)
    weakest = min(contributions, key=lambda c: c["contribution"]) if contributions else None

    # 2) enumerate compatible supports not already socketed
    comp = engine.call("list_compatible_supports", group=grp)
    candidates = [s for s in comp["supports"] if not s["alreadySocketed"]]

    ranked: list[dict[str, Any]] = []
    if weakest is not None:
        # swap_gem replaces the gem at the same index in place, so we only need
        # ONE restore up front; each candidate overwrites the previous trial's
        # gem. This avoids a per-candidate re-import (the slow part) and keeps
        # exactly one support swapped at that index throughout the scan.
        _restore(engine, xml)
        for idx, s in enumerate(candidates):
            try:
                r = engine.call("swap_gem", group=grp, gemIndex=weakest["gemIndex"], name=s["name"])
            except EngineError:
                continue  # some names don't resolve cleanly; skip
            ranked.append({
                "support": s["name"],
                "delta": (r["mainDPS"] or 0) - base,
                "newDPS": r["mainDPS"],
                "lineage": s.get("lineage", False),
            })
            if progress and idx % 25 == 0:
                progress(f"scanned {idx + 1}/{len(candidates)} supports")
        _restore(engine, xml)

    ranked.sort(key=lambda r: r["delta"], reverse=True)
    if not include_item_supports:
        # drop lineage (unique/rare) supports unless explicitly asked for
        ranked = [r for r in ranked if not r.get("lineage")]

    return {
        "baseDPS": base,
        "group": grp,
        "activeSkill": group_info.get("activeSkill"),
        "socketedContributions": sorted(contributions, key=lambda c: c["contribution"]),
        "weakestSlot": weakest,
        "candidatesScanned": len(candidates),
        "topReplacements": ranked[:top],
    }


def simulate_item(engine: PobEngine, xml: str, slot: str, raw: str) -> dict[str, Any]:
    """Equip a raw item in a slot and report the stat delta versus the current build."""
    _restore(engine, xml)
    before = engine.call("calc_stats")
    result = engine.call("equip_item", slot=slot, raw=raw)
    _restore(engine, xml)  # roll back so the loaded build is unchanged afterwards
    return {
        "slot": slot,
        "equipped": result.get("equipped"),
        "before": _key_stats(before),
        "after": _key_stats(result),
        "delta": _delta(before, result),
    }


def optimize_runes(
    engine: PobEngine,
    xml: str,
    slot: str,
    rune_index: int = 1,
    top: int = 12,
) -> dict[str, Any]:
    """Try every valid rune/soul core in one socket of the equipped item, ranked by DPS."""
    _restore(engine, xml)
    base = _main_dps(engine)
    valid = engine.call("list_valid_runes", slot=slot)
    ranked: list[dict[str, Any]] = []
    # set_rune overwrites the same socket each time, so one restore up front is
    # enough — no per-candidate re-import (the slow part).
    for r in valid.get("runes", []):
        try:
            res = engine.call("set_rune", slot=slot, runeIndex=rune_index, rune=r["name"])
        except EngineError:
            continue
        ranked.append({"rune": r["name"], "type": r.get("type"), "delta": (res["mainDPS"] or 0) - base, "newDPS": res["mainDPS"]})
    _restore(engine, xml)
    ranked.sort(key=lambda r: r["delta"], reverse=True)
    return {
        "baseDPS": base,
        "slot": slot,
        "runeIndex": rune_index,
        "current": valid.get("current"),
        "socketCount": valid.get("socketCount"),
        "topRunes": ranked[:top],
    }


# --- small stat helpers -------------------------------------------------

def _key_stats(stats: dict[str, Any]) -> dict[str, Any]:
    off = stats.get("offense", {})
    deff = stats.get("defense", {})
    return {
        "TotalDPS": off.get("TotalDPS"),
        "CombinedDPS": off.get("CombinedDPS"),
        "Life": deff.get("Life"),
        "Mana": deff.get("Mana"),
        "EnergyShield": deff.get("EnergyShield"),
        "TotalEHP": deff.get("TotalEHP"),
        "FireResist": deff.get("FireResist"),
        "ColdResist": deff.get("ColdResist"),
        "LightningResist": deff.get("LightningResist"),
        "ChaosResist": deff.get("ChaosResist"),
    }


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    a, b = _key_stats(before), _key_stats(after)
    out = {}
    for k in a:
        if isinstance(a[k], (int, float)) and isinstance(b[k], (int, float)):
            out[k] = b[k] - a[k]
    return out
