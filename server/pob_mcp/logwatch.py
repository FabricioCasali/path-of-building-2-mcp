"""Tail and parse Path of Exile 2's ``Client.txt`` into structured events.

The game writes a plain-text rolling log (``.../Path of Exile 2/logs/Client.txt``)
that GGG's third-party-tool policy considers fair game to read: it is a file the
client writes itself, no process injection or memory reading involved.

This module turns each interesting line into a typed event dict so a companion
app can track progress (which act/zone, level, deaths), react to it (e.g. pull a
fresh build snapshot and give tips), and gate story/lore spoilers by the exact
beat the player has reached (boss & NPC dialogue lines).

What the log *does* expose: area changes (with area level), level ups, deaths,
instance/login, chat, whispers, trades, AFK/DND, and NPC/boss dialogue.
What it does *not* expose: gear, passive tree, skill gems, HP/mana/position,
quest flags. For those, pair this with the OAuth character API (poe_api.py).

Usage (demo)::

    python -m pob_mcp.logwatch "H:/SteamLibrary/steamapps/common/Path of Exile 2/logs/Client.txt"
    python -m pob_mcp.logwatch <path> --from-start   # replay whole history
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

# --- line grammar ------------------------------------------------------------
# Every line: "YYYY/MM/DD HH:MM:SS <ms> <hex> [<LEVEL> Client <n>] <message>"
# The <hex> is a subsystem id: the message log (chat, whispers, level ups,
# deaths, NPC dialogue) shares one id; engine diagnostics ("Enumerated adapter",
# "Tile hash") use different ones. We learn the message id from unambiguous
# lines to tell NPC dialogue apart from engine chatter that also fits "X: y".
_LINE = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) "
    r"\d+ (?P<sub>[0-9a-f]+) "
    r"\[(?P<level>\w+) Client \d+\] "
    r"(?P<msg>.*)$"
)

# Event kinds that unambiguously come from the message-log subsystem; seeing one
# teaches us that subsystem's hex id.
_MESSAGE_KINDS = frozenset({"chat", "whisper", "level_up", "death"})

# --- message patterns (matched against the message body only) ----------------
_AREA = re.compile(r'^Generating level (?P<lvl>\d+) area "(?P<code>[^"]+)"(?: with seed (?P<seed>\d+))?')
_LEVELUP = re.compile(r"^: (?P<char>.+?) \((?P<cls>[^)]+)\) is now level (?P<lvl>\d+)")
_DEATH = re.compile(r"^: (?P<char>.+?) has been slain\.")
_INSTANCE = re.compile(r"^Connecting to instance server at (?P<ip>[\d.]+):(?P<port>\d+)")
_AFK = re.compile(r'^: AFK mode is now (?P<state>ON|OFF)(?:\. Autoreply "(?P<reply>.*)")?', re.I)
_DND = re.compile(r"^: DND mode is now (?P<state>ON|OFF)", re.I)
# Chat channel prefixes used by PoE: #global $trade %party &guild, @From/@To whisper.
_CHAT = re.compile(r"^(?P<chan>[#$%&])(?P<who>[^:]+): (?P<text>.*)$")
_WHISPER = re.compile(r"^@(?P<dir>From|To) (?P<who>[^:]+): (?P<text>.*)$")
# No-prefix "Speaker: text" — NPC/boss dialogue OR local chat (ambiguous, see below).
_SPEECH = re.compile(r"^(?P<who>[^:@#$%&][^:]*): (?P<text>.+)$")


@dataclass
class Event:
    """One parsed log line. ``kind`` drives everything downstream."""

    kind: str
    ts: datetime
    raw: str
    data: dict = field(default_factory=dict)
    sub: str = ""  # subsystem hex id of the source line

    def __repr__(self) -> str:  # compact, for the demo CLI
        bits = " ".join(f"{k}={v!r}" for k, v in self.data.items())
        return f"[{self.ts:%H:%M:%S}] {self.kind:<12} {bits}"


# --- area classification -----------------------------------------------------
# Area codes are internal ids (G1_1, G2_town, MapCrypt, Sanctum_1_Foyer_1...).
# We can't map every id to its pretty name offline, but we CAN classify the id
# and infer act/difficulty from the campaign prefix. Area *level* comes straight
# from the log line, so progress + spoiler-gating work without a name table.
# The optional NAMES dict below is where you layer in pretty names over time.
_CAMPAIGN = re.compile(r"^(?P<series>[GP])(?P<n>\d+)_")
_TOWN = re.compile(r"(?:_town|_Town)$")

NAMES: dict[str, str] = {
    # Extend freely, e.g. "G1_1": "The Riverbank", "G1_town": "Clearfell Encampment".
}


def classify_area(code: str) -> dict:
    """Return {kind, act, difficulty, is_town, name} for an area code.

    kind: 'town' | 'campaign' | 'map' | 'league' | 'hideout' | 'other'
    """
    name = NAMES.get(code, code)
    is_town = bool(_TOWN.search(code)) or code == "G_Endgame_Town"

    if code == "G_Endgame_Town":
        return {"kind": "town", "act": None, "difficulty": "endgame", "is_town": True, "name": name}

    m = _CAMPAIGN.match(code)
    if m:
        n = int(m.group("n"))
        # G1-G3 = Acts 1-3 (Normal); G4-G6 = Acts 1-3 (Cruel). P-series: legacy/alt
        # campaign ids — act inferred from the digit, difficulty best-effort.
        if m.group("series") == "G":
            act = ((n - 1) % 3) + 1
            difficulty = "normal" if n <= 3 else "cruel"
        else:
            act = n
            difficulty = "unknown"
        return {
            "kind": "town" if is_town else "campaign",
            "act": act,
            "difficulty": difficulty,
            "is_town": is_town,
            "name": name,
        }

    if code.startswith("Map"):
        return {"kind": "map", "act": None, "difficulty": "endgame", "is_town": False, "name": name}

    league_prefixes = ("Abyss", "Chayula", "Delirium", "Expedition", "Incursion", "Sanctum")
    if code.startswith(league_prefixes):
        return {"kind": "league", "act": None, "difficulty": None, "is_town": False, "name": name}
    if code.startswith("Hideout"):
        return {"kind": "hideout", "act": None, "difficulty": None, "is_town": True, "name": name}

    return {"kind": "other", "act": None, "difficulty": None, "is_town": is_town, "name": name}


# NPC/boss speakers look like real names ("Asinia, the Praetor's Consort",
# "The Raven", "Captain Hartlin"): letters/spaces plus , ' . - and a leading
# capital. This deliberately rejects engine noise that also fits "X: y" —
# diagnostics ("[D3D12] Shader Model: ..."), errors ("Error executing GEAL on
# [510]..."), and metadata paths ("Metadata/Monsters/...@54") — via the no-
# digits / no-brackets-slashes-@ constraints. Heuristic, not authoritative:
# it can't tell a boss line from a rare local-chat line by a normally-named
# player, but it removes the pollution that mattered.
_NPC_NAME = re.compile(r"^[A-Z][A-Za-z .,'\-]{1,47}$")


def _looks_like_npc(speaker: str) -> bool:
    if any(ch.isdigit() for ch in speaker):
        return False
    if not _NPC_NAME.match(speaker):
        return False
    return any(ch.islower() for ch in speaker)  # drop ALL-CAPS system tags


def parse_line(line: str, chat_subs: Optional[set] = None) -> Optional[Event]:
    """Parse one raw log line into an ``Event``, or ``None`` if uninteresting.

    ``chat_subs`` is the set of learned message-log subsystem ids. When given,
    a no-prefix ``Speaker: text`` line is only accepted as dialogue if it came
    from a known message subsystem — this rejects engine diagnostics ("Tile
    hash: 1909094995") that share the same shape. Pass ``None`` to skip the
    check (name-heuristic only).
    """
    line = line.rstrip("\n")
    m = _LINE.match(line)
    if not m:
        return None
    ts = datetime.strptime(m.group("ts"), "%Y/%m/%d %H:%M:%S")
    sub = m.group("sub")
    msg = m.group("msg")

    def mk(kind: str, data: dict) -> Event:
        return Event(kind, ts, line, data, sub=sub)

    a = _AREA.match(msg)
    if a:
        info = classify_area(a.group("code"))
        return mk("area", {"code": a.group("code"), "area_level": int(a.group("lvl")), **info})

    lu = _LEVELUP.match(msg)
    if lu:
        return mk("level_up", {"char": lu.group("char"), "cls": lu.group("cls"),
                               "level": int(lu.group("lvl"))})

    d = _DEATH.match(msg)
    if d:
        return mk("death", {"char": d.group("char")})

    inst = _INSTANCE.match(msg)
    if inst:
        return mk("instance", {"ip": inst.group("ip"), "port": int(inst.group("port"))})

    afk = _AFK.match(msg)
    if afk:
        return mk("afk", {"state": afk.group("state").upper(), "reply": afk.group("reply")})

    dnd = _DND.match(msg)
    if dnd:
        return mk("dnd", {"state": dnd.group("state").upper()})

    w = _WHISPER.match(msg)
    if w:
        return mk("whisper", {"dir": w.group("dir").lower(), "who": w.group("who"),
                              "text": w.group("text")})

    c = _CHAT.match(msg)
    if c:
        channel = {"#": "global", "$": "trade", "%": "party", "&": "guild"}[c.group("chan")]
        return mk("chat", {"channel": channel, "who": c.group("who"), "text": c.group("text")})

    s = _SPEECH.match(msg)
    if s:
        # No channel prefix: NPC/boss dialogue OR engine diagnostic that happens
        # to fit "X: y". Require a learned message subsystem when we have one.
        if chat_subs is not None and sub not in chat_subs:
            return None
        speaker = s.group("who")
        return mk("dialogue", {"speaker": speaker, "text": s.group("text"),
                               "likely_npc": _looks_like_npc(speaker)})

    return None


def tail(path: str, from_start: bool = False, poll: float = 1.0) -> Iterator[Event]:
    """Follow ``Client.txt`` and yield events as they are written.

    Survives the game rewriting/rotating the file (detected as the size
    shrinking) by reopening from the top. ``from_start=True`` replays the
    existing content first, then follows.
    """
    while True:
        try:
            f = open(path, "r", encoding="utf-8", errors="replace")
        except FileNotFoundError:
            time.sleep(poll)
            continue
        with f:
            if not from_start:
                f.seek(0, 2)  # jump to EOF; only new lines from here
            pos = f.tell()
            chat_subs: set = set()
            while True:
                line = f.readline()
                if line:
                    ev = parse_line(line, chat_subs)
                    if ev is not None:
                        if ev.kind in _MESSAGE_KINDS:
                            chat_subs.add(ev.sub)  # learn the message subsystem
                        yield ev
                    pos = f.tell()
                    continue
                # No new data: check for truncation/rotation, else wait.
                time.sleep(poll)
                try:
                    if f.tell() > _size(path):
                        break  # file shrank -> reopen
                except OSError:
                    break
                f.seek(pos)


def _size(path: str) -> int:
    import os
    return os.path.getsize(path)


def _collect_chat_subs(path: str) -> set:
    """First pass: learn which subsystem ids carry chat/message lines."""
    subs: set = set()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ev = parse_line(line)  # no gating; we only want message-kind subs
            if ev is not None and ev.kind in _MESSAGE_KINDS:
                subs.add(ev.sub)
    return subs


def replay(path: str) -> Iterator[Event]:
    """Parse the whole existing log once (no following). Handy for backfill.

    Two-pass: learn the message subsystem first so NPC dialogue is separated
    from engine diagnostics even for the earliest lines.
    """
    chat_subs = _collect_chat_subs(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            ev = parse_line(line, chat_subs)
            if ev is not None:
                yield ev


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Parse/follow PoE2 Client.txt into events.")
    ap.add_argument("path", help="path to Client.txt")
    ap.add_argument("--from-start", action="store_true", help="replay existing history, then follow")
    ap.add_argument("--replay-only", action="store_true", help="parse existing history and exit")
    ap.add_argument("--kinds", help="comma-separated event kinds to show (default: all)")
    args = ap.parse_args()

    wanted = set(args.kinds.split(",")) if args.kinds else None

    def show(ev: Event) -> None:
        if wanted is None or ev.kind in wanted:
            print(ev)

    if args.replay_only:
        for ev in replay(args.path):
            show(ev)
        return
    for ev in tail(args.path, from_start=args.from_start):
        show(ev)


if __name__ == "__main__":
    _main()
