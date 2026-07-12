"""Load YAML config into typed objects. This is the whole plugin system:
drop a file into config/agents/ and it becomes a routable agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def bundled_config_root() -> Path:
    """The config/ shipped with the source tree — used as the seed for `init`
    and as the fallback when no user config exists."""
    return Path(__file__).resolve().parents[2] / "config"


def user_config_root() -> Path:
    """Where a user's editable config lives (XDG): ~/.config/orchestra."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "orchestra"


def default_config_root() -> Path:
    """Resolution order: $ORCHESTRA_CONFIG → ~/.config/orchestra → bundled.
    Makes the `orchestra` command work from any directory once `init` has run."""
    env = os.environ.get("ORCHESTRA_CONFIG")
    if env:
        return Path(env)
    user = user_config_root()
    if (user / "agents").is_dir():
        return user
    return bundled_config_root()

from .models import AgentSpec, Capability


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TaskType:
    """A named routing preset from routing.yml."""

    name: str
    capabilities: frozenset[Capability]
    chain: tuple[str, ...] = ()  # explicit agent order; remaining capable agents appended
    strict: bool = False         # if true, ONLY the chain runs — no tail, no rotation


@dataclass(frozen=True, slots=True)
class Routing:
    default_capability: Capability
    task_types: dict[str, TaskType] = field(default_factory=dict)
    spread: bool = True  # rotate across agents (least-recently-used first)


@dataclass(frozen=True, slots=True)
class Config:
    agents: dict[str, AgentSpec]
    routing: Routing
    root: Path


def _caps(values: list[str] | None, where: str) -> frozenset[Capability]:
    out = set()
    for v in values or []:
        try:
            out.add(Capability(v))
        except ValueError as e:
            raise ConfigError(f"{where}: unknown capability {v!r}") from e
    return frozenset(out)


def _load_agent(path: Path, defaults: dict) -> AgentSpec:
    raw = yaml.safe_load(path.read_text()) or {}
    name = raw.get("name", path.stem)
    command = raw.get("command")
    if not command or not isinstance(command, list):
        raise ConfigError(f"{path}: 'command' must be a non-empty list")
    merged = {**defaults, **{k: v for k, v in raw.items() if v is not None}}
    return AgentSpec(
        name=name,
        command=tuple(str(c) for c in command),
        capabilities=_caps(raw.get("capabilities"), str(path)),
        priority=int(merged.get("priority", 100)),
        model=raw.get("model"),
        install=tuple(str(c) for c in (raw.get("install") or ())),
        update=tuple(str(c) for c in (raw.get("update") or ())),
        prompt_via_stdin=bool(merged.get("prompt_via_stdin", False)),
        timeout=float(merged.get("timeout", 600.0)),
        cooldown_seconds=float(merged.get("cooldown_seconds", 900.0)),
        max_retries=int(merged.get("max_retries", 1)),
        quota_patterns=tuple(raw.get("quota_patterns", []) or ()),
        retryable_patterns=tuple(raw.get("retryable_patterns", []) or ()),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        cwd=raw.get("cwd"),
        enabled=bool(raw.get("enabled", True)),
        manual=bool(raw.get("manual", False)),
    )


def load_config(root: str | Path) -> Config:
    root = Path(root)
    limits = yaml.safe_load((root / "limits.yml").read_text()) if (root / "limits.yml").exists() else {}
    defaults = limits.get("defaults", {}) if limits else {}

    agent_dir = root / "agents"
    if not agent_dir.is_dir():
        raise ConfigError(f"no agents directory at {agent_dir}")
    agents: dict[str, AgentSpec] = {}
    for path in sorted(agent_dir.glob("*.yml")) + sorted(agent_dir.glob("*.yaml")):
        spec = _load_agent(path, defaults)
        if spec.name in agents:
            raise ConfigError(f"duplicate agent name {spec.name!r} ({path})")
        agents[spec.name] = spec
    if not agents:
        raise ConfigError(f"no agent configs found in {agent_dir}")

    routing = _load_routing(root / "routing.yml", agents)
    return Config(agents=agents, routing=routing, root=root)


def _load_routing(path: Path, agents: dict[str, AgentSpec]) -> Routing:
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    default_cap = Capability(raw.get("default_capability", "coding"))
    task_types: dict[str, TaskType] = {}
    for name, body in (raw.get("task_types") or {}).items():
        chain = tuple(body.get("chain", []) or ())
        for agent_name in chain:
            if agent_name not in agents:
                raise ConfigError(f"routing task {name!r}: unknown agent {agent_name!r}")
        task_types[name] = TaskType(
            name=name,
            capabilities=_caps(body.get("capabilities"), f"task {name}"),
            chain=chain,
            strict=bool(body.get("strict", False)),
        )
    return Routing(
        default_capability=default_cap,
        task_types=task_types,
        spread=bool(raw.get("spread", True)),
    )
