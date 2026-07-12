#!/usr/bin/env bash
# Install orchestra: the CLI, the Claude Code skill, and an AGENTS.md that
# codex / opencode / mimo pick up (so any of those CLIs can drive orchestration).
# Idempotent — safe to re-run after `git pull`.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"

# 1. the CLI (editable, so pulls take effect without reinstall). This puts the
#    global `orchestra` command on PATH via the console-script entry point.
#    Prefer pipx for an isolated global install; fall back to pip.
if command -v pipx >/dev/null 2>&1; then
    pipx install --force -e "$REPO"
else
    pip install -e "$REPO"
fi

# 1b. editable user config in ~/.config/orchestra so `orchestra` works anywhere
#     without ORCHESTRA_CONFIG. Idempotent (won't clobber an existing config).
orchestra init || true

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
echo "The 'orchestra' command now works from any directory. Try:"
echo "  orchestra config --edit          # open your settings (~/.config/orchestra)"
echo "  orchestra route --task-type refactoring   # preview routing, no quota"
echo "  orchestra agents                 # list agents, priorities, cooldowns"
