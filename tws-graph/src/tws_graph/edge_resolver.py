"""Post-processing edge resolution.

After all files are indexed, this module re-resolves call edges whose targets
are not yet in the DB — catching cross-file type-based references that
single-file AST extraction cannot resolve.

Uses the `target_text` field (unhashed qualified name) stored on each edge
to search for matching nodes across all files.

Algorithm:
  1. Find all call edges with dangling targets (target not in nodes)
  2. Parse target_text to extract the last 1-3 segments (ClassName::methodName)
  3. Build a suffix index from all callable nodes (method/function/class/interface/object/property)
  4. For each dangling edge:
     - Look up by 3-segment suffix (Container::ClassName::methodName)
     - If no match, try 2-segment suffix (ClassName::methodName)
     - If no match, try 1-segment suffix (methodName) — most ambiguous
     - For 1-segment with multiple matches, prefer same-file candidate
     - Exactly 1 match → fix edge target, mark provenance 'resolved'
     - 0 matches → mark provenance 'unresolved'
     - 2+ matches → mark provenance 'ambiguous'
"""

from __future__ import annotations
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Known external call target prefixes
# ---------------------------------------------------------------------------

# Python built-in types whose method calls are always external
_PYTHON_BUILTIN_TYPES: frozenset[str] = frozenset({
    'str', 'list', 'dict', 'int', 'float', 'bool', 'tuple', 'set',
    'frozenset', 'bytes', 'bytearray', 'object', 'type', 'range',
    'slice', 'complex', 'memoryview', 'property', 'classmethod',
    'staticmethod', 'super', 'enumerate', 'zip', 'filter', 'map',
    'reversed', 'sorted', 'iter', 'next', 'any', 'all', 'sum',
    'min', 'max', 'abs', 'round', 'pow', 'len', 'input', 'print',
    'open', 'format', 'chr', 'ord', 'hex', 'oct', 'bin', 'repr',
    'ascii', 'hash', 'id', 'isinstance', 'issubclass', 'callable',
    'getattr', 'setattr', 'delattr', 'hasattr', 'dir', 'vars',
    'globals', 'locals', 'compile', 'eval', 'exec',
})

# Tree-sitter / common library class prefixes whose methods are external
_KNOWN_EXTERNAL_PREFIXES: frozenset[str] = frozenset({
    'node', 'Parser', 'Query', 'Language', 'Tree',
    'TreeCursor', 'Node', 'Point', 'Range', 'response',
    'Request', 'Session', 'Client',
})

# Python stdlib modules — first segment that indicates external
_STDLIB_MODULES: frozenset[str] = frozenset({
    'os', 'sys', 're', 'json', 'math', 'random', 'datetime',
    'collections', 'itertools', 'functools', 'pathlib', 'io',
    'tempfile', 'shutil', 'glob', 'fnmatch', 'linecache',
    'pickle', 'shelve', 'marshal', 'sysconfig',
    'time', 'argparse', 'getopt', 'logging', 'getpass',
    'curses', 'platform', 'errno', 'ctypes', 'struct',
    'threading', 'multiprocessing', 'subprocess', 'signal',
    'email', 'mailbox', 'mimetypes', 'base64', 'binascii',
    'binhex', 'quopri', 'uu', 'csv', 'configparser',
    'tomllib', 'netrc', 'plistlib', 'hashlib', 'hmac',
    'secrets', 'statistics', 'string', 'textwrap', 'unicodedata',
    'difflib', 'pprint', 'reprlib', 'enum', 'graphlib',
    'fractions', 'decimal', 'random', 'statistics',
    'sqlite3', 'gzip', 'bz2', 'lzma', 'zipfile', 'tarfile',
    'typing', 'dataclasses', 'abc', 'atexit', 'contextlib',
    'contextvars', 'copy', 'copyreg', 'gc', 'inspect',
    'traceback', 'warnings', 'weakref', 'dataclasses',
    'pytest', 'unittest', 'doctest',
})


@dataclass
class ResolveResult:
    resolved: int = 0        # edges whose target was fixed
    unresolved: int = 0      # edges confirmed unresolvable (external libs)
    ambiguous: int = 0       # edges with multiple candidate targets
    total_checked: int = 0


def resolve_edges(queries) -> ResolveResult:
    """Main entry point: resolve dangling call edges using target_text.

    P29 (v5.3.0): Import-aware resolution — follows import edges to expand
    aliases, resolve ``from X import Y`` patterns, and use import context
    for disambiguation.

    Args:
        queries: QueryBuilder instance connected to the index DB.

    Returns:
        ResolveResult with counts.
    """
    result = ResolveResult()

    # ── 1. Find all dangling call edges ─────────────────────────
    dangling = queries.get_dangling_call_edges()
    result.total_checked = len(dangling)
    if not dangling:
        return result

    # ── 2. Build import map ─────────────────────────────────────
    # P29a/c: import_map[source_file] → {name: {module, full_name}}
    #   "pd" → {module: "pandas", full_name: "pandas"}
    #   "Baz" → {module: "foo.bar", full_name: "foo.bar.Baz"}
    import_map = _build_import_map(queries)

    # ── 3. Build suffix index from all callable nodes ────
    suffix_index: dict[str, list[str]] = {}
    suffix_index_lower: dict[str, list[str]] = {}
    node_file: dict[str, str] = {}
    node_qname: dict[str, str] = {}  # node_id → qualified_name (for import matching)
    all_nodes = queries.get_all_callable_nodes()
    for node in all_nodes:
        qname = node["qualified_name"]
        nid = node["id"]
        node_file[nid] = node["file_path"]
        node_qname[nid] = qname
        parts = qname.rsplit("::", 3)
        for level in range(1, min(len(parts), 3) + 1):
            suffix = "::".join(parts[-level:])
            suffix_index.setdefault(suffix, []).append(nid)
            suffix_index_lower.setdefault(suffix.lower(), []).append(nid)

    # ── 4. Resolve each dangling edge ───────────────────────────
    for edge in dangling:
        target_text = edge["target_text"]
        if not target_text:
            continue

        source_file = (edge["source_loc"] or "").rsplit(":", 1)[0]

        # P29a/c: try resolving with import alias expansion
        expanded_texts = _expand_target_via_imports(
            target_text, source_file, import_map
        )

        matched_id = None
        matched_count = 0

        # Try each expanded form (original first, then alias-expanded)
        for try_text in expanded_texts:
            matched_id, matched_count = _try_match(
                try_text, suffix_index, suffix_index_lower,
                node_file, source_file
            )
            if matched_id:
                break

        edge_rowid = edge["edge_rowid"]
        if matched_id:
            queries.update_edge_target(edge_rowid, matched_id, "resolved")
            result.resolved += 1
        elif matched_count > 1:
            queries.mark_edge_provenance(edge_rowid, "ambiguous")
            result.ambiguous += 1
        else:
            queries.mark_edge_provenance(edge_rowid, "unresolved")
            result.unresolved += 1

    return result


def _build_import_map(queries) -> dict[str, dict[str, dict[str, str]]]:
    """Build per-file import map from import edges.

    Returns:
        ``{source_file: {local_name: {module, full_name}}}``

    ``local_name`` is what the caller uses — either the alias or the last
    segment of the imported name.  ``module`` is the package prefix and
    ``full_name`` is the complete dotted path.
    """
    import_rows = queries._exec("""
        SELECT e.target_text, ns.file_path AS source_file
        FROM edges e
        JOIN nodes ns ON e.source = ns.id
        WHERE e.kind = 'imports'
          AND e.target_text IS NOT NULL
    """).fetchall()

    imap: dict[str, dict[str, dict[str, str]]] = {}
    for row in import_rows:
        sf = row["source_file"]
        if not sf:
            continue
        target_text = row["target_text"]

        # Parse "module.Name as alias" or "module.Name" or "*"
        alias = None
        rest = target_text

        if " as " in target_text:
            rest, alias = target_text.rsplit(" as ", 1)

        if alias:
            local_name = alias
            full_name = rest
        else:
            # No explicit alias — the local name is the last segment
            local_name = rest.rsplit(".", 1)[-1] if "." in rest else rest
            full_name = rest

        module = rest.rsplit(".", 1)[0] if "." in rest else rest

        imap.setdefault(sf, {})[local_name] = {
            "module": module,
            "full_name": full_name,
        }

    return imap


def _expand_target_via_imports(
    target_text: str,
    source_file: str,
    import_map: dict[str, dict[str, dict[str, str]]],
) -> list[str]:
    """Expand call target_text through import aliases.

    Returns a list of candidate target_text strings, with the original
    first (no expansion) and alias-expanded forms appended.
    """
    results = [target_text]  # always try original first

    if not source_file or source_file not in import_map:
        return results

    file_imports = import_map[source_file]
    if not file_imports:
        return results

    # target_text is "A::B::method" or "func" or "A.B"
    # Split on first "::" to get the receiver prefix
    parts = target_text.split("::", 1)
    first_segment = parts[0]

    # Check if first_segment matches an imported name or alias
    imp_info = file_imports.get(first_segment)
    if imp_info:
        # Expand: "pd::DataFrame" → "pandas::DataFrame"
        if imp_info["full_name"] != first_segment:
            expanded = imp_info["full_name"] + ("::" + parts[1] if len(parts) > 1 else "")
            results.append(expanded)
        # Also try using just the last segment of the import as prefix
        # "from foo.bar import Baz" → target_text "Baz::method"
        # Try "foo.bar.Baz::method" (fully qualified)
        if "." in imp_info["full_name"]:
            expanded_full = imp_info["full_name"] + ("::" + parts[1] if len(parts) > 1 else "")
            results.append(expanded_full)

    # P29b: if source_file has wildcard imports, treat first_segment
    # as potentially from the wildcard-imported module
    for local_name, info in file_imports.items():
        if info["full_name"].endswith(".*") or local_name == "*":
            # Wildcard — first_segment might be from this module
            if first_segment not in file_imports:
                # The call name could be an export of the wildcard module
                expanded = info["module"] + "." + first_segment
                if len(parts) > 1:
                    expanded += "::" + parts[1]
                results.append(expanded)

    return results


def _try_match(
    target_text: str,
    suffix_index: dict[str, list[str]],
    suffix_index_lower: dict[str, list[str]],
    node_file: dict[str, str],
    source_file: str,
) -> tuple[str | None, int]:
    """Try to match a single target_text against the suffix index.

    Returns (matched_id, matched_count).  matched_id is None if no
    unique match was found; matched_count is the number of candidates
    when not unique.
    """
    parts = target_text.rsplit("::", 3)
    matched_id = None
    matched_count = 0

    # Case-sensitive: most specific to least
    for level in range(min(len(parts), 3), 0, -1):
        suffix = "::".join(parts[-level:])
        candidates = suffix_index.get(suffix, [])
        if len(candidates) == 1:
            return (candidates[0], 1)
        elif len(candidates) > 1:
            if level == 1 and source_file:
                same_file = [cid for cid in candidates
                             if node_file.get(cid) == source_file]
                if len(same_file) == 1:
                    return (same_file[0], len(candidates))
            matched_count = len(candidates)

    # Case-insensitive fallback
    if not matched_id and matched_count == 0:
        for level in range(min(len(parts), 2), 0, -1):
            suffix = "::".join(parts[-level:])
            ci_candidates = suffix_index_lower.get(suffix.lower(), [])
            if len(ci_candidates) == 1:
                return (ci_candidates[0], 1)
            elif len(ci_candidates) > 1 and matched_id is None:
                if source_file:
                    same_file_ci = [cid for cid in ci_candidates
                                    if node_file.get(cid) == source_file]
                    if len(same_file_ci) == 1:
                        return (same_file_ci[0], len(ci_candidates))
                matched_count = len(ci_candidates)

    return (matched_id, matched_count)


# ---------------------------------------------------------------------------
# External target classification (used by _populate_unresolved_refs)
# ---------------------------------------------------------------------------

def resolve_structural_edges(queries) -> ResolveResult:
    """Resolve dangling extends/implements edges using target_text.

    Unlike call edges, structural edges use simple qualified names:
    ``FilePath::ClassName`` — just look up the class name across all nodes.

    Args:
        queries: QueryBuilder instance connected to the index DB.

    Returns:
        ResolveResult with counts.
    """
    result = ResolveResult()

    dangling = queries.get_dangling_structural_edges()
    result.total_checked = len(dangling)
    if not dangling:
        return result

    # Build simple name → [node_id] index for class/interface nodes
    name_index: dict[str, list[str]] = {}
    node_file: dict[str, str] = {}
    all_nodes = queries.get_all_callable_nodes()
    for node in all_nodes:
        if node["kind"] in ("class", "interface"):
            simple_name = node["qualified_name"].rsplit("::", 1)[-1]
            name_index.setdefault(simple_name, []).append(node["id"])
            node_file[node["id"]] = node["file_path"]

    for edge in dangling:
        target_text = edge["target_text"]
        if not target_text:
            continue

        # target_text looks like "file.py::ClassName" or just "ClassName"
        simple_name = target_text.rsplit("::", 1)[-1]
        source_file = (edge["source_loc"] or "").rsplit(":", 1)[0]

        candidates = name_index.get(simple_name, [])
        edge_rowid = edge["edge_rowid"]

        if len(candidates) == 1:
            queries.update_edge_target(edge_rowid, candidates[0], "resolved")
            result.resolved += 1
        elif len(candidates) > 1:
            # Try same-file disambiguation
            same_file = [cid for cid in candidates
                         if node_file.get(cid) == source_file]
            if len(same_file) == 1:
                queries.update_edge_target(edge_rowid, same_file[0], "resolved")
                result.resolved += 1
            else:
                queries.mark_edge_provenance(edge_rowid, "ambiguous")
                result.ambiguous += 1
        else:
            queries.mark_edge_provenance(edge_rowid, "unresolved")
            result.unresolved += 1

    return result


def is_call_target_external(
    target_text: str,
    project_files: frozenset[str] | set[str] | None = None,
) -> bool:
    """Determine whether an unresolved call target_text is likely external.

    An **external** target is a method/function from the Python standard
    library, a built-in type, or a third-party package --- not a project
    symbol whose index entry is simply missing.

    Heuristics (evaluated in order, first match wins):
      1. Empty target_text --- internal (cannot classify)
      2. String literal receiver (" "::join) --- external
      3. Number literal receiver --- external
      4. Python built-in type prefix (str::lower, list::append) --- external
      5. Known tree-sitter / common library class prefix --- external
      6. Python stdlib module prefix (os::path::join) --- external
      7. First segment looks like a project file --- check project_files
      8. Default --- internal (conservative --- may be an index gap)
    """
    import os as _os

    if not target_text:
        return False

    parts = target_text.split("::")
    first = parts[0]

    # 1. String literal receiver
    if first.startswith('"') or first.startswith("'"):
        return True

    # 2. Number literal receiver
    if first.lstrip("-").isdigit():
        return True

    # 3. Python built-in types
    if first in _PYTHON_BUILTIN_TYPES:
        return True

    # 4. Known external library prefixes
    if first in _KNOWN_EXTERNAL_PREFIXES:
        return True

    # 5. Python stdlib modules
    if first in _STDLIB_MODULES:
        return True

    # 6. First segment looks like a file path --- check project_files
    if project_files is not None:
        has_file_ext = "." in first and any(
            first.endswith(ext)
            for ext in (
                ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go",
                ".rs", ".kt", ".swift", ".c", ".cpp", ".cc", ".cxx",
                ".cs", ".rb", ".php", ".scala", ".ex", ".exs", ".hs",
                ".clj", ".cljs", ".cljc", ".edn",
            )
        )
        if has_file_ext:
            if first not in project_files:
                return True
            # First segment IS a project file — but the actual call target
            # (last segment) may still be a built-in.  edge_resolver already
            # tried to find it in the project and failed, so if the last
            # segment matches a built-in type/function it is external.
            last = parts[-1]
            if last in _PYTHON_BUILTIN_TYPES:
                return True
            return False

        if first.startswith(".") and _os.path.sep not in first:
            return False

    # 7. Default: unresolved but could be an internal index gap
    return False


def resolve_overrides(queries) -> int:
    """Detect method overrides from extends relationships and create overrides edges.

    Scans all extends edges to find child→parent class relationships,
    then checks for same-named methods in both classes. For each match,
    creates an ``overrides`` edge from the child method to the parent method.

    Multi-level inheritance is handled transitively: if B extends A and
    C extends B, C's methods are checked against both B's and A's methods.

    Args:
        queries: QueryBuilder instance connected to the index DB.

    Returns:
        Number of overrides edges created.
    """
    # 1. Build child_class → parent_class map from extends edges
    extends_rows = queries._exec(
        "SELECT source, target FROM edges WHERE kind = 'extends'"
    ).fetchall()

    if not extends_rows:
        return 0

    child_parent: dict[str, str] = {}
    for row in extends_rows:
        child_parent[row["source"]] = row["target"]

    # 2. Collect all method nodes with their simple name and parent class
    method_rows = queries._exec(
        "SELECT id, name, qualified_name, kind FROM nodes WHERE kind = 'method'"
    ).fetchall()

    # method name → parent_class_id → method_id
    # parent_class_id is extracted from qualified_name: "file.py::ClassName.methodName"
    method_by_class: dict[str, dict[str, list[str]]] = {}
    for row in method_rows:
        qn = row["qualified_name"]
        # Extract: "file.py::ClassName.methodName" → parent_class_qn = "file.py::ClassName"
        parts = qn.rsplit("::", 2)
        if len(parts) >= 2:
            # parts: ["file.py", "ClassName", "methodName"] or ["file.py::ClassName", "methodName"]
            class_qn = "::".join(parts[:-1])
            method_name = parts[-1]
        else:
            continue

        method_by_class.setdefault(class_qn, {}).setdefault(method_name, []).append(row["id"])

    # 3. Build transitive ancestor map from child→parent relations
    def get_ancestors(child_id: str, visited: set | None = None) -> set[str]:
        if visited is None:
            visited = set()
        if child_id in visited:
            return set()
        visited.add(child_id)
        ancestors = set()
        parent_id = child_parent.get(child_id)
        if parent_id:
            ancestors.add(parent_id)
            ancestors |= get_ancestors(parent_id, visited)
        return ancestors

    # Also need: class_id → qualified_name
    class_rows = queries._exec(
        "SELECT id, qualified_name FROM nodes WHERE kind = 'class'"
    ).fetchall()
    class_id_to_qn = {row["id"]: row["qualified_name"] for row in class_rows}

    # 4. For each child class, check its methods against parent class methods
    edges_to_insert: list[dict] = []
    seen_edge_keys: set[tuple[str, str]] = set()

    for child_id, parent_id in child_parent.items():
        ancestors = get_ancestors(child_id)

        child_qn = class_id_to_qn.get(child_id)
        if not child_qn:
            continue

        child_methods = method_by_class.get(child_qn, {})
        if not child_methods:
            continue

        for ancestor_id in ancestors:
            ancestor_qn = class_id_to_qn.get(ancestor_id)
            if not ancestor_qn:
                continue

            parent_methods = method_by_class.get(ancestor_qn, {})
            if not parent_methods:
                continue

            # Find same-named methods
            for method_name, child_method_ids in child_methods.items():
                if method_name in parent_methods:
                    for child_mid in child_method_ids:
                        for parent_mid in parent_methods[method_name]:
                            edge_key = (child_mid, parent_mid)
                            if edge_key not in seen_edge_keys:
                                seen_edge_keys.add(edge_key)
                                edges_to_insert.append({
                                    "source": child_mid,
                                    "target": parent_mid,
                                    "kind": "overrides",
                                    "source_loc": "",
                                    "target_text": "",
                                    "provenance": "heuristic",
                                    "properties": "{}",
                                })

    if edges_to_insert:
        queries.insert_edges(edges_to_insert)

    return len(edges_to_insert)


# Known constructor method names across languages
_CONSTRUCTOR_NAMES = frozenset({"__init__", "__new__", "constructor", "<init>"})


def resolve_instantiates(queries) -> int:
    """Detect class instantiations from calls edges and create instantiates edges.

    Scans all resolved ``calls`` edges where the target is a constructor method
    (``__init__`` for Python, ``constructor`` for TypeScript, ``<init>`` for Java/Kotlin),
    then looks up the parent class and creates an ``instantiates`` edge from the
    caller to the class node.

    Args:
        queries: QueryBuilder instance connected to the index DB.

    Returns:
        Number of instantiates edges created.
    """
    # 1. Find calls edges targeting constructor methods
    rows = queries._exec("""
        SELECT e.source AS caller_id, e.target AS ctor_id, n.qualified_name
        FROM edges e
        JOIN nodes n ON e.target = n.id
        WHERE e.kind = 'calls'
          AND n.kind = 'method'
          AND n.name IN ('__init__', '__new__', 'constructor', '<init>')
    """).fetchall()

    if not rows:
        return 0

    # 2. Build method qualified_name → class qualified_name mapping
    # "file.py::ClassName::__init__" → class_qn = "file.py::ClassName"
    ctor_to_class: dict[str, str] = {}
    for row in rows:
        qn = row["qualified_name"]
        parts = qn.rsplit("::", 1)
        if len(parts) == 2:
            ctor_to_class[row["ctor_id"]] = parts[0]

    if not ctor_to_class:
        return 0

    # 3. Build class qualified_name → class node_id mapping
    class_rows = queries._exec(
        "SELECT id, qualified_name FROM nodes WHERE kind = 'class'"
    ).fetchall()
    qn_to_class_id: dict[str, str] = {r["qualified_name"]: r["id"] for r in class_rows}

    # 4. Create instantiates edges (deduplicate)
    edges_to_insert: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for row in rows:
        caller_id = row["caller_id"]
        ctor_id = row["ctor_id"]
        class_qn = ctor_to_class.get(ctor_id)
        if not class_qn:
            continue

        class_id = qn_to_class_id.get(class_qn)
        if not class_id:
            continue

        edge_key = (caller_id, class_id)
        if edge_key not in seen:
            seen.add(edge_key)
            edges_to_insert.append({
                "source": caller_id,
                "target": class_id,
                "kind": "instantiates",
                "source_loc": "",
                "target_text": "",
                "provenance": "heuristic",
                "properties": "{}",
            })

    if edges_to_insert:
        queries.insert_edges(edges_to_insert)

    return len(edges_to_insert)
