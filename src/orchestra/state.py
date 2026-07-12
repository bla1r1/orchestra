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


class CooldownStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _read(self) -> dict[str, float]:
        try:
            return json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, float]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self._path)  # atomic
        finally:
            Path(tmp).unlink(missing_ok=True)

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
