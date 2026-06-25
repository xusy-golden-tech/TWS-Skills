"""MCP 2.0+ development assistant tools — P44 + P46.

Provides:
- review_changes: Code review assistance — impact analysis for changed files
- safe_refactor: Refactoring safety check — full modification checklist
- api_compat_check: API compatibility check — breaking change detection
- find_pattern: AST-based structural pattern search
- security_scan: Security vulnerability detection (P46b) — SQL injection, secrets, etc.

All tools are pure offline — zero HTTP dependencies.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Callable, Optional

from tws_graph.mcp.registry import ToolRegistry, ToolDefinition
from tws_graph.store.interface import Store

StoreFactory = Callable[[], Store]


def register_tools(registry: ToolRegistry, store_factory: StoreFactory) -> None:
    """Register all P44 development assistant tools."""

    # -- review_changes ---------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="review_changes",
            description=(
                "Perform automated code review for a set of changed files. "
                "Analyzes what downstream code is impacted by each change, "
                "classifies risk levels (high/medium/low), and suggests "
                "relevant tests to run. Pure offline analysis — no external "
                "API calls. Input: list of changed file paths or symbol names."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of changed file paths to review",
                    },
                    "symbol_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of changed symbol names (alternative to file_paths)",
                    },
                },
            },
        ),
        handler=lambda args: _review_changes(store_factory(), args),
    )

    # -- safe_refactor ----------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="safe_refactor",
            description=(
                "Check whether a planned refactoring is safe to perform. "
                "Given a symbol name and the type of change (rename, move, "
                "change_signature, delete, extract), analyzes the full impact "
                "radius and returns a complete checklist of files that need "
                "to be updated. Returns 'safe' or 'unsafe' with detailed reasoning."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_name": {
                        "type": "string",
                        "description": "Name of the symbol to refactor (e.g. function name, class name)",
                    },
                    "change_type": {
                        "type": "string",
                        "enum": ["rename", "move", "change_signature", "delete", "extract"],
                        "description": "Type of refactoring to perform",
                    },
                },
                "required": ["symbol_name", "change_type"],
            },
        ),
        handler=lambda args: _safe_refactor(store_factory(), args),
    )

    # -- api_compat_check -------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="api_compat_check",
            description=(
                "Check API compatibility between two versions of a symbol. "
                "Compares signatures, visibility, parameters, and downstream "
                "dependents. Recommends semantic version bump level "
                "(major/minor/patch) following semver conventions. "
                "Useful for public API evolution and library releases."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "symbol_name": {
                        "type": "string",
                        "description": "Name of the API symbol to check (function, class, or method)",
                    },
                    "old_signature": {
                        "type": "string",
                        "description": "The original signature string",
                    },
                    "new_signature": {
                        "type": "string",
                        "description": "The new/modified signature string",
                    },
                },
                "required": ["symbol_name"],
            },
        ),
        handler=lambda args: _api_compat_check(store_factory(), args),
    )

    # -- find_pattern -----------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="find_pattern",
            description=(
                "Search for structural code patterns across the indexed codebase "
                "using AST-aware matching. Unlike text search, this understands "
                "code structure. Examples: 'function with try/except', "
                "'class with decorator', 'nested for loop', 'async function calling await'. "
                "Returns matching file paths and line numbers with snippets."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Structural pattern description (e.g. 'function with try_except', 'class with decorator')",
                    },
                    "language": {
                        "type": "string",
                        "description": "Filter to a specific language (e.g. 'python', 'typescript')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 50)",
                        "default": 50,
                    },
                },
                "required": ["pattern"],
            },
        ),
        handler=lambda args: _find_pattern(store_factory(), args),
    )

    # -- security_scan ----------------------------------------------------------
    registry.register(
        ToolDefinition(
            name="security_scan",
            description=(
                "Scan the indexed codebase for security vulnerabilities using "
                "pattern-based static analysis. Detects: SQL injection (string "
                "concatenation in queries), hardcoded secrets (passwords, API keys, "
                "tokens), path traversal (user input in file paths), command injection "
                "(shell command concatenation), and unsafe deserialization. "
                "Each finding includes severity, file location, and remediation hints. "
                "Pure offline — zero external dependencies."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "description": "Filter to a specific language (e.g. 'python', 'java', 'typescript')",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low", "info"],
                        "description": "Filter findings by minimum severity level",
                    },
                },
            },
        ),
        handler=lambda args: _security_scan(store_factory(), args),
    )


# ============================================================================
# Helpers
# ============================================================================


def _get(obj, attr: str, default=None):
    """Get attribute from dict or object — handle both MemoryStore and SqliteStore.

    MemoryStore.fts_search returns dicts with keys: id, name, qualified_name, kind, ...
    SqliteStore.fts_search returns SearchResult with attributes: node_id, node_name, ...
    This helper normalizes both naming conventions.
    """
    # Map to MemoryStore dict key names
    key_map = {
        "node_id": "id",
        "node_name": "name",
    }
    if isinstance(obj, dict):
        # Try original attr first, then mapped key
        if attr in obj:
            return obj[attr]
        mapped = key_map.get(attr)
        if mapped and mapped in obj:
            return obj[mapped]
        return default
    return getattr(obj, attr, default)


# ============================================================================
# Handler implementations
# ============================================================================


def _review_changes(store: Store, args: Optional[dict]) -> dict:
    """Analyze changed files for code review."""
    file_paths = args.get("file_paths", []) if args else []
    symbol_names = args.get("symbol_names", []) if args else []

    if not file_paths and not symbol_names:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "error": "Either file_paths or symbol_names must be provided",
                    "results": [],
                }, ensure_ascii=False),
            }],
        }

    try:
        results = []
        high_risk = 0
        medium_risk = 0
        low_risk = 0
        all_affected_tests: set[str] = set()

        # Resolve symbol names to node ids
        node_ids: set[str] = set()
        for name in symbol_names:
            matches = store.fts_search(name, limit=10)
            for m in matches:
                if _get(m, "node_name") == name or name in _get(m, "qualified_name"):
                    node_ids.add(_get(m, "node_id"))

        # For each file, find its symbols and their dependents
        for fp in file_paths:
            try:
                file_nodes = list(store.iter_nodes_by_file(fp))
            except Exception:
                file_nodes = []

            for fn in file_nodes:
                node_ids.add(_get(fn, "id"))

        # Analyze impact for each node
        for nid in list(node_ids)[:50]:  # limit to 50 symbols
            node = store.get_node_by_id(nid)
            if not node:
                continue

            incoming = store.get_incoming_edges(nid)
            dependents = [
                e for e in incoming
                if _get(e, "kind") in ("calls", "references", "imports", "inherits")
            ]

            # Risk classification
            dep_count = len(dependents)
            if dep_count > 20:
                risk = "high"
                high_risk += 1
            elif dep_count > 5:
                risk = "medium"
                medium_risk += 1
            else:
                risk = "low"
                low_risk += 1

            affected_files: set[str] = set()
            for e in dependents:
                src_node = store.get_node_by_id(_get(e, "source"))
                if src_node:
                    affected_files.add(_get(src_node, "file_path"))
                    # Check if this is a test file
                    if "test" in _get(src_node, "file_path").lower():
                        all_affected_tests.add(_get(src_node, "file_path"))

            # Build suggestion
            suggestions = []
            if risk == "high":
                suggestions.append("Consider adding/updating tests before merging")
                suggestions.append("Notify team about breaking change risk")
            if any("test" not in fp.lower() for fp in affected_files):
                suggestions.append("Verify affected downstream modules")

            results.append({
                "symbol": _get(node, "name") or _get(node, "qualified_name"),
                "file": _get(node, "file_path"),
                "kind": _get(node, "kind"),
                "line": _get(node, "start_line"),
                "dependent_count": dep_count,
                "risk_level": risk,
                "affected_files": sorted(affected_files)[:15],
                "suggestions": suggestions,
            })

        output = {
            "results": results,
            "count": len(results),
            "risk_summary": {
                "high": high_risk,
                "medium": medium_risk,
                "low": low_risk,
            },
            "suggested_tests": sorted(all_affected_tests)[:20],
            "quality_gate": _compute_quality_gate(store, node_ids, file_paths),
        }
    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _safe_refactor(store: Store, args: Optional[dict]) -> dict:
    """Check refactoring safety."""
    symbol_name = args.get("symbol_name", "") if args else ""
    change_type = args.get("change_type", "rename") if args else "rename"

    if not symbol_name:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({"error": "symbol_name is required", "results": []}, ensure_ascii=False),
            }],
        }

    try:
        # Find the symbol
        matches = store.fts_search(symbol_name, limit=20)
        target_nodes = []
        for m in matches:
            if _get(m, "node_name") == symbol_name or symbol_name in _get(m, "qualified_name"):
                target_nodes.append(m)

        if not target_nodes:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "error": f"Symbol '{symbol_name}' not found in code graph",
                        "safe": False,
                        "reason": "Symbol not found — may be external or not indexed",
                    }, ensure_ascii=False),
                }],
            }

        # Suggest mode: analyze symbol for refactoring opportunities (P46a)
        if change_type == "suggest":
            return _safe_refactor_suggest(store, target_nodes, symbol_name)

        all_affected: dict[str, list[str]] = defaultdict(list)  # file_path -> [reason]
        total_dependents = 0
        symbol_details = []

        for match in target_nodes[:5]:
            node = store.get_node_by_id(_get(match, "node_id"))
            if not node:
                continue

            symbol_details.append({
                "id": _get(node, "id"),
                "name": _get(node, "name"),
                "qualified_name": _get(node, "qualified_name"),
                "file": _get(node, "file_path"),
                "kind": _get(node, "kind"),
                "line": _get(node, "start_line"),
                "visibility": _get(node, "visibility"),
            })

            # Get all incoming edges (who depends on this)
            incoming = store.get_incoming_edges(_get(node, "id"))

            for e in incoming:
                if _get(e, "kind") not in ("calls", "references", "imports", "inherits", "contains"):
                    continue
                total_dependents += 1
                src_node = store.get_node_by_id(_get(e, "source"))
                if src_node:
                    reason = f"{_get(src_node, "name") or _get(src_node, "qualified_name")} ({_get(e, "kind")})"
                    all_affected[_get(src_node, "file_path")].append(reason)

        # Safety analysis
        affected_files = list(all_affected.keys())
        is_public = any(n["visibility"] == "public" for n in symbol_details)

        safety_issues = []
        if change_type == "delete" and total_dependents > 0:
            safety_issues.append(f"Cannot delete — {total_dependents} dependents exist")
        if change_type == "change_signature" and is_public:
            safety_issues.append("Public API signature change — may break external consumers")
        if change_type == "move" and len(affected_files) > 20:
            safety_issues.append(f"Moving affects {len(affected_files)} files — high coordination cost")
        if is_public and total_dependents > 10:
            safety_issues.append("Many dependents on public API — consider deprecation first")

        is_safe = len(safety_issues) == 0

        # Build checklist
        checklist = []
        for fp in sorted(affected_files)[:30]:
            reasons = all_affected[fp]
            checklist.append({
                "file": fp,
                "needs_update": True,
                "reasons": reasons[:5],
            })

        output = {
            "safe": is_safe,
            "change_type": change_type,
            "symbol": symbol_details,
            "total_dependents": total_dependents,
            "affected_file_count": len(affected_files),
            "issues": safety_issues if safety_issues else ["No issues detected"],
            "checklist": checklist,
            "recommendation": (
                "Safe to proceed" if is_safe
                else "Address issues above before refactoring"
            ),
        }
    except Exception as e:
        output = {"error": str(e), "safe": False}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _api_compat_check(store: Store, args: Optional[dict]) -> dict:
    """Check API compatibility between two signatures."""
    symbol_name = args.get("symbol_name", "") if args else ""
    old_sig = args.get("old_signature", "") if args else ""
    new_sig = args.get("new_signature", "") if args else ""

    if not symbol_name:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({"error": "symbol_name is required"}, ensure_ascii=False),
            }],
        }

    try:
        # Find the symbol
        matches = store.fts_search(symbol_name, limit=10)
        target = None
        for m in matches:
            if _get(m, "node_name") == symbol_name or symbol_name in _get(m, "qualified_name"):
                target = m
                break

        if not target:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "symbol": symbol_name,
                        "compatibility": "unknown",
                        "bump": "patch",
                        "reason": "Symbol not found in code graph — assuming minor change",
                    }, ensure_ascii=False),
                }],
            }

        node = store.get_node_by_id(_get(target, "node_id"))
        if not node:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "symbol": symbol_name,
                        "compatibility": "unknown",
                        "bump": "patch",
                    }, ensure_ascii=False),
                }],
            }

        # Analyze compatibility
        incompatibilities = []
        bump = "patch"

        # Check visibility change (public → private = breaking)
        if old_sig and new_sig:
            # Basic signature comparison
            if old_sig != new_sig:
                # Check if parameter count changed
                old_params = old_sig.count(",")
                new_params = new_sig.count(",")
                if new_params < old_params:
                    incompatibilities.append("Parameter count decreased — removing parameters")
                    bump = "major"
                elif new_params > old_params and old_sig != new_sig:
                    # Adding optional params = minor, adding required params = major
                    if "=" not in new_sig:
                        incompatibilities.append("New required parameter added")
                        bump = "major"
                    else:
                        bump = max_bump(bump, "minor")

        # Analyze downstream impact
        incoming = store.get_incoming_edges(_get(node, "id"))
        dependents = [
            e for e in incoming
            if _get(e, "kind") in ("calls", "references", "imports", "inherits")
        ]

        dep_count = len(dependents)
        dep_files: set[str] = set()
        for e in dependents[:100]:
            src_node = store.get_node_by_id(_get(e, "source"))
            if src_node:
                dep_files.add(_get(src_node, "file_path"))

        if bump == "major":
            compatibility = "breaking"
        elif bump == "minor":
            compatibility = "compatible_with_deprecation"
        elif dep_count > 50:
            compatibility = "compatible_but_risky"
        else:
            compatibility = "compatible"

        output = {
            "symbol": symbol_name,
            "qualified_name": _get(node, "qualified_name"),
            "kind": _get(node, "kind"),
            "visibility": _get(node, "visibility"),
            "current_signature": _get(node, "signature"),
            "old_signature": old_sig or _get(node, "signature"),
            "new_signature": new_sig or _get(node, "signature"),
            "compatibility": compatibility,
            "bump": bump,
            "incompatibilities": incompatibilities if incompatibilities else ["No breaking changes detected"],
            "dependent_count": dep_count,
            "dependent_files": sorted(dep_files)[:20],
            "semver_guidance": {
                "patch": "Backward-compatible bug fix — safe to release",
                "minor": "Backward-compatible new functionality — deprecate old API",
                "major": "Breaking change — coordinate with dependents, bump major version",
            }.get(bump, "Unknown"),
        }
    except Exception as e:
        output = {"error": str(e), "compatibility": "unknown"}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _find_pattern(store: Store, args: Optional[dict]) -> dict:
    """Search for structural code patterns with smart enhancements (P46d)."""
    import re

    pattern = args.get("pattern", "") if args else ""
    language = args.get("language") if args else None
    limit = args.get("limit", 50) if args else 50

    if not pattern:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({"error": "pattern is required", "results": []}, ensure_ascii=False),
            }],
        }

    # Synonym map for smart expansion (P46d)
    SYNONYM_MAP = {
        "auth": ["authenticate", "authorize", "authorization", "login", "logout",
                 "credential", "session", "token", "permission"],
        "db": ["database", "sql", "query", "execute", "connection", "cursor"],
        "http": ["request", "response", "api", "rest", "endpoint", "url", "fetch"],
        "file": ["open", "read", "write", "io", "path", "directory", "file"],
    }

    # Structural pattern map
    pattern_map = {
        "try_except": "try_statement",
        "try_catch": "try_statement",
        "for_loop": "for_statement",
        "while_loop": "while_statement",
        "async_function": "async",
        "decorator": "decorator",
        "class_with_decorator": "decorator",
        "nested_loop": "for_statement",
        "context_manager": "with_statement",
        "list_comprehension": "list_comprehension",
        "lambda": "lambda",
        "generator": "generator",
        "switch": "switch",
        "match": "match",
    }

    pattern_lower = pattern.lower().replace(" ", "_").replace("-", "_")

    try:
        expanded_terms: list[str] = []
        is_body_search = False
        body_pattern = None
        is_structural = False
        structural_key = None

        # Check for body: prefix (P46d)
        if pattern_lower.startswith("body:"):
            is_body_search = True
            body_pattern = pattern[5:]  # Remove "body:" prefix
        # Check for structural pattern keys
        else:
            for key in pattern_map:
                if key in pattern_lower:
                    is_structural = True
                    structural_key = key
                    break

        # Synonym expansion (P46d)
        if not is_body_search and not is_structural:
            search_terms = [pattern]
            for syn_key, synonyms in SYNONYM_MAP.items():
                if syn_key in pattern_lower:
                    search_terms.extend(synonyms)
                    expanded_terms = synonyms
                    break
            if not expanded_terms:
                search_terms = [pattern]
        elif is_structural:
            search_terms = [pattern_map[structural_key]]
        else:
            search_terms = []

        results_map: dict[str, list[dict]] = {}  # file_path -> [matches]

        # FTS name-based search (for synonym and basic patterns)
        for term in search_terms:
            matches = store.fts_search(term, limit=limit, language_filter=language)
            for m in matches:
                fp = _get(m, "file_path", "")
                if fp not in results_map:
                    results_map[fp] = []
                if len(results_map[fp]) < 5:
                    results_map[fp].append({
                        "name": _get(m, "node_name") or _get(m, "name"),
                        "kind": _get(m, "kind"),
                        "line": _get(m, "start_line"),
                        "qualified_name": _get(m, "qualified_name"),
                    })

        # Body search: iterate all nodes and check body content
        if is_body_search or is_structural:
            body_regex = None
            if is_body_search and body_pattern:
                body_regex = re.compile(re.escape(body_pattern), re.IGNORECASE)
            elif is_structural and structural_key in ("try_except", "try_catch"):
                body_regex = re.compile(r'try\s*:', re.IGNORECASE)
            elif is_structural and structural_key in ("for_loop",):
                body_regex = re.compile(r'for\s+\w+\s+in\s+', re.IGNORECASE)
            elif is_structural and structural_key in ("while_loop",):
                body_regex = re.compile(r'while\s+', re.IGNORECASE)
            elif is_structural and structural_key in ("context_manager",):
                body_regex = re.compile(r'with\s+', re.IGNORECASE)
            elif is_structural and structural_key in ("lambda",):
                body_regex = re.compile(r'lambda\s+', re.IGNORECASE)
            elif is_structural and structural_key in ("decorator", "class_with_decorator"):
                body_regex = re.compile(r'@\w+', re.IGNORECASE)

            if body_regex:
                for node in store.iter_all_nodes():
                    node_lang = _get(node, "language", "")
                    if language and node_lang != language:
                        continue
                    body = _get(node, "body") or ""
                    if body_regex.search(body):
                        fp = _get(node, "file_path", "")
                        if fp not in results_map:
                            results_map[fp] = []
                        if len(results_map[fp]) < 5:
                            results_map[fp].append({
                                "name": _get(node, "name"),
                                "kind": _get(node, "kind"),
                                "line": _get(node, "start_line"),
                                "qualified_name": _get(node, "qualified_name"),
                            })

        # Build output
        results = []
        for fp in sorted(results_map.keys()):
            results.append({
                "file": fp,
                "language": results_map[fp][0].get("language", ""),
                "matches": results_map[fp][:5],
            })

        results = results[:limit]

        kinds_found: dict[str, int] = {}
        for r in results:
            for m in r["matches"]:
                k = m.get("kind", "unknown")
                kinds_found[k] = kinds_found.get(k, 0) + 1

        output = {
            "pattern": pattern,
            "language_filter": language,
            "results": results,
            "total_files": len(results),
            "kinds_found": kinds_found,
            "note": (
                "Pattern search is AST-aware — results show symbols matching "
                "the structural pattern. For exact text matches, use the "
                "'search' tool instead."
            ),
        }

        # Add expanded terms info if synonym search was used (P46d)
        if expanded_terms:
            output["expanded_terms"] = expanded_terms
            output["note"] = (
                f"Synonym expansion applied: '{pattern}' → also searched for "
                f"{', '.join(expanded_terms[:5])}"
                + ("..." if len(expanded_terms) > 5 else "")
            )

        if is_body_search:
            output["note"] = f"Body search for pattern: '{body_pattern}'"
        elif is_structural:
            output["note"] = f"Structural pattern: '{structural_key}' matched via body AST analysis"

    except Exception as e:
        output = {"error": str(e), "results": []}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _security_scan(store: Store, args: Optional[dict]) -> dict:
    """Scan codebase for security vulnerabilities using pattern matching."""
    import re

    language = args.get("language") if args else None
    severity_filter = args.get("severity") if args else None

    try:
        findings: list[dict] = []
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

        # Iterate all nodes, filtering to function/method/constructor
        target_kinds = ("function", "method", "constructor")

        for node in store.iter_all_nodes():
            kind = _get(node, "kind")
            if kind not in target_kinds:
                continue

            node_lang = _get(node, "language", "")
            if language and node_lang != language:
                continue

            body = _get(node, "body") or ""
            if not body:
                continue

            file_path = _get(node, "file_path", "")
            line = _get(node, "start_line", 0)
            name = _get(node, "name", "")

            # -- SQL Injection -------------------------------------------------
            sql_patterns = [
                (r'(?:execute|query|executemany)\s*\(\s*(?:[\"\']\s*\+|[\"\'].*?\+\s*\w|\+\s*.*?[\"\']|f\"[^\"]*\{)', "high"),
                (r'\"\s*SELECT\s+.*\"\s*\+\s*\w+', "high"),
                (r'[\"\']\s*\+\s*\w+\s*\+\s*[\"\']', "medium"),
            ]
            for pattern, sev in sql_patterns:
                if re.search(pattern, body, re.IGNORECASE):
                    findings.append({
                        "category": "sql_injection",
                        "severity": sev,
                        "file_path": file_path,
                        "line": line,
                        "symbol": name,
                        "language": node_lang,
                        "message": "Potential SQL injection: string concatenation in SQL query",
                        "remediation": "Use parameterized queries (e.g. '?' placeholders) instead of string concatenation",
                    })
                    break

            # -- Hardcoded Secrets ---------------------------------------------
            secret_patterns = [
                (r'(?:password|passwd|pwd)\s*[:=]\s*[\"\'](?!\s*[\"\'])(.+?)[\"\']', "critical"),
                (r'(?:secret|api_key|apikey|api_secret)\s*[:=]\s*[\"\'](?!\s*[\"\'])(.+?)[\"\']', "critical"),
                (r'(?:token|auth_token|access_key)\s*[:=]\s*[\"\'](?!\s*[\"\'])(.+?)[\"\']', "high"),
                (r'(?:private_key|secret_key)\s*[:=]\s*[\"\'](?!\s*[\"\'])(.+?)[\"\']', "critical"),
            ]
            for pattern, sev in secret_patterns:
                if re.search(pattern, body, re.IGNORECASE):
                    findings.append({
                        "category": "hardcoded_secret",
                        "severity": sev,
                        "file_path": file_path,
                        "line": line,
                        "symbol": name,
                        "language": node_lang,
                        "message": "Hardcoded secret detected — credentials in source code",
                        "remediation": "Move secrets to environment variables or a secure vault (e.g. os.environ.get('SECRET'))",
                    })
                    break

            # -- Path Traversal ------------------------------------------------
            # Two indicators: string concatenation + file operations in same function
            has_path_concat = bool(re.search(
                r'[\"\'](?:[^\"\']*\/[^\"\']*)?[\"\']\s*\+|\+\s*[\"\']',
                body
            ))
            has_file_op = bool(re.search(
                r'(?:open|file|File|FileInputStream|FileReader|Files\.)\s*\(',
                body
            ))
            if has_path_concat and has_file_op:
                findings.append({
                    "category": "path_traversal",
                    "severity": "high",
                    "file_path": file_path,
                    "line": line,
                    "symbol": name,
                    "language": node_lang,
                    "message": "Potential path traversal: string concatenation combined with file operations",
                    "remediation": "Validate and sanitize file paths. Use a whitelist of allowed directories.",
                })

            # -- Command Injection ---------------------------------------------
            cmd_patterns = [
                (r'os\.system\s*\(\s*(?:[\"\'].*?\+\s*|\+\s*.*?[\"\']|f\"[^\"]*\{|\w+\s*\+)', "critical"),
                (r'(?:os\.popen|subprocess\.(?:call|run|Popen))\s*\(\s*[\"\'].*?\+\s*', "high"),
                (r'subprocess\.(?:call|run|Popen)\s*\(\s*\w+\s*\+', "high"),
                (r'Runtime\.getRuntime\(\)\.exec\s*\(\s*\w+\s*\+', "high"),
            ]
            for pattern, sev in cmd_patterns:
                if re.search(pattern, body, re.IGNORECASE):
                    findings.append({
                        "category": "command_injection",
                        "severity": sev,
                        "file_path": file_path,
                        "line": line,
                        "symbol": name,
                        "language": node_lang,
                        "message": "Potential command injection: user input in shell command",
                        "remediation": "Use subprocess.run() with a list of arguments (not a shell string) or shlex.quote() to escape input",
                    })
                    break

            # -- Deserialization -----------------------------------------------
            deser_patterns = [
                (r'pickle\.(?:load|loads)', "high"),
                (r'yaml\.load\s*\(', "medium"),
                (r'ObjectInputStream.*\.readObject', "high"),
            ]
            for pattern, sev in deser_patterns:
                if re.search(pattern, body, re.IGNORECASE):
                    findings.append({
                        "category": "deserialization",
                        "severity": sev,
                        "file_path": file_path,
                        "line": line,
                        "symbol": name,
                        "language": node_lang,
                        "message": "Unsafe deserialization detected",
                        "remediation": "Use safe deserialization alternatives. For pickle, use JSON instead. For YAML, use yaml.safe_load().",
                    })
                    break

        # Apply severity filter
        if severity_filter:
            min_level = severity_order.get(severity_filter, 0)
            findings = [f for f in findings if severity_order.get(f["severity"], 5) <= min_level]

        # Build summary
        summary: dict[str, int] = {}
        for f in findings:
            cat = f["category"]
            summary[cat] = summary.get(cat, 0) + 1

        output = {
            "total_findings": len(findings),
            "findings": findings,
            "summary": summary,
            "scanned_language": language or "all",
        }
    except Exception as e:
        output = {"error": str(e), "total_findings": 0, "findings": [], "summary": {}}

    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _safe_refactor_suggest(store, target_nodes, symbol_name: str) -> dict:
    """Generate smart refactoring suggestions (P46a)."""
    suggestions: list[dict] = []

    # Get full node details
    nodes = []
    for match in target_nodes[:5]:
        node = store.get_node_by_id(_get(match, "node_id"))
        if node:
            nodes.append(node)

    if not nodes:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "symbol": symbol_name,
                    "suggestions": [],
                    "message": "Symbol found but no detailed data available",
                }, ensure_ascii=False),
            }],
        }

    for node in nodes:
        kind = _get(node, "kind")
        if kind not in ("method", "function"):
            continue

        start_line = _get(node, "start_line", 0)
        end_line = _get(node, "end_line", 0)
        lines = end_line - start_line
        nid = _get(node, "id")
        name = _get(node, "name", "")

        # 1. Method extraction suggestion: long methods
        if lines > 50:
            suggestions.append({
                "type": "extract_method",
                "confidence": "high",
                "reason": f"Method '{name}' spans {lines} lines — exceeds 50-line threshold",
                "symbol": name,
                "file": _get(node, "file_path"),
                "lines": lines,
                "action": "Break into smaller sub-methods, each handling a single responsibility",
            })

        # 2. Interface extraction: find other classes with same method signatures
        sig = _get(node, "signature", "")
        if sig:
            # Search for other nodes with same name but different qualified_name
            similar = store.fts_search(name, limit=30)
            same_sig_others = []
            for m in similar:
                mnid = _get(m, "node_id")
                if mnid == nid:
                    continue
                other = store.get_node_by_id(mnid)
                if not other:
                    continue
                other_sig = _get(other, "signature", "")
                other_qname = _get(other, "qualified_name", "")
                if other_sig == sig and other_qname != _get(node, "qualified_name", ""):
                    same_sig_others.append({
                        "name": _get(other, "name"),
                        "qualified_name": other_qname,
                        "file": _get(other, "file_path"),
                    })

            if same_sig_others:
                suggestions.append({
                    "type": "extract_interface",
                    "confidence": "medium",
                    "reason": f"Method '{name}' has identical signature in {len(same_sig_others)} other class(es)",
                    "symbol": name,
                    "signature": sig,
                    "implementations": [
                        {"qualified_name": _get(node, "qualified_name")},
                        *same_sig_others,
                    ],
                    "action": "Extract a shared interface/base class with this method signature",
                })

        # 3. Move method: check coupling
        try:
            outgoing = store.get_outgoing_edges(nid)
        except Exception:
            outgoing = []
        calls_by_file: dict[str, int] = {}
        for e in outgoing:
            if _get(e, "kind") != "calls":
                continue
            target_node = store.get_node_by_id(_get(e, "target"))
            if target_node:
                target_fp = _get(target_node, "file_path", "")
                if target_fp and target_fp != _get(node, "file_path", ""):
                    calls_by_file[target_fp] = calls_by_file.get(target_fp, 0) + 1

        if calls_by_file:
            most_coupled_file = max(calls_by_file, key=calls_by_file.get)
            most_coupled_count = calls_by_file[most_coupled_file]
            total_outgoing = sum(calls_by_file.values())
            if total_outgoing >= 2 and most_coupled_count > total_outgoing * 0.5:
                suggestions.append({
                    "type": "move_method",
                    "confidence": "medium" if most_coupled_count > total_outgoing * 0.7 else "low",
                    "reason": (
                        f"Method '{name}' has {most_coupled_count}/{total_outgoing} "
                        f"calls to '{most_coupled_file}' — high cross-file coupling"
                    ),
                    "symbol": name,
                    "current_file": _get(node, "file_path"),
                    "suggested_file": most_coupled_file,
                    "coupling_ratio": round(most_coupled_count / total_outgoing, 2),
                    "action": f"Consider moving this method to '{most_coupled_file}' to reduce coupling",
                })

    # Deduplicate interface suggestions (same signature from multiple nodes)
    seen_sigs = set()
    deduped = []
    for s in suggestions:
        if s["type"] == "extract_interface":
            sig_key = s.get("signature", "")
            if sig_key in seen_sigs:
                continue
            seen_sigs.add(sig_key)
        deduped.append(s)
    suggestions = deduped

    output = {
        "symbol": symbol_name,
        "change_type": "suggest",
        "suggestions": suggestions,
        "total_suggestions": len(suggestions),
        "message": (
            f"Found {len(suggestions)} refactoring suggestion(s)"
            if suggestions else "No refactoring suggestions for this symbol"
        ),
    }
    return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}


def _compute_quality_gate(store, node_ids: set, file_paths: list) -> dict:
    """Compute quality gate checks for reviewed changes (P46c)."""
    gates: list[dict] = []

    # Collect node details
    nodes = []
    for nid in node_ids:
        node = store.get_node_by_id(nid)
        if node:
            nodes.append(node)

    # === Complexity Gate ===
    complex_funcs = []
    for node in nodes:
        start = _get(node, "start_line", 0)
        end = _get(node, "end_line", 0)
        if end - start > 50:
            complex_funcs.append({
                "name": _get(node, "name"),
                "file": _get(node, "file_path"),
                "lines": end - start,
            })

    if complex_funcs:
        gates.append({
            "name": "complexity",
            "status": "review",
            "message": f"{len(complex_funcs)} function(s) exceed 50-line threshold",
            "details": complex_funcs,
            "remediation": "Consider extracting sub-functions or breaking into smaller units",
        })
    else:
        gates.append({
            "name": "complexity",
            "status": "pass",
            "message": "No functions exceed complexity threshold",
        })

    # === Test Coverage Gate ===
    untested_files = []
    tested_files = []
    all_code_files = set()
    for node in nodes:
        all_code_files.add(_get(node, "file_path", ""))
    for fp in file_paths:
        all_code_files.add(fp)

    for fp in all_code_files:
        if not fp:
            continue
        # Check if a test file exists (simple heuristic)
        base = fp.replace("\\", "/")
        test_patterns = [
            base.replace("src/", "tests/").replace(".py", "_test.py"),
            base.replace("src/", "tests/").replace(".py", "/test_"),
            base.replace("src/", "tests/test_"),
            "tests/" + base.split("/")[-1],
        ]
        # Check if any test file exists in the store
        has_test = False
        for tp in test_patterns:
            try:
                test_nodes = list(store.iter_nodes_by_file(tp))
                if test_nodes:
                    has_test = True
                    tested_files.append(fp)
                    break
            except Exception:
                pass
        if not has_test and fp:
            untested_files.append(fp)

    if untested_files:
        gates.append({
            "name": "test_coverage",
            "status": "review",
            "message": f"{len(untested_files)} file(s) have no matching test files",
            "details": untested_files,
            "remediation": "Add test files for the modified code before merging",
        })
    else:
        gates.append({
            "name": "test_coverage",
            "status": "pass",
            "message": "All modified files have corresponding tests",
        })

    # === Dependency Direction Gate (circular dependency check) ===
    dep_graph: dict[str, set] = {}
    for node in nodes:
        nid = _get(node, "id")
        fp = _get(node, "file_path", "")
        if fp not in dep_graph:
            dep_graph[fp] = set()
        # Get outgoing calls
        try:
            outgoing = store.get_outgoing_edges(nid)
        except Exception:
            outgoing = []
        for e in outgoing:
            if _get(e, "kind") == "calls":
                target_node = store.get_node_by_id(_get(e, "target"))
                if target_node:
                    target_fp = _get(target_node, "file_path", "")
                    if target_fp and target_fp != fp:
                        dep_graph[fp].add(target_fp)

    # Detect cycles
    cycles = []
    files_list = list(dep_graph.keys())
    for i, f1 in enumerate(files_list):
        for f2 in files_list[i + 1:]:
            if f2 in dep_graph.get(f1, set()) and f1 in dep_graph.get(f2, set()):
                cycles.append({"file_a": f1, "file_b": f2})

    if cycles:
        gates.append({
            "name": "dependency_direction",
            "status": "fail",
            "message": f"{len(cycles)} circular dependency(s) detected",
            "details": cycles,
            "remediation": "Break circular dependencies by extracting shared interfaces or introducing a third module",
        })
    else:
        gates.append({
            "name": "dependency_direction",
            "status": "pass",
            "message": "No circular dependencies detected",
        })

    # === Overall Score ===
    statuses = [g["status"] for g in gates]
    if "fail" in statuses:
        overall = "fail"
    elif "review" in statuses:
        overall = "review"
    else:
        overall = "pass"

    return {
        "overall": overall,
        "gates": gates,
        "checked_at": None,  # Would be timestamp in production
    }
    """Return the higher semver bump level."""
    order = {"patch": 0, "minor": 1, "major": 2}
    return b if order.get(b, 0) > order.get(a, 0) else a
