from __future__ import annotations

from pathlib import Path

import pytest

from orchestra.agent import _classify
from orchestra.config import load_config
from orchestra.executor import Executor
from orchestra.models import AgentSpec, Capability, Outcome
from orchestra.router import RouteRequest, Router
from orchestra.state import CooldownStore


def _spec(**kw) -> AgentSpec:
    base = dict(name="x", command=("true",), capabilities=frozenset({Capability.coding}))
    return AgentSpec(**{**base, **kw})


def test_classify_quota_beats_exit_code():
    s = _spec(quota_patterns=("usage limit",))
    assert _classify(s, 0, "hit your usage limit") is Outcome.quota
    assert _classify(s, 0, "all good") is Outcome.success
    assert _classify(s, 1, "boom") is Outcome.error


def test_classify_retryable():
    s = _spec(retryable_patterns=("try again",))
    assert _classify(s, 1, "please try again") is Outcome.retryable


def test_router_orders_by_priority_and_filters_cooldown(config_root: Path):
    config = load_config(config_root)
    cd = CooldownStore(config_root / "state" / "cd.json")
    router = Router(config, cd)
    candidates, _ = router.resolve(RouteRequest(frozenset({Capability.coding})))
    assert [c.name for c in candidates] == ["limited", "broken", "good"]  # by priority

    cd.start("limited", 60)
    candidates, skipped = router.resolve(RouteRequest(frozenset({Capability.coding})))
    assert "limited" not in [c.name for c in candidates]
    assert any("limited" in s for s in skipped)


def test_router_prefer_and_capability_filter(config_root: Path):
    config = load_config(config_root)
    router = Router(config, CooldownStore(config_root / "state" / "cd.json"))
    candidates, _ = router.resolve(RouteRequest(frozenset({Capability.coding}), prefer="good"))
    assert candidates[0].name == "good"

    # nobody declares 'architecture' -> empty chain
    candidates, skipped = router.resolve(RouteRequest(frozenset({Capability.architecture})))
    assert candidates == []
    assert len(skipped) == 3


async def test_executor_falls_back_past_quota_and_error(executor: Executor):
    report = await executor.run("do a thing", RouteRequest(frozenset({Capability.coding})))
    assert report.succeeded
    assert report.final.agent == "good"
    # tried limited (quota) -> broken (error) -> good (success)
    assert [a.agent for a in report.attempts] == ["limited", "broken", "good"]
    assert report.attempts[0].outcome is Outcome.quota


async def test_quota_trips_persistent_cooldown(executor: Executor, config_root: Path):
    await executor.run("x", RouteRequest(frozenset({Capability.coding})))
    remaining = CooldownStore(config_root / "state" / "cd.json").cooling_down("limited")
    assert remaining > 0  # survives to the next process


async def test_parallel_runs_all(executor: Executor):
    results = await executor.parallel("x", ["good", "limited", "broken"])
    outcomes = {r.agent: r.outcome for r in results}
    assert outcomes["good"] is Outcome.success
    assert outcomes["limited"] is Outcome.quota
    assert outcomes["broken"] is Outcome.error


async def test_model_default_and_override(tmp_path):
    from orchestra.agent import run_agent
    # echo agent that prints back the model arg it was given
    spec = AgentSpec(
        name="echo", command=("python3", "-c", "import sys;print(sys.argv[1])", "{model}"),
        capabilities=frozenset({Capability.coding}), model="default-model",
    )
    assert (await run_agent(spec, "p")).stdout.strip() == "default-model"
    assert (await run_agent(spec, "p", model="override")).stdout.strip() == "override"


async def test_run_maintenance_runs_command():
    from orchestra.agent import run_maintenance
    ok, out = await run_maintenance(("python3", "-c", "print('installed')"))
    assert ok and "installed" in out
    ok2, _ = await run_maintenance(("definitely-not-a-real-binary-xyz",))
    assert not ok2


async def test_no_eligible_agent_reports_cleanly(executor: Executor):
    report = await executor.run("x", RouteRequest(frozenset({Capability.reasoning})))
    assert not report.succeeded
    assert report.final.agent == "<none>"
