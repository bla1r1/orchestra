"""Build a throwaway config pointing at fake agents implemented as tiny python
scripts, so tests exercise real subprocess execution + classification with no
external CLIs installed."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from orchestra.config import load_config
from orchestra.executor import Executor
from orchestra.router import Router
from orchestra.state import CooldownStore

# Fake agent: prints its argv-prompt to stdout, but exits with a chosen behaviour
# based on an env var, so a single script backs "good", "quota" and "boom" agents.
FAKE = textwrap.dedent("""
    import os, sys
    mode = os.environ["FAKE_MODE"]
    prompt = sys.argv[-1]
    if mode == "quota":
        print("Error: you have hit your usage limit (quota)", file=sys.stderr)
        sys.exit(2)
    if mode == "boom":
        print("kaboom", file=sys.stderr)
        sys.exit(1)
    print(f"{os.environ['AGENT_ID']} handled: {prompt}")
""")


def _agent_yaml(name: str, mode: str, priority: int) -> str:
    return textwrap.dedent(f"""
        name: {name}
        command: ["{sys.executable}", "{{fake}}", "{{prompt}}"]
        capabilities: [coding, review]
        priority: {priority}
        cooldown_seconds: 60
        env:
          FAKE_MODE: "{mode}"
          AGENT_ID: "{name}"
        quota_patterns: ["usage limit", "quota"]
    """).replace("{fake}", "{fake}")  # keep {prompt} intact, defer {fake}


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    fake = tmp_path / "fake_agent.py"
    fake.write_text(FAKE)
    agents = tmp_path / "agents"
    agents.mkdir()
    # good=cheapest, quota trips fallback, boom errors out
    for name, mode, prio in [("good", "ok", 5), ("limited", "quota", 1), ("broken", "boom", 3)]:
        y = _agent_yaml(name, mode, prio).replace("{fake}", str(fake))
        (agents / f"{name}.yml").write_text(y)
    (tmp_path / "routing.yml").write_text("default_capability: coding\n")
    return tmp_path


@pytest.fixture
def executor(config_root: Path) -> Executor:
    config = load_config(config_root)
    cooldowns = CooldownStore(config_root / "state" / "cd.json")
    return Executor(config, Router(config, cooldowns), cooldowns)
