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

# 2b. Reliable activation: a pointer in ~/.claude/CLAUDE.md, which loads every
#     session with no approval needed (more dependable than a hook). Idempotent.
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if ! grep -q "skills/orchestrate" "$CLAUDE_MD" 2>/dev/null; then
    mkdir -p "$HOME/.claude"
    cat >> "$CLAUDE_MD" <<'MD'

# orchestra
- **orchestrate** (`~/.claude/skills/orchestrate/SKILL.md`) - delegate coding/refactoring/review/bug/docs work to subscription worker CLIs with routing, fallback, load-spreading and QC; act as orchestrator (delegate + control) rather than implementing large tasks yourself. CLI: `orchestra`. Trigger: `/orchestrate`, or any substantial coding task worth delegating.
MD
    echo "✓ CLAUDE.md       pointer added (loads every session)"
fi

# 2c. SessionStart hook too (belt & suspenders; may need review in the app to run).
chmod +x "$REPO/hooks/orchestra-session-start.sh"
python3 "$REPO/hooks/install-claude-hook.py"

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
