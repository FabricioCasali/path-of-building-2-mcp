"""Spoiler-safe lore gate: answer story questions only up to the player's point.

The knowledge of PoE2's story lives in the LLM, not here. This module's job is
to compute, from the game log, an authoritative *spoiler frontier* — how far the
player has actually progressed — plus a journal of the story beats they've
literally witnessed (boss/NPC dialogue lines). It then builds a prompt that
tells the LLM: answer using only lore up to this frontier; deflect anything
beyond it.

Two signals drive the frontier:

* **Progress** — act + difficulty + area level, tracked monotonically (you can't
  un-see Act 3 by walking back to town). Derived from ``area`` events.
* **Beats witnessed** — deduped boss/NPC dialogue (``dialogue`` events flagged
  ``likely_npc``), each tagged with where/when it was seen. This is ground truth
  for "the player has met this character / seen this fight."

Feed it the same events as :mod:`logwatch` / :mod:`advisor`.

Demo::

    python -m pob_mcp.lore "H:/.../logs/Client.txt" --ask "Quem e a Rainha?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from . import logwatch
from .logwatch import Event

# Monotonic ordering of difficulties. Endgame maps sit above the campaign.
_DIFFICULTY_RANK = {"normal": 0, "cruel": 1, "unknown": 1, "endgame": 2}


@dataclass(frozen=True)
class Frontier:
    """How far the player has progressed — the spoiler ceiling."""

    difficulty: str = "normal"
    act: int = 1
    area_level: int = 1
    area_code: Optional[str] = None
    kind: Optional[str] = None  # campaign | town | map | league | ...

    def _key(self) -> tuple:
        return (_DIFFICULTY_RANK.get(self.difficulty, 0), self.act or 0, self.area_level or 0)

    def is_ahead_of(self, other: "Frontier") -> bool:
        return self._key() > other._key()

    def label(self) -> str:
        if self.kind == "map" or self.difficulty == "endgame":
            return f"Endgame / mapas (área nível {self.area_level})"
        diff = {"normal": "Normal", "cruel": "Cruel"}.get(self.difficulty, self.difficulty)
        return f"Ato {self.act} {diff} (área nível {self.area_level})"


@dataclass
class Beat:
    """A story beat the player literally saw in-game."""

    speaker: str
    text: str
    ts: datetime
    frontier_label: str

    def key(self) -> tuple:
        return (self.speaker, self.text)


class LoreGate:
    """Tracks the spoiler frontier and witnessed beats; builds gated prompts."""

    def __init__(self) -> None:
        self.frontier = Frontier()
        self._beats: list[Beat] = []
        self._seen: set[tuple] = set()
        self._bosses: dict[str, int] = {}  # speaker -> times heard

    def feed(self, ev: Event) -> None:
        d = ev.data
        if ev.kind == "area":
            cand = Frontier(
                difficulty=d.get("difficulty") or "normal",
                act=d.get("act") or self.frontier.act,
                area_level=d.get("area_level") or self.frontier.area_level,
                area_code=d.get("code"),
                kind=d.get("kind"),
            )
            # Only advance the ceiling; backtracking to town never lowers it.
            if cand.is_ahead_of(self.frontier):
                self.frontier = cand
            else:
                # keep latest area_code/kind for context without moving the key
                self.frontier = Frontier(
                    difficulty=self.frontier.difficulty, act=self.frontier.act,
                    area_level=self.frontier.area_level, area_code=d.get("code"),
                    kind=d.get("kind") or self.frontier.kind,
                )
        elif ev.kind == "dialogue" and d.get("likely_npc"):
            beat = Beat(d["speaker"], d["text"], ev.ts, self.frontier.label())
            self._bosses[beat.speaker] = self._bosses.get(beat.speaker, 0) + 1
            if beat.key() not in self._seen:
                self._seen.add(beat.key())
                self._beats.append(beat)

    # --- queries -------------------------------------------------------------
    def characters_met(self) -> list[str]:
        """Distinct NPC/boss speakers the player has heard, most-heard first."""
        return [name for name, _ in sorted(self._bosses.items(), key=lambda kv: -kv[1])]

    def journal(self, limit: int = 15) -> list[Beat]:
        return self._beats[-limit:]

    def boundary_text(self) -> str:
        met = self.characters_met()
        met_str = ", ".join(met[:20]) if met else "(nenhum registrado no log)"
        return (
            f"FRONTEIRA DE PROGRESSO DO JOGADOR: {self.frontier.label()}.\n"
            f"PERSONAGENS/BOSSES QUE O JOGADOR JÁ ENCONTROU (fala capturada no log): {met_str}."
        )

    def build_prompt(self, question: str) -> dict:
        """Return a spoiler-safe prompt bundle for an LLM to answer ``question``.

        The LLM supplies the lore; this constrains *how much* it may reveal.
        """
        recent = self._beats[-8:]
        journal = "\n".join(f"  - [{b.frontier_label}] {b.speaker}: {b.text}" for b in recent) \
            or "  (sem falas de NPC capturadas ainda)"
        system = (
            "Você é um lore-master de Path of Exile 2 que responde SEM SPOILER. "
            "Regra absoluta: só revele elementos da história ATÉ a fronteira de progresso "
            "informada. Se a pergunta exigir algo além dela, diga que está adiante do ponto "
            "atual do jogador e ofereça só o que é seguro, sem entregar o que vem depois. "
            "Prefira ancorar a resposta no que o jogador comprovadamente já viu."
        )
        context = (
            f"{self.boundary_text()}\n\n"
            f"BEATS RECENTES QUE O JOGADOR VIU (mais recentes):\n{journal}"
        )
        return {"system": system, "context": context, "question": question,
                "frontier": self.frontier.label()}


def _main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Spoiler-safe lore gate from Client.txt.")
    ap.add_argument("path")
    ap.add_argument("--ask", help="a lore question to build a gated prompt for")
    ap.add_argument("--journal", type=int, default=10, help="how many recent beats to show")
    args = ap.parse_args()

    gate = LoreGate()
    for ev in logwatch.replay(args.path):
        gate.feed(ev)

    print("== " + gate.boundary_text())
    print("\n== Diário de história (últimos beats vistos) ==")
    for b in gate.journal(args.journal):
        print(f"  [{b.ts:%m-%d %H:%M}] ({b.frontier_label}) {b.speaker}: {b.text[:70]}")

    if args.ask:
        bundle = gate.build_prompt(args.ask)
        print("\n== Prompt gated para o LLM ==")
        print("[system]\n" + bundle["system"])
        print("\n[context]\n" + bundle["context"])
        print("\n[question] " + bundle["question"])


if __name__ == "__main__":
    _main()
