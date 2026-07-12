"""The CLI abstraction: one async function drives any agent. Everything that
differs between agents (argv, timeout, quota/retry regexes) is data on AgentSpec,
which is why adding an agent needs no code."""

from __future__ import annotations

import asyncio
import os
import re
import time
from functools import lru_cache

from .models import AgentSpec, ExecutionResult, Outcome


@lru_cache(maxsize=256)
def _compile(patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def _classify(spec: AgentSpec, exit_code: int | None, text: str) -> Outcome:
    # A clean exit is success — do NOT scan the output for quota/error words, or a
    # task *about* rate limiting/quotas would falsely trip fallback on its own
    # (correct) answer. Quota/retry signals only matter on an actual failure.
    if exit_code == 0:
        return Outcome.success
    if any(p.search(text) for p in _compile(spec.quota_patterns)):
        return Outcome.quota
    if any(p.search(text) for p in _compile(spec.retryable_patterns)):
        return Outcome.retryable
    return Outcome.error


async def run_agent(spec: AgentSpec, prompt: str, model: str | None = None) -> ExecutionResult:
    """Execute one agent once. Never raises for agent failure — the outcome is
    encoded in the returned result so the executor can decide to fall back.

    ``model`` overrides the agent's default for this run; either value is
    substituted for the ``{model}`` token in the command.
    """

    effective_model = model or spec.model or ""
    if spec.prompt_via_stdin:
        argv = [a.replace("{model}", effective_model) for a in spec.command]
        stdin_data: bytes | None = prompt.encode()
    else:
        argv = [a.replace("{prompt}", prompt).replace("{model}", effective_model) for a in spec.command]
        stdin_data = None

    env = {**os.environ, **spec.env}
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=spec.cwd,
        )
    except (FileNotFoundError, PermissionError) as e:
        return ExecutionResult(
            agent=spec.name, outcome=Outcome.error, exit_code=None,
            stdout="", stderr=f"cannot launch {spec.command[0]!r}: {e}",
            duration=time.monotonic() - start, reason="binary-missing",
        )

    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(stdin_data), timeout=spec.timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ExecutionResult(
            agent=spec.name, outcome=Outcome.timeout, exit_code=None,
            stdout="", stderr=f"timeout after {spec.timeout}s",
            duration=time.monotonic() - start,
        )

    stdout = out_b.decode(errors="replace")
    stderr = err_b.decode(errors="replace")
    outcome = _classify(spec, proc.returncode, f"{stdout}\n{stderr}")
    return ExecutionResult(
        agent=spec.name, outcome=outcome, exit_code=proc.returncode,
        stdout=stdout, stderr=stderr, duration=time.monotonic() - start,
    )


async def run_maintenance(argv: tuple[str, ...], *, timeout: float = 600.0) -> tuple[bool, str]:
    """Run an install/update command. Returns (ok, combined output). Used by
    `orchestra install/update` and by auto-install on a missing binary."""
    if not argv:
        return False, "no command configured"
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=os.environ,
        )
    except (FileNotFoundError, PermissionError) as e:
        return False, f"cannot run {argv[0]!r}: {e}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False, f"timeout after {timeout}s"
    return proc.returncode == 0, out.decode(errors="replace")


async def healthcheck(spec: AgentSpec) -> bool:
    """Cheap liveness probe: can we launch the binary at all?"""
    try:
        proc = await asyncio.create_subprocess_exec(
            spec.command[0], "--version",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, **spec.env},
        )
    except (FileNotFoundError, PermissionError):
        return False
    try:
        await asyncio.wait_for(proc.wait(), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False
    return True
