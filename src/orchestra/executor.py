"""Walk the router's chain: retry transient failures against an agent, trip a
cooldown and advance on quota, give up gracefully at the end. Also the parallel
fan-out that Claude uses for `parallel { ... }` blocks."""

from __future__ import annotations

import asyncio
import logging

from .agent import run_agent, run_maintenance
from .config import Config
from .models import AgentSpec, ExecutionResult, Outcome, TaskReport
from .router import RouteRequest, Router
from .state import CooldownStore

log = logging.getLogger("orchestra")


class Executor:
    def __init__(self, config: Config, router: Router, cooldowns: CooldownStore) -> None:
        self._config = config
        self._router = router
        self._cooldowns = cooldowns

    async def run(self, prompt: str, req: RouteRequest, *, model: str | None = None) -> TaskReport:
        candidates, skipped = self._router.resolve(req)
        for note in skipped:
            log.info("skip", extra={"detail": note})
        report = TaskReport(prompt=prompt, capabilities=req.capabilities, attempts=[])

        for spec in candidates:
            result = await self._try_agent(spec, prompt, model)
            report.attempts.append(result)
            if result.ok:
                log.info("success", extra={"agent": spec.name, "duration": round(result.duration, 2)})
                return report
            log.warning(
                "fallback",
                extra={"agent": spec.name, "outcome": result.outcome.value, "reason": result.reason},
            )

        if not candidates:
            report.attempts.append(
                ExecutionResult(
                    agent="<none>", outcome=Outcome.error, exit_code=None,
                    stdout="", stderr="no eligible agent; see skipped notes", duration=0.0,
                    reason="; ".join(skipped) or "no agents matched",
                )
            )
        return report

    async def _try_agent(self, spec: AgentSpec, prompt: str, model: str | None = None) -> ExecutionResult:
        result: ExecutionResult | None = None
        installed = False
        for attempt in range(1, spec.max_retries + 1):
            result = await run_agent(spec, prompt, model)
            if result.reason == "binary-missing" and spec.install and not installed:
                installed = True
                log.warning("auto-install", extra={"agent": spec.name})
                ok, out = await run_maintenance(spec.install, timeout=1800)
                log.info("auto-install-done", extra={"agent": spec.name, "ok": ok, "tail": out[-200:]})
                if ok:
                    result = await run_agent(spec, prompt, model)
            if result.outcome is Outcome.success:
                return result
            if result.outcome is Outcome.quota:
                self._cooldowns.start(spec.name, spec.cooldown_seconds)
                result.reason = f"quota/rate limit -> cooldown {spec.cooldown_seconds:.0f}s"
                return result
            if result.outcome in (Outcome.timeout, Outcome.retryable) and attempt < spec.max_retries:
                log.info("retry", extra={"agent": spec.name, "attempt": attempt})
                continue
            result.reason = f"{result.outcome.value} (attempt {attempt}/{spec.max_retries})"
            return result
        assert result is not None  # loop runs at least once (max_retries >= 1)
        return result

    async def parallel(self, prompt: str, agent_names: list[str]) -> list[ExecutionResult]:
        """Run the same prompt across several named agents concurrently. Results
        come back for Claude to merge/review — no automatic fallback here."""
        specs = []
        for name in agent_names:
            spec = self._config.agents.get(name)
            if spec is None:
                raise KeyError(f"unknown agent {name!r}")
            specs.append(spec)
        return list(await asyncio.gather(*(self._try_agent(s, prompt) for s in specs)))
