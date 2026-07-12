"""Entrypoint the Skill shells out to.

    orchestra run --capability coding,review [--task-type refactoring]
                  [--prefer codex] "prompt"
    orchestra parallel --agents codex,gemini,mimo "prompt"
    orchestra agents            # list configured agents + cooldown state
    orchestra health            # probe every agent binary

Prints the winning agent's stdout on success (exit 0). On total failure prints
the attempt trail to stderr and exits 1. Machine-readable trail with --json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .agent import healthcheck, run_maintenance
from .config import load_config
from .executor import Executor
from .logging_setup import setup_logging
from .models import Capability
from .router import RouteRequest, Router
from .state import CooldownStore


def _config_root() -> Path:
    return Path(os.environ.get("ORCHESTRA_CONFIG", Path.cwd() / "config"))


def _parse_caps(raw: str) -> frozenset[Capability]:
    return frozenset(Capability(c.strip()) for c in raw.split(",") if c.strip())


def _build(root: Path) -> tuple[Executor, Router]:
    config = load_config(root)
    cooldowns = CooldownStore(root / "state" / "cooldowns.json")
    router = Router(config, cooldowns)
    return Executor(config, router, cooldowns), router


def _report_json(report) -> str:
    return json.dumps(
        {
            "prompt": report.prompt,
            "capabilities": sorted(c.value for c in report.capabilities),
            "succeeded": report.succeeded,
            "attempts": [asdict(a) | {"outcome": a.outcome.value} for a in report.attempts],
        },
        indent=2, default=str,
    )


async def _cmd_run(args: argparse.Namespace, root: Path) -> int:
    executor, _ = _build(root)
    req = RouteRequest(
        capabilities=_parse_caps(args.capability),
        task_type=args.task_type,
        prefer=args.prefer,
    )
    report = await executor.run(args.prompt, req, model=args.model)
    if args.json:
        print(_report_json(report))
    elif report.succeeded:
        sys.stdout.write(report.final.stdout)
    else:
        for a in report.attempts:
            print(f"[{a.agent}] {a.outcome.value}: {a.reason or a.stderr.strip()[:200]}", file=sys.stderr)
    return 0 if report.succeeded else 1


async def _cmd_parallel(args: argparse.Namespace, root: Path) -> int:
    executor, _ = _build(root)
    names = [n.strip() for n in args.agents.split(",") if n.strip()]
    results = await executor.parallel(args.prompt, names)
    print(json.dumps(
        [asdict(r) | {"outcome": r.outcome.value} for r in results], indent=2, default=str,
    ))
    return 0 if any(r.ok for r in results) else 1


async def _cmd_route(args: argparse.Namespace, root: Path) -> int:
    """Dry-run the router: show the chain + why agents were skipped. No execution,
    no quota spent — use it to check the effect of routing.yml / priority edits."""
    _, router = _build(root)
    req = RouteRequest(
        capabilities=_parse_caps(args.capability),
        task_type=args.task_type,
        prefer=args.prefer,
    )
    candidates, skipped = router.resolve(req)
    label = args.task_type or args.capability
    print(f"task '{label}' would run in order:")
    for i, spec in enumerate(candidates, 1):
        model = f"  (model {spec.model})" if spec.model else ""
        print(f"  {i}. {spec.name}  prio={spec.priority}{model}")
    if not candidates:
        print("  (no eligible agent)")
    if skipped:
        print("skipped:")
        for note in skipped:
            print(f"  - {note}")
    return 0


async def _cmd_agents(args: argparse.Namespace, root: Path) -> int:
    config = load_config(root)
    cooldowns = CooldownStore(root / "state" / "cooldowns.json").snapshot()
    for spec in sorted(config.agents.values(), key=lambda s: (s.priority, s.name)):
        cd = cooldowns.get(spec.name)
        state = f"cooldown {cd:.0f}s" if cd else ("enabled" if spec.enabled else "disabled")
        caps = ",".join(sorted(c.value for c in spec.capabilities))
        print(f"{spec.name:<14} prio={spec.priority:<4} [{state}] {caps}")
    return 0


async def _cmd_maint(args: argparse.Namespace, root: Path, field: str) -> int:
    """Shared install/update handler. `field` is 'install' or 'update'."""
    config = load_config(root)
    wanted = [n.strip() for n in args.agents.split(",")] if args.agents else list(config.agents)
    rc = 0
    for name in wanted:
        spec = config.agents.get(name)
        if spec is None:
            print(f"{name:<14} unknown agent", file=sys.stderr)
            rc = 1
            continue
        argv = getattr(spec, field)
        if not argv:
            print(f"{name:<14} no {field} command configured")
            continue
        ok, out = await run_maintenance(argv, timeout=1800)
        print(f"{name:<14} {field}: {'OK' if ok else 'FAILED'}")
        if not ok:
            sys.stderr.write(out[-500:] + "\n")
            rc = 1
    return rc


async def _cmd_health(args: argparse.Namespace, root: Path) -> int:
    config = load_config(root)
    checks = await asyncio.gather(*(healthcheck(s) for s in config.agents.values()))
    ok = True
    for spec, healthy in zip(config.agents.values(), checks):
        print(f"{spec.name:<14} {'OK' if healthy else 'UNREACHABLE'}")
        ok = ok and healthy
    return 0 if ok else 1


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orchestra")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="route + execute with fallback")
    run.add_argument("prompt")
    run.add_argument("--capability", default="coding", help="comma-separated")
    run.add_argument("--task-type", default=None)
    run.add_argument("--prefer", default=None)
    run.add_argument("--model", default=None, help="override the agent's default model (use with --prefer)")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=_cmd_run)

    par = sub.add_parser("parallel", help="fan out to several agents")
    par.add_argument("prompt")
    par.add_argument("--agents", required=True, help="comma-separated agent names")
    par.set_defaults(func=_cmd_parallel)

    ag = sub.add_parser("agents", help="list configured agents")
    ag.set_defaults(func=_cmd_agents)

    rt = sub.add_parser("route", help="dry-run routing (no execution, no quota)")
    rt.add_argument("--capability", default="coding", help="comma-separated")
    rt.add_argument("--task-type", default=None)
    rt.add_argument("--prefer", default=None)
    rt.set_defaults(func=_cmd_route)

    hc = sub.add_parser("health", help="probe agent binaries")
    hc.set_defaults(func=_cmd_health)

    ins = sub.add_parser("install", help="run agents' install commands")
    ins.add_argument("--agents", default=None, help="comma-separated (default: all)")
    ins.set_defaults(func=lambda a, r: _cmd_maint(a, r, "install"))

    upd = sub.add_parser("update", help="run agents' update commands")
    upd.add_argument("--agents", default=None, help="comma-separated (default: all)")
    upd.set_defaults(func=lambda a, r: _cmd_maint(a, r, "update"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = _config_root()
    setup_logging(root, verbose=args.verbose)
    return asyncio.run(args.func(args, root))


if __name__ == "__main__":
    raise SystemExit(main())
