"""Live coaching loop: watch the game log, snapshot the build, give tips.

Closes the loop the companion app is built around:

    Client.txt (logwatch) --event--> ProgressState --trigger--> build snapshot
        (import_character + calc_stats + get_defenses) --> rule checks --> Advice

The log tells us *where* the player is and *what just happened* (act, level,
deaths). It does NOT contain gear/tree/resists, so on a meaningful trigger we
pull a fresh live snapshot through the same code the MCP server uses, then run
grounded rule checks (uncapped resists, death streaks, level/act pacing).

The rules here are deliberately conservative and factual. Anything requiring
judgement (build-specific advice, lore) is meant to be layered on top by an LLM
consuming ``Advisor.context()`` — this module never guesses.

Demo::

    python -m pob_mcp.advisor "H:/.../logs/Client.txt"
    python -m pob_mcp.advisor <path> --no-snapshot   # log-only, no engine/API
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from . import logwatch
from .logwatch import Event

# --- tunables ----------------------------------------------------------------
RES_TARGET = 75            # elemental resist cap; anything below is a hole
CHAOS_FLOOR = 0            # chaos below this is flagged (PoE2 chaos res is rare/negative)
LEVEL_MILESTONE = 5        # snapshot every N levels
DEATH_STREAK_N = 3         # this many deaths...
DEATH_STREAK_WINDOW = timedelta(minutes=10)   # ...within this window -> alert

# Areas whose entry is worth a snapshot even without a level milestone: act
# towns (you just finished/started an act) and known boss/league beats.
_BOSS_AREA_HINTS = ("UberBoss", "HungerBoss", "_town", "Foyer")


@dataclass
class Advice:
    """One actionable tip, with a severity so the UI can rank/colour it."""

    severity: str   # 'info' | 'warn' | 'critical'
    topic: str      # 'resist' | 'survivability' | 'pacing' | 'progress'
    message: str


@dataclass
class ProgressState:
    """Everything we've learned about the current play session from the log."""

    character: Optional[str] = None
    char_class: Optional[str] = None
    level: Optional[int] = None
    area_code: Optional[str] = None
    area_level: Optional[int] = None
    act: Optional[int] = None
    difficulty: Optional[str] = None
    area_kind: Optional[str] = None
    last_snapshot_level: int = 0
    recent_deaths: list[datetime] = field(default_factory=list)
    last_boss_line: Optional[str] = None   # furthest story beat seen (for lore-gate)
    # When set, level-ups/deaths for names NOT in here are ignored: the log also
    # records party members, and only the account owner's characters can be
    # snapshotted via the API. None = accept everyone (no account context).
    own_characters: Optional[set] = None

    def _is_own(self, name: str) -> bool:
        return self.own_characters is None or name in self.own_characters

    def _switch_character(self, name: str) -> None:
        """Reset per-character counters when the played character changes.

        Level, death streak and the snapshot baseline belong to one character;
        without this, alternating characters bleeds one's level/deaths into the
        other's pacing and streak checks.
        """
        if name != self.character:
            self.character = name
            self.char_class = None
            self.level = None
            self.recent_deaths = []
            self.last_snapshot_level = 0

    def apply(self, ev: Event) -> None:
        d = ev.data
        if ev.kind == "level_up":
            if not self._is_own(d["char"]):
                return  # a party member's level-up, not the player's
            self._switch_character(d["char"])
            self.char_class, self.level = d["cls"], d["level"]
        elif ev.kind == "area":
            self.area_code, self.area_level = d["code"], d["area_level"]
            self.act, self.difficulty, self.area_kind = d["act"], d["difficulty"], d["kind"]
        elif ev.kind == "death":
            if not self._is_own(d["char"]):
                return  # a party member died, not the player
            self._switch_character(d["char"])  # no-op unless a different char died
            # keep only deaths inside the streak window
            self.recent_deaths = [t for t in self.recent_deaths if ev.ts - t <= DEATH_STREAK_WINDOW]
            self.recent_deaths.append(ev.ts)
        elif ev.kind == "dialogue" and d.get("likely_npc"):
            self.last_boss_line = f"{d['speaker']}: {d['text']}"


# A snapshot function returns the merged stats dict, or None if unavailable.
SnapshotFn = Callable[[str], Optional[dict]]


def own_character_names() -> Optional[set]:
    """Names of the account's own characters, to filter out party members.

    Best-effort: returns None if not logged in / API unavailable, in which case
    the advisor tracks everyone (no filtering).
    """
    try:
        from . import poe_api
        return {c["name"] for c in poe_api.list_characters()}
    except Exception:  # noqa: BLE001
        return None


def live_snapshot(character: str) -> Optional[dict]:
    """Pull a live build snapshot via the MCP server internals.

    Reuses the exact functions the MCP exposes (same engine + OAuth token), so
    no MCP round-trip. Returns {'stats':..., 'defenses':...} or None on failure
    (not logged in, engine down, character not found) — the loop degrades to
    log-only advice in that case.
    """
    try:
        from . import server  # lazy: keeps advisor importable without the engine
        server.import_character(character)
        return {"stats": server.calc_stats(), "defenses": server.get_defenses()}
    except Exception as exc:  # noqa: BLE001 - advice must never crash the loop
        return {"error": str(exc)}


# --- rule checks -------------------------------------------------------------
def check_resists(stats: dict) -> list[Advice]:
    out: list[Advice] = []
    deff = stats.get("defense", {})
    elems = {"Fire": deff.get("FireResist"), "Cold": deff.get("ColdResist"),
             "Lightning": deff.get("LightningResist")}
    holes = {k: v for k, v in elems.items() if isinstance(v, (int, float)) and v < RES_TARGET}
    for name, val in sorted(holes.items(), key=lambda kv: kv[1]):
        gap = RES_TARGET - val
        sev = "critical" if gap >= 20 else "warn"
        out.append(Advice(sev, "resist",
                          f"{name} resist em {val:g} (faltam {gap:g} p/ o cap {RES_TARGET})."))
    chaos = deff.get("ChaosResist")
    if isinstance(chaos, (int, float)) and chaos < CHAOS_FLOOR:
        out.append(Advice("warn", "resist", f"Chaos resist negativo ({chaos:g})."))
    return out


def check_survivability(stats: dict, state: ProgressState) -> list[Advice]:
    out: list[Advice] = []
    deff = stats.get("defense", {})
    life, es = deff.get("Life"), deff.get("EnergyShield")
    pool = (life or 0) + (es or 0)
    # Rough campaign floor: ~ area_level * 40 hp+es is a lenient "don't get oneshot"
    # line during the campaign. Best-effort, flagged as guidance not gospel.
    if state.area_level and pool and pool < state.area_level * 40:
        out.append(Advice("warn", "survivability",
                          f"Pool de vida+ES {pool:g} baixo p/ área nível {state.area_level} "
                          f"(referência folgada ~{state.area_level*40})."))
    return out


def check_death_streak(state: ProgressState) -> list[Advice]:
    if len(state.recent_deaths) >= DEATH_STREAK_N:
        n = len(state.recent_deaths)
        return [Advice("critical", "survivability",
                       f"{n} mortes em {DEATH_STREAK_WINDOW.seconds//60} min — revisar defesas "
                       f"(get_defenses: maior hit por tipo) antes de insistir.")]
    return []


def check_pacing(state: ProgressState) -> list[Advice]:
    """Very rough under-levelling check vs the area level."""
    if state.level and state.area_level and state.level + 5 < state.area_level:
        return [Advice("warn", "pacing",
                       f"Personagem nível {state.level} vs área nível {state.area_level} "
                       f"— sub-levelado, cuidado.")]
    return []


class Advisor:
    """Feed it log events; it yields Advice when a trigger fires."""

    def __init__(self, snapshot: SnapshotFn | None = live_snapshot,
                 own_characters: Optional[set] = None):
        self.state = ProgressState(own_characters=own_characters)
        self._snapshot = snapshot
        self._last_area_code: Optional[str] = None
        self._last_sig: Optional[tuple] = None

    def _should_trigger(self, ev: Event) -> Optional[str]:
        """Return a human reason string if this event warrants a snapshot+check."""
        if ev.kind == "level_up":
            # apply() ignores party members' level-ups, leaving state untouched;
            # only trigger for the tracked character.
            if ev.data.get("char") != self.state.character or self.state.level is None:
                return None
            if self.state.level - self.state.last_snapshot_level >= LEVEL_MILESTONE:
                return f"subiu para o nível {self.state.level}"
        elif ev.kind == "death":
            if ev.data.get("char") != self.state.character:
                return None
            if len(self.state.recent_deaths) >= DEATH_STREAK_N:
                return "sequência de mortes"
        elif ev.kind == "area":
            code = ev.data["code"]
            if code != self._last_area_code:
                self._last_area_code = code
                if any(h in code for h in _BOSS_AREA_HINTS):
                    return f"entrou em {code}"
        return None

    def feed(self, ev: Event) -> Optional[dict]:
        """Update state; if a trigger fires, return an advice bundle, else None."""
        self.state.apply(ev)
        reason = self._should_trigger(ev)
        if not reason:
            return None

        # Mark this level as handled as soon as it triggers, so the milestone
        # doesn't re-fire on every subsequent level (even when snapshot is off).
        if ev.kind == "level_up":
            self.state.last_snapshot_level = self.state.level

        advice: list[Advice] = check_death_streak(self.state) + check_pacing(self.state)
        snap = None
        if self._snapshot and self.state.character:
            snap = self._snapshot(self.state.character)
            if snap and "error" not in snap:
                stats = snap.get("stats", {})
                advice += check_resists(stats)
                advice += check_survivability(stats, self.state)

        # Dedup area triggers: re-entering town shouldn't repeat identical advice.
        # Level/death triggers always fire (they're events worth surfacing again).
        sig = tuple((a.topic, a.message) for a in advice)
        if ev.kind == "area" and sig == self._last_sig:
            return None
        self._last_sig = sig

        # context() is a point-in-time copy so a caller can keep the bundle
        # without it mutating as later events arrive (state is a live object).
        return {"reason": reason, "context": self.context(), "advice": advice, "snapshot": snap}

    def context(self) -> dict:
        """Compact JSON blob an LLM can turn into richer, build-specific tips/lore."""
        s = self.state
        return {
            "character": s.character, "class": s.char_class, "level": s.level,
            "act": s.act, "difficulty": s.difficulty, "area": s.area_code,
            "area_level": s.area_level, "area_kind": s.area_kind,
            "recent_deaths": len(s.recent_deaths),
            "furthest_story_beat": s.last_boss_line,
        }


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Live PoE2 coaching from Client.txt.")
    ap.add_argument("path")
    ap.add_argument("--no-snapshot", action="store_true",
                    help="don't call the engine/API; log-only advice (deaths, pacing)")
    ap.add_argument("--from-start", action="store_true", help="replay history first")
    args = ap.parse_args()

    own = None if args.no_snapshot else own_character_names()
    adv = Advisor(snapshot=None if args.no_snapshot else live_snapshot, own_characters=own)
    who = f"{len(own)} personagens da conta" if own else "todos (sem filtro de conta)"
    print(f"[advisor] seguindo {args.path} (snapshot={'off' if args.no_snapshot else 'on'}, "
          f"rastreando {who})\n")
    for ev in logwatch.tail(args.path, from_start=args.from_start):
        bundle = adv.feed(ev)
        if not bundle:
            continue
        c = bundle["context"]
        loc = f"Ato {c['act']} {c['difficulty']}" if c["act"] else (c["area_kind"] or "?")
        lvl = c["level"] if c["level"] is not None else "?"
        print(f"── gatilho: {bundle['reason']}  |  {c['character']} nv{lvl}  "
              f"{loc} (área {c['area_level']})")
        snap = bundle["snapshot"]
        if snap and "error" in snap:
            print(f"   (snapshot indisponível: {snap['error']})")
        if not bundle["advice"]:
            print("   ✓ nada crítico.")
        for a in bundle["advice"]:
            icon = {"info": "•", "warn": "⚠", "critical": "‼"}.get(a.severity, "•")
            print(f"   {icon} [{a.topic}] {a.message}")
        print()


if __name__ == "__main__":
    _main()
