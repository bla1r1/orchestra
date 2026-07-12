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
from .config import bundled_config_root, default_config_root, load_config, user_config_root
from .executor import Executor
from .logging_setup import setup_logging
from .models import Capability
from .quality import QualityConfig, scan
from .router import RouteRequest, Router
from .state import CooldownStore, UsageStore


def _config_root() -> Path:
    return default_config_root()


def _parse_caps(raw: str) -> frozenset[Capability]:
    return frozenset(Capability(c.strip()) for c in raw.split(",") if c.strip())


def _build(root: Path) -> tuple[Executor, Router]:
    config = load_config(root)
    cooldowns = CooldownStore(root / "state" / "cooldowns.json")
    usage = UsageStore(root / "state" / "usage.json")
    router = Router(config, cooldowns, usage)
    return Executor(config, router, cooldowns, usage), router


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
    import time

    config = load_config(root)
    cooldowns = CooldownStore(root / "state" / "cooldowns.json").snapshot()
    usage = UsageStore(root / "state" / "usage.json").snapshot()
    now = time.time()
    for spec in sorted(config.agents.values(), key=lambda s: (s.priority, s.name)):
        cd = cooldowns.get(spec.name)
        state = f"cooldown {cd:.0f}s" if cd else ("enabled" if spec.enabled else "disabled")
        last = usage.get(spec.name)
        used = f"used {now - last:.0f}s ago" if last else "unused"
        tag = " manual" if spec.manual else ""
        caps = ",".join(sorted(c.value for c in spec.capabilities))
        print(f"{spec.name:<14} prio={spec.priority:<4} [{state}{tag}] {used:<16} {caps}")
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


def build_handoff_prompt(task: str | None, transcript: str) -> str:
    """Instruction that turns an unfinished worker's transcript into a compact
    continuation prompt a fresh agent can pick up from."""
    task_line = f"ORIGINAL TASK:\n{task}\n\n" if task else ""
    return (
        "Another AI worked on a task but did NOT finish it. Below is the full "
        "transcript of its session. Compact it into a short CONTINUATION PROMPT "
        "that a different, fresh AI can act on to COMPLETE the task. The prompt "
        "must state: (1) what is already done, (2) what still remains, (3) key "
        "files, decisions and constraints, (4) the exact next steps. Be concise "
        "and self-contained. Output ONLY the continuation prompt, nothing else.\n\n"
        f"{task_line}TRANSCRIPT:\n{transcript}"
    )


async def _cmd_compact(args: argparse.Namespace, root: Path) -> int:
    """Hand off an unfinished task: feed a worker's transcript to a compactor
    agent (opencode by default) and print a continuation prompt for the next AI."""
    transcript = Path(args.file).read_text() if args.file else sys.stdin.read()
    if not transcript.strip():
        print("compact: empty transcript (pass --file or pipe on stdin)", file=sys.stderr)
        return 1
    executor, _ = _build(root)
    req = RouteRequest(capabilities=frozenset({Capability.documentation}), prefer=args.with_agent)
    report = await executor.run(build_handoff_prompt(args.task, transcript), req)
    if report.succeeded:
        sys.stdout.write(report.final.stdout)
        return 0
    for a in report.attempts:
        print(f"[{a.agent}] {a.outcome.value}: {a.reason or a.stderr[:120]}", file=sys.stderr)
    return 1


async def _cmd_init(args: argparse.Namespace, root: Path) -> int:
    """Copy the bundled config to ~/.config/orchestra so `orchestra` works from
    any directory and the user has an editable copy."""
    import shutil

    dst = user_config_root()
    if (dst / "agents").is_dir() and not args.force:
        print(f"config already exists at {dst} (use --force to overwrite)")
        return 0
    src = bundled_config_root()
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in {"state", "logs"}:
            continue  # runtime dirs, not defaults
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    print(f"config initialised at {dst}")
    print("edit it with:  orchestra config --edit")
    return 0


async def _cmd_config(args: argparse.Namespace, root: Path) -> int:
    """Show (or open) the active config so settings are easy to find and edit."""
    print(f"active config: {root}")
    for f in sorted(root.rglob("*.yml")):
        print(f"  {f.relative_to(root)}")
    if args.edit:
        editor = os.environ.get("EDITOR", "nano")
        proc = await asyncio.create_subprocess_exec(editor, str(root))
        await proc.wait()
    return 0


async def _cmd_qc(args: argparse.Namespace, root: Path) -> int:
    """Quality-control scan: flag stubs/placeholders/hacks in a worker's output.
    Exit 1 if any are found so the orchestrator can reject and re-delegate."""
    cfg = QualityConfig.load(root)
    findings = scan(args.paths, cfg)
    if args.json:
        print(json.dumps([f.__dict__ for f in findings], indent=2))
    elif findings:
        print(f"QC FAILED — {len(findings)} incomplete/hack marker(s):", file=sys.stderr)
        for f in findings:
            print(f"  {f.file}:{f.line}: {f.text}", file=sys.stderr)
    else:
        print("QC passed — no stub/placeholder/hack markers found")
    return 1 if findings else 0


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

    qc = sub.add_parser("qc", help="quality-control scan for stubs/hacks (exit 1 if found)")
    qc.add_argument("paths", nargs="+", help="files or directories to scan")
    qc.add_argument("--json", action="store_true")
    qc.set_defaults(func=_cmd_qc)

    cmp = sub.add_parser("compact", help="turn an unfinished worker's transcript into a continuation prompt")
    cmp.add_argument("--with", dest="with_agent", default="opencode", help="compactor agent (default: opencode)")
    cmp.add_argument("--task", default=None, help="the original task, for context")
    cmp.add_argument("--file", default=None, help="transcript file (default: read stdin)")
    cmp.set_defaults(func=_cmd_compact)

    ini = sub.add_parser("init", help="scaffold editable config in ~/.config/orchestra")
    ini.add_argument("--force", action="store_true", help="overwrite an existing config")
    ini.set_defaults(func=_cmd_init)

    cfg = sub.add_parser("config", help="show or open the active config")
    cfg.add_argument("--edit", action="store_true", help="open it in $EDITOR")
    cfg.set_defaults(func=_cmd_config)

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
