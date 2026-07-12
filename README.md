# orchestra

Subscription-CLI AI agent orchestration for Claude Code. Claude Code (Opus) is
the orchestrator/reviewer; implementation is delegated to coding CLIs you pay for
by subscription (Codex, Gemini, MiMo, OpenCode, Antigravity, Claude CLI) — no API
billing. Routing picks an agent by capability, falls back on quota/rate-limit/
timeout, and benches tripped agents with a persistent cooldown.

## Install

```bash
./install.sh
```

Installs the `orchestra` CLI, links the skill into Claude Code
(`~/.claude/skills/orchestrate`), and generates an `AGENTS.md` that codex /
opencode / mimo read — so any of those CLIs can act as the conductor. Re-run
after `git pull`; it's idempotent. (Bare CLI only: `pip install -e ".[dev]"`.)

## Configure — priority & "what agent for what"

Two knobs, no code, previewable without spending quota:

```bash
# "what agent for what task": edit the chain for a task type
$EDITOR config/routing.yml

# "global preference": edit an agent's priority (lower = tried earlier)
$EDITOR config/agents/codex.yml

# see the effect immediately — who runs, with which model, why others skip:
orchestra route --task-type refactoring
orchestra route --capability coding,review
```

Example `orchestra route` output:

```
task 'refactoring' would run in order:
  1. codex     prio=10  (model gpt-5.6-luna)
  2. opencode  prio=70  (model opencode/deepseek-v4-flash-free)
  3. claude    prio=90
skipped:
  - antigravity: cooling down 20927s
  - mimo: lacks ['refactoring']
```

## Use

```bash
orchestra run --task-type refactoring "Split the billing module"
orchestra run --capability coding,review "Review auth/token.py"
orchestra run --prefer mimo --model xiaomi/mimo-v2.5-pro "…"  # override the model
orchestra parallel --agents codex,mimo,opencode "Design a rate limiter"
orchestra route --task-type refactoring       # dry-run routing, no quota spent
orchestra qc $(git diff --name-only)          # QC gate: flag stubs/hacks, exit 1
orchestra agents      # list agents + cooldown state
orchestra health      # probe every agent binary
orchestra update      # run each agent's update command
orchestra install --agents opencode,mimo   # (re)install named CLIs
```

Each agent declares a default `model:` (codex→gpt-5.6-luna, antigravity→Gemini
3.5 Flash, mimo→xiaomi/mimo-v2.5, opencode→deepseek); `--model` overrides it for
a run. A missing binary auto-installs on first use if the agent has an `install:`
command, then the run retries.

Config dir defaults to `./config` (override with `ORCHESTRA_CONFIG`).

## Add an agent — no code

Drop a YAML into `config/agents/`:

```yaml
name: mynewcli
command: ["mynewcli", "--prompt", "{prompt}"]   # {prompt} is substituted
capabilities: [coding, testing]
priority: 15
quota_patterns: ["rate limit", "quota"]          # regex -> triggers fallback
retryable_patterns: ["timeout"]
```

`prompt_via_stdin: true` pipes the prompt to stdin instead of substituting.
Edit `config/routing.yml` to slot it into a task-type chain. Restart nothing.

## Layout

```
src/orchestra/
  models.py         typed core (AgentSpec, Capability, Outcome, results)
  config.py         load YAML agents/routing/limits  (the plugin system)
  agent.py          async subprocess execution + outcome classification
  state.py          persistent cooldowns (JSON)
  router.py         capability + priority + chain -> ordered candidates
  executor.py       fallback/retry walk + parallel fan-out
  logging_setup.py  JSON-lines audit log (logs/orchestra.log)
  cli.py            orchestra run|parallel|agents|health
config/             agents/*.yml, routing.yml, limits.yml
skills/orchestrate/ Claude Code skill (the PM/router persona)
tests/              subprocess-backed behavioural tests
```

## Quality control

A worker doesn't grade its own homework. Two gates before the orchestrator
accepts delegated code:

- **Objective scan** — `orchestra qc <files>` flags stub/placeholder/hack markers
  (`TODO`, `NotImplementedError`, "for simplicity", "in a real implementation",
  bare `...` bodies, …) and exits 1 if any are found. Patterns are config, not
  code — edit `config/quality.yml`. Point it at the worker's changes:
  `orchestra qc $(git diff --name-only)`.
- **LLM review** — Claude (the orchestrator) reads the actual diff against a hard
  bar: no stubs, full implementation, no hacks, real error handling, tests where
  logic is non-trivial. Fail → reject and re-delegate with the defects named,
  then re-run QC. This loop is baked into the skill.

## Verified against real CLIs

Checked on this machine (2026-07):

| Agent | Installed | Auth | Invocation | Status |
|-------|-----------|------|------------|--------|
| codex | ✅ 0.144.1 | ChatGPT subscription (no API key) | `codex exec --skip-git-repo-check -s workspace-write "…"` | **VERIFIED LIVE** via `orchestra run` — answered in 3.5s |
| claude | ✅ 2.1.196 | `claude login` creds (NOT inherited from parent session) | `claude -p "…"` | OK from a normal terminal; 401 inside the Claude Desktop sandbox |
| antigravity (`agy`) | ✅ 1.1.1 | own login | `agy -p --dangerously-skip-permissions --mode accept-edits "…"` | health OK. Replaced the Gemini CLI, inherits its architecture/planning routing role |
| opencode | ✅ 1.17.18 | own provider login | `opencode run "…"` (absolute path, headless = no prompts) | health OK |
| mimo | ✅ 0.1.5 | none — built-in `mimo-auto` model | `mimo run "…"` (absolute path, headless = no prompts) | **VERIFIED LIVE**, answered a prompt with no login |
| gemini | ❌ discontinued | — | — | removed (killed ~2026-06, superseded by Antigravity 2.0) |

opencode/mimo live in `~/.opencode/bin` and `~/.mimocode/bin`, which the
installers did not add to the shell rc — so their agent configs use the absolute
binary path and don't depend on `PATH` at all.

Findings baked into config: codex needs `--skip-git-repo-check` (it refuses to
run outside a trusted git dir) and `-s workspace-write` (default sandbox is
read-only, so it can't edit files). No `ANTHROPIC_API_KEY` is set — every CLI
bills against its own subscription, which is the whole point. `orchestra health`
tells you which binaries are actually reachable.

## Deliberately NOT built (roadmap)

MCP, GitHub integration, Docker execution, remote/distributed workers, web
dashboard, metrics. The `AgentSpec`/`Router`/`Executor` seam is where they slot
in — none is needed to route and fall back today, so none was written. Add when
there's a second machine or a real quota-analytics need.
