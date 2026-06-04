"""Snapshot and diff — file-copy based, not in-DB tables.

Design decision: copy the entire .db file for snapshots.
- Simpler than maintaining separate snapshot tables
- Each snapshot is self-contained (openable by any SQLite tool)
- TWS only needs 2 snapshots (before/after), not multi-version history
"""

import shutil
import sqlite3
import os
from dataclasses import dataclass, field


@dataclass
class DiffReport:
    added_symbols: list[dict] = field(default_factory=list)
    removed_symbols: list[dict] = field(default_factory=list)
    signature_changed: list[dict] = field(default_factory=list)
    added_edges: list[dict] = field(default_factory=list)
    removed_edges: list[dict] = field(default_factory=list)
    affected_files: set[str] = field(default_factory=set)

    @property
    def has_changes(self) -> bool:
        return any([
            self.added_symbols, self.removed_symbols, self.signature_changed,
            self.added_edges, self.removed_edges,
        ])


def save_snapshot(db_path: str, snapshot_name: str, snapshots_dir: str) -> str:
    """Copy the current index.db to a named snapshot file.

    Returns the path to the created snapshot.
    """
    os.makedirs(snapshots_dir, exist_ok=True)
    dest = os.path.join(snapshots_dir, f"index-{snapshot_name}.db")
    shutil.copy(db_path, dest)
    return dest


def list_snapshots(snapshots_dir: str) -> list[str]:
    """List available snapshot names."""
    if not os.path.isdir(snapshots_dir):
        return []
    names = []
    for f in os.listdir(snapshots_dir):
        if f.startswith("index-") and f.endswith(".db"):
            # Extract name: "index-<name>.db" -> "<name>"
            name = f[6:-3]
            names.append(name)
    return sorted(names)


def compare_snapshots(
    before_path: str,
    after_path: str,
) -> DiffReport:
    """Compare two snapshot .db files and produce a DiffReport.

    Matches nodes by qualified_name and compares signatures.
    Edges are matched by (source_qualified, target_qualified, kind).
    """
    if not os.path.exists(before_path):
        raise FileNotFoundError(f"Before snapshot not found: {before_path}")
    if not os.path.exists(after_path):
        raise FileNotFoundError(f"After snapshot not found: {after_path}")

    before = sqlite3.connect(before_path)
    before.row_factory = sqlite3.Row
    after = sqlite3.connect(after_path)
    after.row_factory = sqlite3.Row

    report = DiffReport()

    # --- Compare nodes by qualified_name ---
    before_nodes = {
        r["qualified_name"]: dict(r)
        for r in before.execute("SELECT * FROM nodes").fetchall()
    }
    after_nodes = {
        r["qualified_name"]: dict(r)
        for r in after.execute("SELECT * FROM nodes").fetchall()
    }

    before_names = set(before_nodes.keys())
    after_names = set(after_nodes.keys())

    # Added symbols
    for name in sorted(after_names - before_names):
        n = after_nodes[name]
        report.added_symbols.append({
            "qualified_name": name,
            "kind": n.get("kind"),
            "name": n.get("name"),
            "file_path": n.get("file_path"),
            "start_line": n.get("start_line"),
            "visibility": n.get("visibility"),
            "signature": n.get("signature"),
        })
        report.affected_files.add(n.get("file_path", ""))

    # Removed symbols
    for name in sorted(before_names - after_names):
        n = before_nodes[name]
        report.removed_symbols.append({
            "qualified_name": name,
            "kind": n.get("kind"),
            "name": n.get("name"),
            "file_path": n.get("file_path"),
            "start_line": n.get("start_line"),
        })
        report.affected_files.add(n.get("file_path", ""))

    # Signature changes
    for name in sorted(before_names & after_names):
        before_sig = before_nodes[name].get("signature")
        after_sig = after_nodes[name].get("signature")
        if before_sig != after_sig:
            n = after_nodes[name]
            report.signature_changed.append({
                "qualified_name": name,
                "kind": n.get("kind"),
                "name": n.get("name"),
                "file_path": n.get("file_path"),
                "old_signature": before_sig,
                "new_signature": after_sig,
            })
            report.affected_files.add(n.get("file_path", ""))

    # --- Compare edges ---
    # Build edge keys: (source_qname, target_qname, kind)
    before_node_map = {r["id"]: r["qualified_name"]
                       for r in before.execute("SELECT id, qualified_name FROM nodes").fetchall()}
    after_node_map = {r["id"]: r["qualified_name"]
                      for r in after.execute("SELECT id, qualified_name FROM nodes").fetchall()}

    def _edge_key(edge_row, node_map) -> tuple | None:
        src_qname = node_map.get(edge_row["source"])
        tgt_qname = node_map.get(edge_row["target"])
        if src_qname and tgt_qname:
            return (src_qname, tgt_qname, edge_row["kind"])
        return None

    before_edges = {}
    for e in before.execute("SELECT * FROM edges").fetchall():
        key = _edge_key(e, before_node_map)
        if key:
            before_edges[key] = dict(e)

    after_edges = {}
    for e in after.execute("SELECT * FROM edges").fetchall():
        key = _edge_key(e, after_node_map)
        if key:
            after_edges[key] = dict(e)

    before_edge_keys = set(before_edges.keys())
    after_edge_keys = set(after_edges.keys())

    for key in sorted(after_edge_keys - before_edge_keys):
        src, tgt, kind = key
        report.added_edges.append({"source": src, "target": tgt, "kind": kind})

    for key in sorted(before_edge_keys - after_edge_keys):
        src, tgt, kind = key
        report.removed_edges.append({"source": src, "target": tgt, "kind": kind})

    before.close()
    after.close()
    return report


def format_diff_report(report: DiffReport, brief: bool = False) -> str:
    """Format a DiffReport as human-readable text.

    If brief=True, only outputs a summary line ("changed" / "unchanged").
    """
    if brief:
        return "changed" if report.has_changes else "unchanged"

    lines = []

    if report.added_symbols:
        lines.append(f"新增符号 ({len(report.added_symbols)}):")
        for s in report.added_symbols:
            vis = f" ({s.get('visibility', '')})" if s.get('visibility') else ""
            sig = f"\n    signature: {s['signature']}" if s.get('signature') else ""
            lines.append(f"  + {s['qualified_name']}{vis}{sig}")

    if report.removed_symbols:
        lines.append(f"\n删除符号 ({len(report.removed_symbols)}):")
        for s in report.removed_symbols:
            lines.append(f"  - {s['qualified_name']}")

    if report.signature_changed:
        lines.append(f"\n签名变更 ({len(report.signature_changed)}):")
        for s in report.signature_changed:
            lines.append(f"  ~ {s['qualified_name']}")
            lines.append(f"    old: {s['old_signature']}")
            lines.append(f"    new: {s['new_signature']}")

    if report.added_edges:
        lines.append(f"\n新增调用关系 ({len(report.added_edges)}):")
        for e in report.added_edges[:20]:  # cap at 20
            lines.append(f"  + {e['source']} --[{e['kind']}]--> {e['target']}")
        if len(report.added_edges) > 20:
            lines.append(f"  ... and {len(report.added_edges) - 20} more")

    if report.removed_edges:
        lines.append(f"\n断开的调用关系 ({len(report.removed_edges)}):")
        for e in report.removed_edges[:20]:
            lines.append(f"  - {e['source']} --[{e['kind']}]--> {e['target']}")
        if len(report.removed_edges) > 20:
            lines.append(f"  ... and {len(report.removed_edges) - 20} more")

    if not lines:
        lines.append("(no differences)")

    lines.append(f"\n涉及文件: {len(report.affected_files)}")
    return "\n".join(lines)
