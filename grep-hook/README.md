# TWS Grep Hook

PreToolUse hook for Claude Code that intercepts Grep tool calls. When the
search pattern looks like a code symbol name, it queries `tws-graph search`,
extracts the relevant file paths, and **narrows the grep scope** to only those
directories. This reduces grep noise → fewer output tokens.

## How It Works

```
Agent types: Grep(pattern="CodingTaskService")
                  │
                  ▼
         PreToolUse Hook
                  │
    ┌─────────────┴─────────────┐
    │ Is pattern a symbol name? │
    │  (no regex chars, no /)   │
    └─────────────┬─────────────┘
         Yes      │      No
          ▼       │       ▼
  tws-graph search│   Pass through
          │       │
    ┌─────┴─────┐ │
    │ Results?   │ │
    └─────┬─────┘ │
    Yes   │  No   │
     ▼    │   ▼   │
  Narrow  │ Pass  │
  grep    │through│
  path    │       │
     │    │       │
     ▼    ▼       ▼
  Modified tool_input → Claude Code executes Grep with narrowed scope
```

- If tws-graph finds the symbol in `engine/coding_task_service.py`, grep is
  restricted to `path: "engine/"` instead of the whole project.
- If tws-graph finds nothing or the pattern is a regex → pass through unchanged.
- The hook is **non-blocking**: always exits 0. Grep always runs.
- **Interactive mode only**: hooks do not fire in `--print`/`-p` mode (Claude Code limitation).

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

## Test Results (2026-07-02)

| Scenario | Input Pattern | Behavior | Result |
|----------|--------------|----------|--------|
| Symbol name | `CallsResult` | Narrow grep to `tws-graph\rust_core\src\query` | ✅ |
| Symbol with flags | `CodingTaskService` + `output_mode: files_with_matches` | Path narrowed | ✅ |
| Regex pattern | `import.*from` | Pass through unchanged | ✅ |
| Non-Grep tool | `Read(file_path="test.py")` | Pass through unchanged | ✅ |
| Unknown symbol | `NonExistentSymbolXYZ123` | Pass through unchanged (tws-graph returns empty) | ✅ |

## Known Limitations

### Print Mode
Hooks do **not** fire in `claude --print`/`-p` non-interactive mode. This is a
Claude Code limitation, not specific to this hook.

### Heuristic Symbol Detection
The pattern is treated as a symbol name when ALL of these hold:
- Length > 1 character
- Contains none of these regex metacharacters: `[ ] ( ) { } . * + ? ^ $ \ |`
- Is not a file-path pattern (contains no `/` or `**`)

Edge cases:
- `MyClass.method` contains `.` so it passes through (the `.` is a regex wildcard).
- `src/auth/login` contains `/` so it passes through (file path).
- `*.py` contains `*` so it passes through (file glob).
- Plain identifiers like `some_function` or `AppContainer` will be intercepted.

### tws-graph Dependency
The hook needs `tws-graph` on `PATH` and an indexed project. If unavailable,
it silently passes through.

### Performance
Adds a `tws-graph search` call (< 1 second) before qualifying Grep invocations.
Search capped at `--limit 15` results.

### Complement, Not Replacement
This hook narrows grep scope but does not enforce "use tws-graph instead of
grep." The `found-tws-graph-usage` skill is still the primary mechanism for
teaching agents which tws-graph command to use for each task.
