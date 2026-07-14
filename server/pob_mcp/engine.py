"""Manage a persistent PoB2 headless engine process and talk to it over JSON.

The engine (``fork/src/mcp_entry.lua``) boots once inside the prebuilt Docker
image and serves a line-based JSON protocol on stdin/stdout. Booting loads the
whole passive tree + item data, so it's expensive — we keep ONE process hot and
funnel every tool call through it.

stdout carries only JSON responses; all engine chatter goes to stderr, which we
drain in a background thread (and watch for the readiness sentinel).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

__all__ = ["PobEngine", "EngineError"]

READY_SENTINEL = "MCP_ENTRY_READY"
DEFAULT_IMAGE = "ghcr.io/pathofbuildingcommunity/pathofbuilding-tests:latest"
# package dir -> server/ -> project root -> fork
DEFAULT_FORK = Path(__file__).resolve().parents[2] / "fork"


class EngineError(RuntimeError):
    """Raised when the engine process fails to start or a command errors."""


class PobEngine:
    """A persistent PoB2 headless engine running in Docker.

    Not safe for concurrent calls from multiple threads without the internal
    lock — which ``call()`` takes, so requests are serialized (the engine is a
    single stateful build anyway).
    """

    def __init__(
        self,
        fork_path: Path | str = DEFAULT_FORK,
        image: str = DEFAULT_IMAGE,
        ready_timeout: float = 120.0,
        call_timeout: float = 60.0,
    ) -> None:
        self.fork_path = Path(fork_path).resolve()
        self.image = image
        self.ready_timeout = ready_timeout
        self.call_timeout = call_timeout
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._ready = threading.Event()
        self._stderr_tail: list[str] = []

    # ---- lifecycle ------------------------------------------------------

    def _docker_cmd(self) -> list[str]:
        mount = f"{self.fork_path.as_posix()}:/workdir:ro"
        return [
            "docker", "run", "--rm", "-i",
            "-e", "HOME=/tmp",
            "-e", "CI=true",
            "-e", "LUA_PATH=../runtime/lua/?.lua;../runtime/lua/?/init.lua;;",
            "-w", "/workdir/src",
            "-v", mount,
            self.image,
            "luajit", "mcp_entry.lua",
        ]

    def start(self) -> None:
        if self._proc is not None:
            return
        if not (self.fork_path / "src" / "mcp_entry.lua").exists():
            raise EngineError(f"mcp_entry.lua not found under {self.fork_path}")

        self._proc = subprocess.Popen(
            self._docker_cmd(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,  # line-buffered
        )

        drainer = threading.Thread(target=self._drain_stderr, daemon=True)
        drainer.start()

        if not self._ready.wait(self.ready_timeout):
            self.close()
            raise EngineError(
                f"engine did not report {READY_SENTINEL} within "
                f"{self.ready_timeout}s. Last stderr:\n" + self._tail()
            )

    def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            line = line.rstrip("\n")
            if line.strip() == READY_SENTINEL:
                self._ready.set()
                continue
            # keep a bounded tail for diagnostics
            self._stderr_tail.append(line)
            if len(self._stderr_tail) > 100:
                del self._stderr_tail[0]

    def _tail(self) -> str:
        return "\n".join(self._stderr_tail[-40:])

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def __enter__(self) -> "PobEngine":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- protocol -------------------------------------------------------

    def call(self, cmd: str, **args: object) -> dict:
        """Send one command and return its ``result`` dict.

        Raises ``EngineError`` on transport failure or an ``ok:false`` reply.
        """
        with self._lock:
            if self._proc is None:
                raise EngineError("engine not started")
            if self._proc.poll() is not None:
                raise EngineError(
                    f"engine process exited (code {self._proc.returncode}). "
                    f"Last stderr:\n{self._tail()}"
                )

            self._next_id += 1
            req_id = self._next_id
            request = {"id": req_id, "cmd": cmd, **args}

            assert self._proc.stdin is not None and self._proc.stdout is not None
            try:
                self._proc.stdin.write(json.dumps(request) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise EngineError(f"failed to write to engine: {exc}") from exc

            line = self._proc.stdout.readline()
            if line == "":
                raise EngineError(
                    "engine closed stdout (process died?). "
                    f"Last stderr:\n{self._tail()}"
                )

            try:
                reply = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EngineError(f"invalid JSON from engine: {line!r} ({exc})") from exc

        if not reply.get("ok"):
            raise EngineError(reply.get("error", "unknown engine error"))
        return reply.get("result", {})
