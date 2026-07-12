#!/usr/bin/env bash
# Install orchestra: the CLI, the Claude Code skill, and an AGENTS.md that
# codex / opencode / mimo pick up (so any of those CLIs can drive orchestration).
# Idempotent — safe to re-run after `git pull`.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"

# 1. the CLI (editable, so pulls take effect without reinstall)
pip install -e "$REPO"

# 2. Claude Code skill  ->  ~/.claude/skills/orchestrate
mkdir -p "$HOME/.claude/skills"
ln -sfn "$REPO/skills/orchestrate" "$HOME/.claude/skills/orchestrate"
echo "✓ Claude skill    ~/.claude/skills/orchestrate"

# 3. AGENTS.md for the other CLIs — generated from SKILL.md (frontmatter stripped),
#    single source of truth. codex/opencode/mimo read AGENTS.md from cwd or home.
awk 'BEGIN{f=0} /^---$/{f++; next} f>=2{print}' \
    "$REPO/skills/orchestrate/SKILL.md" > "$REPO/AGENTS.md"
echo "✓ AGENTS.md       $REPO/AGENTS.md (read when a worker CLI runs in this repo)"

# 4. global AGENTS.md for CLIs whose config dir exists (so it works anywhere)
for dir in "$HOME/.codex" "$HOME/.config/opencode" "$HOME/.mimocode"; do
    if [ -d "$dir" ]; then
        ln -sfn "$REPO/AGENTS.md" "$dir/AGENTS.md"
        echo "✓ global instr    $dir/AGENTS.md"
    fi
done

echo
echo "Point tools at your config:  export ORCHESTRA_CONFIG=$REPO/config"
echo "Edit routing:                $REPO/config/routing.yml"
echo "Edit an agent's priority:    $REPO/config/agents/<name>.yml  (priority: lower = preferred)"
echo "Check the effect (no quota): orchestra route --task-type refactoring"
