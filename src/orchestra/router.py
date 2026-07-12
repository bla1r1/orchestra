"""Turn a task (capabilities + optional task-type) into an ordered candidate
chain. Explicit chain from routing.yml wins; capable agents fill the tail;
disabled and cooling-down agents drop out."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .models import AgentSpec, Capability
from .state import CooldownStore


@dataclass(frozen=True, slots=True)
class RouteRequest:
    capabilities: frozenset[Capability]
    task_type: str | None = None
    prefer: str | None = None  # force this agent to the front if it qualifies


class Router:
    def __init__(self, config: Config, cooldowns: CooldownStore) -> None:
        self._config = config
        self._cooldowns = cooldowns

    def resolve(self, req: RouteRequest) -> tuple[list[AgentSpec], list[str]]:
        """Return (ordered candidates, skipped-agent notes)."""
        caps = set(req.capabilities)
        chain: list[str] = []
        if req.task_type:
            tt = self._config.routing.task_types.get(req.task_type)
            if tt is None:
                raise KeyError(f"unknown task_type {req.task_type!r}")
            caps |= set(tt.capabilities)
            chain = list(tt.chain)
        needed = frozenset(caps)

        ordered: list[str] = []
        if req.prefer:
            ordered.append(req.prefer)
        ordered += [a for a in chain if a not in ordered]
        # tail: every remaining capable agent, best priority first
        tail = sorted(
            (s for s in self._config.agents.values() if s.name not in ordered),
            key=lambda s: (s.priority, s.name),
        )
        ordered += [s.name for s in tail]

        candidates: list[AgentSpec] = []
        skipped: list[str] = []
        for name in ordered:
            spec = self._config.agents.get(name)
            if spec is None:
                skipped.append(f"{name}: not configured")
                continue
            if not spec.enabled:
                skipped.append(f"{name}: disabled")
                continue
            if not spec.handles(needed):
                skipped.append(f"{name}: lacks {sorted(c.value for c in needed - spec.capabilities)}")
                continue
            remaining = self._cooldowns.cooling_down(name)
            if remaining > 0:
                skipped.append(f"{name}: cooling down {remaining:.0f}s")
                continue
            candidates.append(spec)
        return candidates, skipped
