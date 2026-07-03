#!/bin/bash
# grep-hook.sh — PreToolUse hook for Claude Code
#
# v2: Two intervention modes:
#   Mode A (bare symbol): narrows grep scope to files tws-graph identified
#   Mode B (escaped regex): injects tws-graph search suggestions via additionalContext
#   Mode C (pure regex/glob/non-Grep): pass through
#
# Non-blocking: always exits 0. On any failure or non-match, passes through.

INPUT=$(cat)

# ── Find working Python ──────────────────────────────────────────────────────
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
    echo "$INPUT"
    exit 0
fi

# ── Create temp Python script ────────────────────────────────────────────────
PYHELPER=$(mktemp 2>/dev/null || echo "/tmp/tws-grep-hook-$$.py")
trap 'rm -f "$PYHELPER"' EXIT

cat > "$PYHELPER" << 'PYEOF'
import sys, json, subprocess, re, os

try:
    data = json.load(sys.stdin)
except Exception:
    # Pass through: can't parse input
    print(json.dumps(data) if 'data' in dir() else "{}")
    sys.exit(0)

# Only intercept Grep tool calls
tool_name = data.get("tool_name", "")
if tool_name != "Grep":
    print(json.dumps(data))
    sys.exit(0)

tool_input = data.get("tool_input", {})
pattern = tool_input.get("pattern", "")

# ── Heuristic: is this pattern a symbol name? ────────────────────────────────
if not pattern or len(pattern) <= 1:
    print(json.dumps(data))
    sys.exit(0)

# ── Regex metacharacters detected ──────────────────────────────────────────────
if re.search(r'[\[\](){}.*+?^$\\|]', pattern):

    # Branch B: escaped regex (\X) → extract symbol names, query tws-graph
    if re.search(r'\\([\[\](){}.*+?^$|])', pattern):

        # Locate project root via git
        try:
            root = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                stderr=subprocess.DEVNULL, text=True
            ).strip()
        except Exception:
            print(json.dumps(data))
            sys.exit(0)

        # Unescape: replace \X with X for regex metacharacters only
        unescaped = re.sub(r'\\([\[\](){}.*+?^$|])', r'\1', pattern)

        # Extract identifiers >= 3 chars, dedup, take first 5
        tokens = []
        seen_tok = set()
        for tok in re.findall(r'[A-Za-z_]\w{2,}', unescaped):
            if tok not in seen_tok:
                seen_tok.add(tok)
                tokens.append(tok)
                if len(tokens) >= 5:
                    break

        suggestions = []
        if tokens:
            for token in tokens:
                try:
                    result = subprocess.check_output(
                        ["tws-graph", "search", token, "--limit", "5"],
                        cwd=root, stderr=subprocess.DEVNULL, text=True, timeout=8
                    ).strip()
                except Exception:
                    continue
                if result:
                    for line in result.split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("找到") or line.startswith("Found"):
                            continue
                        # Skip "no match" messages
                        if line.startswith("未找到匹配") or line.startswith("No match"):
                            continue
                        suggestions.append(line)

        if suggestions:
            # Deduplicate while preserving order
            seen_s = set()
            uniq = []
            for s in suggestions:
                if s not in seen_s:
                    seen_s.add(s)
                    uniq.append(s)
            ctx_lines = ["[tws-graph] 在代码图中找到以下匹配符号（建议优先查看）："]
            for s in uniq[:10]:
                ctx_lines.append("  " + s)
            ctx_lines.append("  → 提示：tws-graph search <symbol> 可精确查看定义和调用关系")
            data["additionalContext"] = "\n".join(ctx_lines)
            print(json.dumps(data))
            sys.exit(0)

        # Branch B with no results → pass through
        print(json.dumps(data))
        sys.exit(0)

    # Not Branch B (pure regex without \escaping) → pass through
    print(json.dumps(data))
    sys.exit(0)

# File-path / glob patterns
if re.search(r'\*\*|/', pattern):
    print(json.dumps(data))
    sys.exit(0)

# ── Locate project root via git ─────────────────────────────────────────────
try:
    root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        stderr=subprocess.DEVNULL, text=True
    ).strip()
except Exception:
    print(json.dumps(data))
    sys.exit(0)

# ── Query tws-graph ──────────────────────────────────────────────────────────
try:
    result = subprocess.check_output(
        ["tws-graph", "search", pattern, "--limit", "15"],
        cwd=root, stderr=subprocess.STDOUT, text=True, timeout=10
    ).strip()
except Exception:
    # tws-graph failed → pass through
    print(json.dumps(data))
    sys.exit(0)

if not result:
    print(json.dumps(data))
    sys.exit(0)

# ── Extract file paths from tws-graph output ─────────────────────────────────
# Supports multiple output formats:
#   New: "  name [kind] (lang) path/to/file.py:line"
#   Old: "(kind):name @ path/to/file.py"
#   Rich: "|-- (kind):name | sig: ... | line: N | @ path"
file_paths = set()
for line in result.split("\n"):
    line = line.strip()
    if not line:
        continue
    # Skip the "Found N results:" header line
    if line.startswith("找到") or line.startswith("Found"):
        continue

    path = None
    # Format 1: name [kind] (lang) path:line
    m = re.search(r'\)\s+(\S+):\d+\s*$', line)
    if m:
        path = m.group(1)
    else:
        # Format 2/3: "@ path" somewhere in line
        for sep in (" @ ", " @"):
            if sep in line:
                path = line.rsplit(sep, 1)[-1].strip().rstrip(':')
                break

    if path:
        file_paths.add(path)

if not file_paths:
    print(json.dumps(data))
    sys.exit(0)

# ── Find common parent directory ─────────────────────────────────────────────
def common_parent(paths, root):
    """Find the most specific common parent directory of all paths."""
    # Normalize root to native format for reliable startswith comparison
    root_native = os.path.normpath(root)
    dirs = []
    for p in paths:
        d = os.path.normpath(os.path.join(root_native, p))
        d = os.path.dirname(d)
        if os.path.isdir(d):
            dirs.append(d)

    if not dirs:
        return None

    common = os.path.commonpath(dirs)
    if os.path.normpath(common).startswith(root_native):
        return os.path.relpath(common, root_native)
    return None

narrow_path = common_parent(file_paths, root)

# ── Modify tool_input to narrow grep scope ───────────────────────────────────
# Only set path if it's more specific than the current setting (if any)
existing_path = tool_input.get("path", "")
if narrow_path and narrow_path != ".":
    if not existing_path or len(narrow_path) > len(existing_path):
        tool_input["path"] = narrow_path

data["tool_input"] = tool_input
print(json.dumps(data))
PYEOF

# Execute Python helper. On any failure, pass through.
echo "$INPUT" | "$PYTHON" "$PYHELPER" 2>/dev/null || echo "$INPUT"

exit 0
