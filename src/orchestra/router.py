"""Turn a task (capabilities + optional task-type) into an ordered candidate
chain.

Ordering, in priority of tie-breakers:
  1. an explicit `--prefer` agent, if it qualifies;
  2. then, when routing.spread is on (default), the remaining eligible agents
     least-recently-used first — so work rotates across all agents and no single
     subscription's limit gets drained;
  3. otherwise the task-type's explicit `chain` order, then priority.

Disabled, capability-mismatched, and cooling-down agents drop out with a note.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .models import AgentSpec, Capability
from .state import CooldownStore, UsageStore


@dataclass(frozen=True, slots=True)
class RouteRequest:
    capabilities: frozenset[Capability]
    task_type: str | None = None
    prefer: str | None = None  # force this agent to the front if it qualifies


class Router:
    def __init__(
        self, config: Config, cooldowns: CooldownStore, usage: UsageStore | None = None
    ) -> None:
        self._config = config
        self._cooldowns = cooldowns
        self._usage = usage

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

        skipped: list[str] = []
        for name in filter(None, [req.prefer, *chain]):
            if name not in self._config.agents:
                skipped.append(f"{name}: not configured")

        eligible: list[str] = []
        for name, spec in self._config.agents.items():
            if not spec.enabled:
                skipped.append(f"{name}: disabled")
            elif not spec.handles(needed):
                skipped.append(f"{name}: lacks {sorted(c.value for c in needed - spec.capabilities)}")
            elif (cd := self._cooldowns.cooling_down(name)) > 0:
                skipped.append(f"{name}: cooling down {cd:.0f}s")
            else:
                eligible.append(name)

        ordered = self._order(eligible, req.prefer, chain)
        return [self._config.agents[n] for n in ordered], skipped

    def _order(self, eligible: list[str], prefer: str | None, chain: list[str]) -> list[str]:
        rest = [n for n in eligible if n != prefer]
        spread = self._config.routing.spread and self._usage is not None
        if spread:
            # least-recently-used first; priority then name break ties (and order
            # the never-used, whose used_at is 0.0, by preference).
            rest.sort(key=lambda n: (self._usage.used_at(n), self._config.agents[n].priority, n))
        else:
            chain_part = [n for n in chain if n in rest]
            tail = sorted(
                (n for n in rest if n not in chain_part),
                key=lambda n: (self._config.agents[n].priority, n),
            )
            rest = chain_part + tail
        head = [prefer] if prefer in eligible else []
        return head + rest
