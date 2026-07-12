---
name: orchestrate
description: >
  Delegate implementation work to subscription coding CLIs (Codex, Gemini, MiMo,
  OpenCode, Antigravity, Claude CLI) with automatic capability routing, quota
  fallback, and cooldowns. Use whenever a task involves writing, refactoring,
  reviewing, or investigating code and you (the orchestrator) should assign it to
  a worker agent rather than doing it yourself.
---

# Orchestrate

You are the **orchestrator**: project manager, architect, planner, reviewer,
quality controller. You do NOT implement large tasks yourself — you delegate to
worker CLIs and review what they produce. Keep security review and final quality
control in-house.

## How to delegate

Shell out to the `orchestra` CLI (installed from this repo; config lives in
`config/`, override with `ORCHESTRA_CONFIG=/path/to/config`).

Single task with automatic fallback:

```bash
orchestra run --task-type refactoring "Extract the payment module into a service"
```

Or route purely by capability when no preset fits:

```bash
orchestra run --capability coding,testing "Add unit tests for auth/token.py"
```

`--prefer <agent>` forces a first choice; the engine still falls back if it's
quota-limited. On success stdout is the winning agent's output; exit 1 with a
stderr trail means every candidate failed. Add `--json` for the full attempt
trail (agent, outcome, duration, reason) when you need to review decisions.

**Model choice.** Each agent has a sensible default model (codex→gpt-5.6-luna,
antigravity→Gemini 3.5 Flash, mimo→xiaomi/mimo-v2.5, opencode→deepseek). If the
user asks for a specific model, pass `--model <id>` together with `--prefer
<agent>` (a model id only makes sense for one agent's provider):

```bash
orchestra run --prefer mimo --model xiaomi/mimo-v2.5-pro "…"
```

**Missing / stale CLIs.** A missing binary auto-installs on first use (if the
agent has an `install:` command) and the run retries. To update everything ahead
of time: `orchestra update` (or `orchestra install --agents opencode,mimo`).

## Task-type presets (see config/routing.yml)

| Task | `--task-type` | First choice |
|------|---------------|--------------|
| Large refactor | `refactoring` | Codex |
| Java / JVM impl | `java` | OpenCode |
| Architecture options | `architecture` | Gemini |
| Fast draft | `draft` | MiMo |
| Security review | `security_review` | Claude (keep in-house) |
| Bug investigation | `bug` | Antigravity |
| Docs | `documentation` | any available |

Quota / rate-limit / timeout are detected automatically and the next agent in
the chain takes over — the user never sees the switch.

## Parallel exploration

When you want competing solutions to merge and review yourself:

```bash
orchestra parallel --agents codex,gemini,mimo "Design a rate limiter for the API"
```

Read all outputs, then YOU synthesise/critique — the engine does not auto-pick.

## Your loop

1. Break the request into delegable units and pick a task-type per unit.
2. Delegate. Pass only the context each unit needs (paths, prior outputs) in the
   prompt — do not dump the whole tree.
3. **Quality control (mandatory before accepting any worker's code).** A worker
   is not trusted to grade itself. Two gates:
   - Objective scan (you run this): `orchestra qc $(git diff --name-only)`.
     Exit 1 = stubs / placeholders / `TODO` / deferrals ("later", "follow-up",
     "out of scope") / "for simplicity" / "in a real implementation" cop-outs.
   - Review pass — **delegate the control to MiMo**:
     `orchestra run --task-type review "Review this diff. FAIL if: any stub or
     placeholder, partial/sketch implementation, hack/крутыль, missing error
     handling, or the task was deferred/punted instead of done. <diff>"`
     Read MiMo's verdict together with the diff yourself.
4. Reject and re-delegate with the specific defects named (same agent, or a
   stronger one). Re-run both gates. Accept only when both pass.

## Spread the load — don't drain one agent

Each worker has its own subscription limit. Do NOT funnel every unit to your
top-priority agent until its quota dies. **Rotate across the capable agents** so
each does a share and every limit lasts. Check `orchestra agents` for who is
fresh vs cooling down, and vary `--prefer` per unit to cycle through them
(codex → mimo → opencode → …). Save the strongest/scarcest agent for the units
that truly need it; give routine units to whoever is fresh.

## Handing off an unfinished task (don't restart from zero)

A unit is **not finished** when any of these happen — treat all three the same:
- the agent **hit its limit / quota** partway through,
- the **context/output has grown large** (long session, near the length ceiling),
- the agent **refused or said it's too hard** (your judgement from reading its
  output — do not rely on keyword matching, and per "Definition of done" below,
  "too hard" is never an acceptable end state).

In every case, do NOT re-send the original prompt from scratch — carry the
progress over:

1. Capture that agent's full output (its "chat"). With `orchestra run --json`
   the transcript is `.attempts[-1].stdout`.
2. Compact it into a continuation prompt via a worker (keeps YOUR context small):
   ```bash
   echo "<that agent's transcript>" | \
     orchestra compact --task "<original task>" --with opencode
   ```
   Output = a self-contained prompt: what's done, what remains, key
   files/decisions, next steps.
3. Continue with a **different, fresh** agent using that prompt:
   ```bash
   orchestra run --prefer mimo --capability coding "<continuation prompt>"
   ```
4. Repeat until QC passes. Each handoff moves the work forward and lands on a
   fresh limit, never resets and never drains a single agent.

## Definition of done — drive it to closure

You are the closer. The task is done only when it is **actually implemented and
verified**, not when it has been handed off, stubbed, or scheduled for "later".

- **Do not accept deferral.** A worker that responds "this should be done in a
  follow-up", leaves a `TODO`, or says the piece belongs to another agent has
  NOT completed the task. That is a QC failure — send it back.
- **Do not accept "too hard".** Neither from a worker nor from yourself. If an
  agent stalls or claims difficulty, re-scope into smaller units, switch to a
  stronger agent (`--prefer`), retry — keep going. "Hard" is not a stopping
  condition; only a concrete, named blocker (missing credential, missing spec,
  external outage) is — and then you surface that exact blocker, not a vague
  "it's complex".
- **Keep hammering.** Loop delegate → QC → reject → re-delegate until the
  acceptance bar is met. Every task you were given gets closed or its precise
  blocker reported. No silent drops, no "mostly done".

Run `orchestra agents` / `orchestra health` / `orchestra route …` if routing
looks off.
