"""Cooldown state persisted to a JSON file, because each `orchestra run` is a
fresh process and a cooldown from a quota hit must survive to the next call.

ponytail: single JSON file + coarse file lock via atomic replace. Swap for
Redis/SQLite only if multiple orchestrators race on the same machine.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def _read_map(path: Path) -> dict[str, float]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_map(path: Path, data: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)  # atomic
    finally:
        Path(tmp).unlink(missing_ok=True)


class UsageStore:
    """Last-used timestamp per agent, so the router can rotate work across agents
    (least-recently-used first) instead of draining one agent's limit."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def used_at(self, agent: str) -> float:
        return _read_map(self._path).get(agent, 0.0)

    def mark(self, agent: str, *, now: float | None = None) -> None:
        data = _read_map(self._path)
        data[agent] = time.time() if now is None else now
        _write_map(self._path, data)

    def snapshot(self) -> dict[str, float]:
        return _read_map(self._path)


class CooldownStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _read(self) -> dict[str, float]:
        return _read_map(self._path)

    def _write(self, data: dict[str, float]) -> None:
        _write_map(self._path, data)

    def cooling_down(self, agent: str, *, now: float | None = None) -> float:
        """Seconds remaining on cooldown for ``agent`` (0.0 if available)."""
        now = time.time() if now is None else now
        until = self._read().get(agent, 0.0)
        return max(0.0, until - now)

    def start(self, agent: str, seconds: float, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        data = self._read()
        data[agent] = max(data.get(agent, 0.0), now + seconds)
        # prune expired entries so the file stays small
        data = {a: t for a, t in data.items() if t > now}
        self._write(data)

    def snapshot(self, *, now: float | None = None) -> dict[str, float]:
        now = time.time() if now is None else now
        return {a: max(0.0, t - now) for a, t in self._read().items() if t > now}
