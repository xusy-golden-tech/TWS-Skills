#!/bin/bash
# grep-hook.sh - PreToolUse hook for tws-graph
# Intercepts Grep tool calls and suggests tws-graph search results when the
# pattern looks like a code symbol name (no regex special chars, not a file path).
#
# Non-blocking: always exits 0. On symbol-name match, injects tws-graph search
# results as additionalContext. On any failure or non-match, passes through silently.

INPUT=$(cat)

# Locate a working Python interpreter (python3 preferred, fallback to python).
# Some environments (e.g. pyenv-win) have a non-functional python3 shim.
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
    echo "{}"
    exit 0
fi

# Create temporary Python helper script for JSON processing.
# We use Python because pure-bash JSON parsing is fragile, and jq may not be
# available on all systems. Python 3 is a reasonable baseline.
PYHELPER=$(mktemp 2>/dev/null || echo "/tmp/tws-grep-hook-$$.py")
trap 'rm -f "$PYHELPER"' EXIT

cat > "$PYHELPER" << 'PYEOF'
import sys, json, subprocess, re

try:
    data = json.load(sys.stdin)
except Exception:
    print("{}")
    sys.exit(0)

# Only intercept Grep tool calls
tool_name = data.get("tool_name", "")
if tool_name != "Grep":
    print(json.dumps(data))
    sys.exit(0)

# Extract the search pattern
pattern = data.get("tool_input", {}).get("pattern", "")

# ── Heuristic: is this pattern likely a symbol name? ──────────────────────
# We look for bare identifiers: no regex metacharacters, no file-path syntax.
# If the pattern looks like a regex or glob, we pass through without
# intervention so we never interfere with legitimate grep usage.

if not pattern or len(pattern) <= 1:
    print(json.dumps(data))
    sys.exit(0)

# Regex special characters that indicate a regex, not a plain symbol name
if re.search(r'[\[\](){}.*+?^$\\|]', pattern):
    print(json.dumps(data))
    sys.exit(0)

# File-path / glob patterns (** globs, any pattern containing /)
if re.search(r'\*\*|/', pattern):
    print(json.dumps(data))
    sys.exit(0)

# ── Locate project root via git ───────────────────────────────────────────
try:
    root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        stderr=subprocess.DEVNULL, text=True
    ).strip()
except Exception:
    print(json.dumps(data))
    sys.exit(0)

# ── Query tws-graph ───────────────────────────────────────────────────────
try:
    result = subprocess.check_output(
        ["tws-graph", "search", pattern, "--limit", "15"],
        cwd=root, stderr=subprocess.STDOUT, text=True, timeout=10
    ).strip()
except Exception:
    print(json.dumps(data))
    sys.exit(0)

# Empty result means no symbols found — let Grep handle it
if not result:
    print(json.dumps(data))
    sys.exit(0)

# ── Format output ─────────────────────────────────────────────────────────
# Truncate excessively long output
lines = result.split("\n")
if len(lines) > 20:
    result = "\n".join(lines[:20]) + "\n... (truncated)"

context = (
    'tws-graph found symbols for pattern "' + pattern + '":\n' +
    result + '\n\n' +
    'Prefer tws-graph search results over Grep for symbol searches.'
)

output = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": context
    }
}
print(json.dumps(output))
PYEOF

# Execute Python helper with the hook input on stdin.
# On any failure (python not found, parse error, etc.), output empty JSON
# to allow the tool call without modification.
echo "$INPUT" | "$PYTHON" "$PYHELPER" 2>/dev/null || echo '{}'

exit 0
