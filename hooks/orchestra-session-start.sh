#!/usr/bin/env bash
# Orchestra SessionStart hook. Its stdout is injected as session context, so
# Claude knows the orchestrate skill is available from the first message.
# Kept as an availability notice, not a behaviour override — the skill's own
# description handles when to trigger.
cat <<'EOF'
ORCHESTRA AVAILABLE — the `orchestrate` skill delegates coding / refactoring /
review / bug-investigation / docs work to subscription worker CLIs (codex, mimo,
opencode, antigravity, claude) with capability routing, quota fallback, cooldowns
and a quality-control gate. Act as orchestrator: delegate + QC rather than
implementing large tasks yourself. Preview routing with `orchestra route
--task-type <t>`; list agents with `orchestra agents`.
EOF
