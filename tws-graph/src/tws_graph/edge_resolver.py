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


@dataclass
class ResolveResult:
    resolved: int = 0        # edges whose target was fixed
    unresolved: int = 0      # edges confirmed unresolvable (external libs)
    ambiguous: int = 0       # edges with multiple candidate targets
    total_checked: int = 0


def resolve_edges(queries) -> ResolveResult:
    """Main entry point: resolve dangling call edges using target_text.

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

    # ── 2. Build suffix index from all callable nodes ────
    # Index maps: suffix → [node_id, ...] (both exact and case-insensitive)
    suffix_index: dict[str, list[str]] = {}
    suffix_index_lower: dict[str, list[str]] = {}  # lowercase → [node_id]
    node_file: dict[str, str] = {}  # node_id → file_path (for same-file disambiguation)
    all_nodes = queries.get_all_callable_nodes()
    for node in all_nodes:
        qname = node["qualified_name"]
        node_file[node["id"]] = node["file_path"]
        parts = qname.rsplit("::", 3)
        # Build up to 3 levels of suffix
        for level in range(1, min(len(parts), 3) + 1):
            suffix = "::".join(parts[-level:])
            suffix_index.setdefault(suffix, []).append(node["id"])
            suffix_index_lower.setdefault(suffix.lower(), []).append(node["id"])

    # ── 3. Resolve each dangling edge ───────────────────────────
    for edge in dangling:
        target_text = edge["target_text"]
        if not target_text:
            continue

        # Parse target_text: "file.kt::Container::ClassName::method" or similar
        parts = target_text.rsplit("::", 3)
        # parts look like: ["file.kt", "ClassName", "method"] or ["file.kt::ClassName", "method"]

        matched_id = None
        match_level = None
        matched_count = 0

        # Extract source file from source_loc ("file.kt:line")
        source_file = (edge["source_loc"] or "").rsplit(":", 1)[0]

        # Try from most specific (3 segments) to least (1 segment)
        for level in range(min(len(parts), 3), 0, -1):
            suffix = "::".join(parts[-level:])
            candidates = suffix_index.get(suffix, [])
            if len(candidates) == 1:
                matched_id = candidates[0]
                match_level = level
                matched_count = 1
                break
            elif len(candidates) > 1:
                # For 1-segment: try same-file disambiguation
                if level == 1 and source_file:
                    same_file = [cid for cid in candidates
                                 if node_file.get(cid) == source_file]
                    if len(same_file) == 1:
                        matched_id = same_file[0]
                        match_level = level
                        matched_count = len(candidates)
                        break
                # Record multi-match but keep looking for a more specific match
                if matched_id is None:
                    matched_count = len(candidates)

        # If no exact match, try case-insensitive fallback
        # (handles camelCase-vs-PascalCase: documentOpener vs DocumentOpener)
        if not matched_id and matched_count == 0:
            for level in range(min(len(parts), 2), 0, -1):
                suffix = "::".join(parts[-level:])
                ci_candidates = suffix_index_lower.get(suffix.lower(), [])
                if len(ci_candidates) == 1:
                    matched_id = ci_candidates[0]
                    matched_count = 1
                    break
                elif len(ci_candidates) > 1 and matched_id is None:
                    # Try same-file within CI matches
                    if source_file:
                        same_file_ci = [cid for cid in ci_candidates
                                        if node_file.get(cid) == source_file]
                        if len(same_file_ci) == 1:
                            matched_id = same_file_ci[0]
                            matched_count = len(ci_candidates)
                            break
                    matched_count = len(ci_candidates)

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
