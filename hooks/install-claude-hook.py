#!/usr/bin/env python3
"""Register (or remove) the orchestra SessionStart hook in ~/.claude/settings.json.

Idempotent: merges into existing settings, never clobbers other keys or hooks
(e.g. plugin hooks). `--remove` undoes it. Run by install.sh.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MARKER = "orchestra-session-start.sh"
HOOK_CMD = f'bash "{Path(__file__).resolve().parent / MARKER}"'
SETTINGS = Path.home() / ".claude" / "settings.json"


def _load() -> dict:
    if not SETTINGS.exists():
        return {}
    try:
        return json.loads(SETTINGS.read_text())
    except json.JSONDecodeError:
        sys.exit(f"error: {SETTINGS} is not valid JSON — fix it and re-run")


def _save(data: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2) + "\n")


def _session_start(data: dict) -> list:
    return data.setdefault("hooks", {}).setdefault("SessionStart", [])


def _already_present(groups: list) -> bool:
    return any(
        MARKER in h.get("command", "")
        for g in groups for h in g.get("hooks", [])
    )


def install() -> None:
    data = _load()
    groups = _session_start(data)
    if _already_present(groups):
        print("orchestra hook already installed")
        return
    groups.append({"hooks": [{"type": "command", "command": HOOK_CMD}]})
    _save(data)
    print(f"orchestra SessionStart hook installed in {SETTINGS}")


def remove() -> None:
    data = _load()
    groups = data.get("hooks", {}).get("SessionStart", [])
    kept = [
        g for g in groups
        if not any(MARKER in h.get("command", "") for h in g.get("hooks", []))
    ]
    if len(kept) == len(groups):
        print("orchestra hook not present")
        return
    data["hooks"]["SessionStart"] = kept
    _save(data)
    print("orchestra hook removed")


if __name__ == "__main__":
    remove() if "--remove" in sys.argv else install()
