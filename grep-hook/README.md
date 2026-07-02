# TWS Grep Hook

PreToolUse hook for Claude Code that intercepts Grep tool calls and suggests
tws-graph search results when the pattern looks like a code symbol name.

## How It Works

The hook script (`grep-hook.sh`) runs before every Grep tool call in Claude
Code. It inspects the search pattern:

- If the pattern looks like a **symbol name** (bare identifier, no regex
  metacharacters, no file-path syntax) -- it runs `tws-graph search` on the
  pattern and injects the results as `additionalContext` so the agent sees
  structured code-graph results alongside grep output.
- If the pattern looks like a **regex or file glob** -- it passes through
  without intervention so legitimate grep usage is never disrupted.

The hook is **non-blocking**: it always exits 0. It never prevents Grep from
executing. It only adds information.

## Installation

```bash
cd grep-hook/
bash install-grep-hook.sh
```

This will:
1. Create `~/.claude/hooks/` directory
2. Copy `grep-hook.sh` to `~/.claude/hooks/grep-hook.sh` and make it executable
3. Configure `~/.claude/settings.json` with the PreToolUse hook entry

## Verification

1. Confirm the hook script is in place:
   ```bash
   ls -la ~/.claude/hooks/grep-hook.sh
   ```

2. Confirm the hook is registered in Claude Code settings:
   ```bash
   cat ~/.claude/settings.json
   ```
   Look for `"PreToolUse"` under `"hooks"` containing a matcher for `"Grep"`
   that references `grep-hook.sh`.

3. Test in Claude Code: search for a known symbol (e.g. a class or function
   name) using the Grep tool. If tws-graph has indexed the project, the hook
   will add tws-graph search results as additionalContext in the tool response.

## Uninstallation

1. Edit `~/.claude/settings.json` and remove the entry under
   `hooks.PreToolUse` that has `"matcher": "Grep"` and references
   `grep-hook.sh`. If that was the only PreToolUse hook, you may remove the
   entire `PreToolUse` array or `hooks` block.

2. Remove the hook script:
   ```bash
   rm ~/.claude/hooks/grep-hook.sh
   ```

## Known Limitations

### Windows Hook Compatibility
Claude Code hooks on Windows have known bugs. The hook may not trigger
reliably in Windows environments. This is a Claude Code platform issue,
not specific to this hook.

### Heuristic Symbol Detection
The script uses simple heuristics to decide whether a pattern is a symbol
name. The pattern is treated as a symbol name when ALL of these hold:
- Length > 1 character
- Contains none of these regex metacharacters: `[ ] ( ) { } . * + ? ^ $ \ |`
- Is not a file-path pattern (contains no `/` or `**`)

Edge cases:
- `MyClass.method` contains `.` so it passes through (the `.` is a regex
  wildcard -- we err on the side of caution).
- `src/auth/login` contains `/` so it passes through (file path).
- `kind:class` contains `:` but no regex chars, so it would trigger tws-graph
  search. This happens to be correct since tws-graph supports qualifiers.
- `*.py` contains `*` so it passes through (file glob).
- Plain strings like `some_function` or `AppContainer` will be intercepted
  correctly.

### tws-graph Dependency
The hook only works if:
- `tws-graph` is installed and on `PATH`
- The project has been indexed (`tws-graph index` has been run)

If either condition is not met, the hook silently passes through.

### Performance
The hook adds a `tws-graph search` call (typically < 1 second) before every
qualifying Grep invocation. For projects with large indexes this may add
slight latency. The search is capped at `--limit 15` and output is truncated
to 20 lines to keep context size reasonable.

### Git Requirement
The hook uses `git rev-parse --show-toplevel` to locate the project root.
It will not function in directories that are not inside a git repository.

### Non-Blocking Only
This hook operates in advisory mode only. It cannot enforce the rule that
"symbol searches must use tws-graph." It only suggests. The skill
`found-tws-graph-usage` is still the primary enforcement mechanism via
agent instructions.
