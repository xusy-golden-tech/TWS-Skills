#!/bin/bash
# install-grep-hook.sh - Install the grep-hook PreToolUse hook for Claude Code
#
# Steps:
#   1. Create ~/.claude/hooks/ directory
#   2. Copy grep-hook.sh to ~/.claude/hooks/ and make it executable
#   3. Merge the PreToolUse hook config into ~/.claude/settings.json
#
# Idempotent: safe to run multiple times; will skip if already configured.

set -uo pipefail

# Locate a working Python interpreter (python3 preferred, fallback to python).
_find_python() {
    for py in python3 python; do
        if command -v "$py" >/dev/null 2>&1; then
            if echo "1+1" | "$py" -c "print(2)" 2>/dev/null | grep -q "2"; then
                echo "$py"
                return 0
            fi
        fi
    done
    return 1
}
PYTHON=$(_find_python)
if [ -z "$PYTHON" ]; then
    echo "ERROR: No working Python interpreter found (tried python3, python)"
    exit 1
fi

HOOKS_DIR="$HOME/.claude/hooks"
SETTINGS_FILE="$HOME/.claude/settings.json"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$SCRIPT_DIR/settings.json.template"

echo "=== tws-graph Grep Hook Installer ==="
echo ""

# ── Step 1: Create hooks directory ────────────────────────────────────────
mkdir -p "$HOOKS_DIR"
echo "[1/3] Created $HOOKS_DIR"

# ── Step 2: Install hook script ───────────────────────────────────────────
cp "$SCRIPT_DIR/grep-hook.sh" "$HOOKS_DIR/grep-hook.sh"
chmod +x "$HOOKS_DIR/grep-hook.sh"
echo "[2/3] Installed grep-hook.sh to $HOOKS_DIR/grep-hook.sh"

# ── Step 3: Configure settings.json ───────────────────────────────────────
PYHELPER=$(mktemp 2>/dev/null || echo "/tmp/tws-install-hook-$$.py")
trap 'rm -f "$PYHELPER"' EXIT

cat > "$PYHELPER" << 'PYEOF'
import json, os, sys

template_path = sys.argv[1]
settings_path = os.path.expanduser("~/.claude/settings.json")

# Read the hook entry from template
with open(template_path, 'r') as f:
    template = json.load(f)

new_hook = template['hooks']['PreToolUse'][0]

# Read existing settings (or start fresh)
if os.path.exists(settings_path):
    with open(settings_path, 'r') as f:
        try:
            settings = json.load(f)
        except json.JSONDecodeError:
            print("Warning: invalid JSON in settings.json, creating new")
            settings = {}
else:
    settings = {}

# Ensure the nested structure exists
settings.setdefault('hooks', {}).setdefault('PreToolUse', [])

# Check for duplicate — don't add the same hook twice
existing = settings['hooks']['PreToolUse']
already_installed = any(
    h.get('matcher') == 'Grep' and
    any('grep-hook' in c.get('command', '') for c in h.get('hooks', []))
    for h in existing
)

if already_installed:
    print("grep-hook already configured in settings.json, skipping")
else:
    existing.append(new_hook)
    with open(settings_path, 'w') as f:
        json.dump(settings, f, indent=2)
    print("Added grep-hook to PreToolUse hooks in settings.json")

print("Settings file: " + settings_path)
PYEOF

"$PYTHON" "$PYHELPER" "$TEMPLATE"
echo "[3/3] Configured settings.json"

echo ""
echo "=== Installation Complete ==="
echo ""
echo "To verify:"
echo "  cat ~/.claude/settings.json"
echo "  cat ~/.claude/hooks/grep-hook.sh"
echo ""
echo "To uninstall:"
echo "  1. Remove the Grep matcher entry from ~/.claude/settings.json"
echo "     (delete the entry under hooks.PreToolUse with matcher: Grep)"
echo "  2. Remove the hook script:"
echo "     rm ~/.claude/hooks/grep-hook.sh"
echo ""
echo "Known limitations:"
echo "  - Windows hooks may have compatibility issues (Claude Code known bug)"
echo "  - Heuristic symbol detection may miss edge cases (see grep-hook.sh)"
echo "  - tws-graph must be installed and indexed for the hook to add context"
