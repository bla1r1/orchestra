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


def test_classify_success_ignores_output_text():
    # clean exit is success even if the (topical) output mentions quota/limits —
    # a task about rate limiting must not trip its own fallback
    s = _spec(quota_patterns=("usage limit", "rate limit"))
    assert _classify(s, 0, "here is how to add a rate limit") is Outcome.success
    assert _classify(s, 0, "all good") is Outcome.success
    # quota only counts on an actual failure
    assert _classify(s, 1, "you hit your usage limit") is Outcome.quota
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


async def test_hollow_success_empty_output_is_failure():
    from orchestra.agent import run_agent
    # exit 0 but prints nothing (e.g. a silent auth failure) -> not a real success
    silent = AgentSpec(name="silent", command=("true",), capabilities=frozenset({Capability.coding}))
    r = await run_agent(silent, "do it")
    assert r.exit_code == 0 and r.outcome is Outcome.error and "empty output" in r.reason


async def test_run_maintenance_runs_command():
    from orchestra.agent import run_maintenance
    ok, out = await run_maintenance(("python3", "-c", "print('installed')"))
    assert ok and "installed" in out
    ok2, _ = await run_maintenance(("definitely-not-a-real-binary-xyz",))
    assert not ok2


def test_spread_rotates_least_recently_used(config_root, tmp_path):
    from orchestra.config import load_config
    from orchestra.router import Router, RouteRequest
    from orchestra.state import CooldownStore, UsageStore

    config = load_config(config_root)  # routing spread defaults to true
    usage = UsageStore(tmp_path / "u.json")
    router = Router(config, CooldownStore(tmp_path / "c.json"), usage)
    req = RouteRequest(frozenset({Capability.coding}))

    first = router.resolve(req)[0][0].name
    usage.mark(first, now=1000.0)          # simulate it just ran
    order = [a.name for a in router.resolve(req)[0]]
    assert order[0] != first               # rotated away from the just-used agent
    assert order[-1] == first              # and it drops to the back


def test_spread_off_follows_priority(config_root, tmp_path):
    from orchestra.config import load_config
    from orchestra.router import Router, RouteRequest
    from orchestra.state import CooldownStore, UsageStore

    config = load_config(config_root)
    object.__setattr__(config.routing, "spread", False)  # frozen dataclass
    usage = UsageStore(tmp_path / "u.json")
    usage.mark("good", now=1.0)  # even a just-used top agent stays first when off
    router = Router(config, CooldownStore(tmp_path / "c.json"), usage)
    order = [a.name for a in router.resolve(RouteRequest(frozenset({Capability.coding})))[0]]
    assert order == ["limited", "broken", "good"]  # pure priority


def test_manual_agent_excluded_from_auto_routing(tmp_path):
    from orchestra.config import bundled_config_root, load_config
    from orchestra.router import Router, RouteRequest
    from orchestra.state import CooldownStore, UsageStore

    cfg = load_config(bundled_config_root())  # shipped config: claude is manual
    router = Router(cfg, CooldownStore(tmp_path / "c.json"), UsageStore(tmp_path / "u.json"))

    auto = [a.name for a in router.resolve(RouteRequest(frozenset({Capability.coding})))[0]]
    assert "claude" not in auto                       # never auto-routed / rotated
    assert auto                                        # but other agents still route

    forced = [a.name for a in router.resolve(
        RouteRequest(frozenset({Capability.coding}), prefer="claude"))[0]]
    assert forced[0] == "claude"                       # --prefer still works

    # security_review is strict: ONLY claude, no rotation, no fallback
    sec = [a.name for a in router.resolve(
        RouteRequest(frozenset(), task_type="security_review"))[0]]
    assert sec == ["claude"]


def test_build_handoff_prompt():
    from orchestra.cli import build_handoff_prompt
    p = build_handoff_prompt("build a parser", "wrote lexer, tokenizer half done")
    assert "CONTINUATION PROMPT" in p
    assert "build a parser" in p          # original task carried
    assert "tokenizer half done" in p     # transcript carried
    # task is optional
    assert "ORIGINAL TASK" not in build_handoff_prompt(None, "x")


def test_config_resolution_order(tmp_path, monkeypatch):
    from orchestra.config import default_config_root
    # 1. explicit env wins
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "explicit"))
    assert default_config_root() == tmp_path / "explicit"
    # 2. no env + a user config that exists -> user dir
    monkeypatch.delenv("ORCHESTRA_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "orchestra" / "agents").mkdir(parents=True)
    assert default_config_root() == tmp_path / "orchestra"
    # 3. no env + no user config -> bundled falls back (path ends with /config)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
    assert default_config_root().name == "config"


def test_qc_scan_flags_stubs_and_passes_clean(tmp_path):
    from orchestra.quality import QualityConfig, scan
    cfg = QualityConfig.load(tmp_path)  # no quality.yml -> built-in defaults
    bad = tmp_path / "impl.py"
    bad.write_text("def pay():\n    raise NotImplementedError  # TODO later\n")
    good = tmp_path / "ok.py"
    good.write_text("def add(a, b):\n    return a + b\n")

    findings = scan([bad, good], cfg)
    hit_files = {f.file for f in findings}
    assert str(bad) in hit_files          # stub caught
    assert str(good) not in hit_files     # clean code passes

    # excludes: a test file with TODO must not trip QC
    (tmp_path / "test_x.py").write_text("# TODO: nothing\n")
    assert not [f for f in scan([tmp_path], cfg) if "test_x.py" in f.file]


async def test_no_eligible_agent_reports_cleanly(executor: Executor):
    report = await executor.run("x", RouteRequest(frozenset({Capability.reasoning})))
    assert not report.succeeded
    assert report.final.agent == "<none>"
