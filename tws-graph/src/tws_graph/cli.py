"""TWS Code Graph CLI — typer-based command-line interface.

Commands:
    index       Build / update the code symbol graph
    calls       Find callers (--inbound) / callees (--outbound) of a symbol
    impact      Calculate impact radius of a symbol
    trace       Find call path between two symbols
    snapshot    Save a named copy of the current index
    diff        Compare two snapshots
    analyze     Run graph analysis algorithms (clone/community/centrality/cycle)
    watch       Watch directory for changes and auto-sync the index
"""

import hashlib
import json
import os
import sys
from typing import Optional

import typer

from . import __version__
from .db.connection import DatabaseConnection
from .db.queries import QueryBuilder
from .indexer.orchestrator import ExtractionOrchestrator
from .indexer.scanner import scan_directory
from .indexer.language_detect import detect_language
from .pipeline.engine import PipelineEngine
from .pipeline.passes import (
    StatFilterPass,
    ParseExtractPass,
    NodeInsertPass,
    EdgeInsertPass,
    DataFlowPass,
    CrossFileResolvePass,
)
from .store import SqliteStore
from .graph.traversal import GraphTraverser
from .graph.algorithms.similarity import CloneDetector
from .graph.algorithms.community import CommunityDetector
from .graph.algorithms.centrality import CentralityComputer
from .graph.algorithms.cycle_detect import CycleDetector
from .watcher.interface import FileChangeEvent
from .watcher.polling_watcher import PollingFileWatcher
from .watcher.debounce import DebounceQueue, DebounceConfig
from .diff import (
    save_snapshot, list_snapshots, compare_snapshots,
    format_diff_report, DiffReport,
)

app = typer.Typer(
    name="tws-graph",
    help="TWS Code Graph — 预建代码符号关系图，agent 查图而非搜索",
)

# LSP sub-command group
lsp_app = typer.Typer(help="LSP (Language Server Protocol) 集成管理")
app.add_typer(lsp_app, name="lsp", help="LSP 集成管理")


@app.callback(invoke_without_command=True)
def _version_callback(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V",
        help="Show version and exit",
        is_eager=True,
    ),
):
    if version:
        print(f"tws-graph {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())
        raise typer.Exit()

# Default paths
DEFAULT_DB = ".tws/codegraph/index.db"
DEFAULT_SNAPSHOTS = ".tws/codegraph"


def _get_db(db_path: Optional[str] = None) -> DatabaseConnection:
    """Open or create the index database."""
    path = db_path or DEFAULT_DB
    if os.path.exists(path):
        return DatabaseConnection.open(path)
    return DatabaseConnection.initialize(path)


def _resolve_node(queries: QueryBuilder, symbol: str):
    """Search for a symbol by name or qualified_name. Returns the node or None."""
    # Try exact name match first
    nodes = queries.get_nodes_by_name(symbol)
    if nodes:
        if len(nodes) == 1:
            return nodes[0]
        # Multiple matches: prefer the one that's a function/method
        funcs = [n for n in nodes if n["kind"] in ("function", "method")]
        if funcs:
            return funcs[0]
        return nodes[0]

    # Try qualified_name partial match
    results = queries.search_nodes(symbol, limit=5)
    if results:
        return results[0]

    return None


def _get_store(db_path: str) -> SqliteStore:
    """Open or create the index database via SqliteStore.

    For new databases, runs the Store migration system to create the full
    schema. For existing databases (potentially created by the old schema),
    ensures the ``properties`` column exists on the ``nodes`` table.
    """
    import sqlite3 as _sqlite3

    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    is_new = not os.path.exists(db_path)

    if is_new:
        # Fresh database: use the full migration system
        from .store.migrations import MigrationRunner

        conn = _sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = _sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        runner = MigrationRunner(conn)
        runner.migrate()
        conn.close()
    else:
        # Existing database: ensure properties column exists
        # (old schema created by DatabaseConnection.initialize() lacks it)
        conn = _sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = _sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            conn.execute("SELECT properties FROM nodes LIMIT 1")
        except _sqlite3.OperationalError:
            conn.execute(
                "ALTER TABLE nodes ADD COLUMN properties TEXT DEFAULT '{}'"
            )
        try:
            conn.execute("SELECT properties FROM edges LIMIT 1")
        except _sqlite3.OperationalError:
            conn.execute(
                "ALTER TABLE edges ADD COLUMN properties TEXT DEFAULT '{}'"
            )
        conn.close()

    return SqliteStore(db_path)


def _build_pipeline_engine(store: SqliteStore) -> PipelineEngine:
    """Build the standard 5-Pass indexing pipeline.

    Passes (in dependency order):
        1. StatFilterPass    — mtime/size filtering
        2. ParseExtractPass  — tree-sitter parse + extract
        3. NodeInsertPass    — write symbol nodes to Store
        4. EdgeInsertPass    — write call/ref edges to Store
        5. CrossFileResolvePass — resolve cross-file dangling edges
    """
    engine = PipelineEngine(store=store)
    engine.register_pass(StatFilterPass())
    engine.register_pass(ParseExtractPass())
    engine.register_pass(NodeInsertPass())
    engine.register_pass(EdgeInsertPass())
    engine.register_pass(DataFlowPass())
    engine.register_pass(CrossFileResolvePass())
    return engine


def _cleanup_deleted_files(store: SqliteStore, current_files: list[str]) -> None:
    """Remove database records for files that no longer exist on disk."""
    existing = {f["path"] for f in store.get_all_files()}
    current = set(current_files)
    removed = existing - current
    for path in removed:
        store.delete_file(path)


def _upsert_file_records(
    store: SqliteStore,
    processed_files: list[str],
    root_dir: str,
) -> None:
    """Upsert file records for files that passed through the pipeline.

    This populates the file record table (path, content_hash, language, size,
    modified_at) so that StatFilterPass can correctly skip unchanged files on
    subsequent incremental runs.
    """
    for file_path in processed_files:
        full_path = os.path.join(root_dir, file_path)
        try:
            stat = os.stat(full_path)
        except OSError:
            continue

        # Read content and compute hash
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue

        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        language = detect_language(file_path)

        # Count nodes for this file
        node_count = sum(1 for _ in store.iter_nodes_by_file(file_path))

        store.upsert_file(
            path=file_path,
            content_hash=content_hash,
            language=language,
            node_count=node_count,
            size=stat.st_size,
            modified_at=int(stat.st_mtime),
        )


# ============================================================================
# index
# ============================================================================

@app.command()
def index(
    project_path: str = typer.Argument(".", help="项目根目录"),
    force: bool = typer.Option(False, "--force", help="强制全量重建索引（跳过 content-hash 检查）"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径（默认: 项目目录/.tws/codegraph/index.db）"),
):
    """索引项目的所有源文件，构建代码关系图。"""
    root_dir = os.path.abspath(project_path)
    default_db = os.path.join(root_dir, DEFAULT_DB)
    db_path_resolved = db_path or default_db

    # 1. Scan source files
    files = scan_directory(root_dir)

    # 2. Create Store and clean up deleted files
    store = _get_store(db_path_resolved)
    _cleanup_deleted_files(store, files)

    if not files:
        from .indexer.registry import get_all_extensions
        exts = sorted(get_all_extensions())
        typer.echo(f"  警告: 未找到源文件 ({', '.join(exts)})", err=True)
        store.close()
        raise typer.Exit(1)

    # 3. Build and execute pipeline
    typer.echo(f"正在索引: {root_dir}")
    engine = _build_pipeline_engine(store)
    ctx = engine.execute(files=list(files), root_dir=root_dir, force=force)

    # 4. Manage file records (for StatFilterPass on subsequent runs)
    _upsert_file_records(store, ctx.files, root_dir)

    # 5. Post-processing
    store.rebuild_fts()
    store.optimize()

    # 6. Map pipeline context to output
    files_indexed = len(ctx.files)
    files_skipped = ctx.metadata.get("filtered_out", 0)
    nodes_created = ctx.metadata.get("node_count", 0)
    edges_created = ctx.metadata.get("edge_count", 0)
    files_errored = sum(
        1 for results in ctx.parsed_results.values()
        if results.get("errors")
    )

    typer.echo(f"  索引完成: {files_indexed} 个文件"
               f"{f' (+{files_skipped} 跳过)' if files_skipped else ''},"
               f" {nodes_created} 个符号,"
               f" {edges_created} 条关系,"
               f" 耗时 {ctx.duration_ms}ms")

    if files_errored:
        typer.echo(f"  {files_errored} 个文件解析失败", err=True)
        for err in ctx.errors[:3]:
            err_file = err.get("file_path", "")
            err_msg = err.get("error", str(err))
            typer.echo(f"    - {err_file}: {err_msg}", err=True)

    # 7. Skill indexing (uses store interface)
    store.flush()
    skill_count = _index_skills(root_dir, store)

    # 8. Database stats
    stats = store.stats()
    typer.echo(f"  数据库: {stats['node_count']} 节点, {stats['edge_count']} 边,"
               f" {stats['file_count']} 文件")

    # 9. Resolve stats
    cross_file = ctx.metadata.get("cross_file_resolved", {})
    if cross_file:
        resolved = cross_file.get("resolved", 0)
        ambiguous = cross_file.get("ambiguous", 0)
        unresolved = cross_file.get("unresolved", 0)
        total_checked = resolved + ambiguous + unresolved
        if total_checked > 0:
            typer.echo(f"  边解析: {resolved} 补全, {ambiguous} 歧义, {unresolved} 未解析"
                       f" (共检查 {total_checked} 条)")

    if skill_count > 0:
        typer.echo(f"  技能索引: {skill_count} 个 TWS skill")

    store.close()


def _index_skills(root_dir: str, store) -> int:
    """Index TWS skill .md files if the project contains them."""
    from .indexer.skill_parser import (
        extract_skill_file, extract_skill_refs,
        collect_known_skills, _read_file,
    )
    import os as _os

    dir_names, skill_paths, fm_to_dir = collect_known_skills(root_dir)
    if not dir_names:
        return 0

    count = 0
    for dir_name, rel_path in sorted(skill_paths.items()):
        full_path = _os.path.join(root_dir, rel_path)
        content = _read_file(full_path)
        if not content:
            continue

        # Delete old skill data via Store interface
        store.delete_nodes_by_file(rel_path, kind="skill")

        # Extract skill node
        extraction = extract_skill_file(rel_path, content)
        if extraction.nodes:
            store.insert_nodes(extraction.nodes)
            count += 1

        # Extract reference edges from body (using dir_names for matching)
        ref_edges = extract_skill_refs(rel_path, content, dir_names, skill_paths)
        if ref_edges:
            store.insert_edges(ref_edges)

    # Flush buffered writes, then rebuild FTS after skill nodes
    store.flush()
    store.rebuild_fts()

    return count


def _show_unresolved_hint_in_calls(queries, focal_node, direction: str = "inbound"):
    """If the focal node has unresolved call edges, show hints to the agent.

    Unresolved edges have real sources but dangling targets (external libs, etc.).
    They appear as outgoing edges FROM existing nodes TO unresolvable targets.
    """
    node_id = focal_node["id"]
    name = focal_node["name"]

    # Check outgoing unresolved calls (source is this node, target is unresolvable)
    out = queries._exec("""
        SELECT COUNT(*) as cnt FROM edges
        WHERE source = ? AND kind = 'calls' AND provenance = 'unresolved'
    """, (node_id,)).fetchone()
    if out and out["cnt"] > 0:
        if direction == "inbound":
            typer.echo(f"  [?] '{name}' 自身发出了 {out['cnt']} 条未解析的调用"
                       f"（可能为外部库/动态调度），这些调用者在图中不可见，需手动 grep/read")
        else:
            typer.echo(f"  [?] '{name}' 有 {out['cnt']} 条发出的调用无法解析目标"
                       f"（可能为外部库/动态调度），需手动 grep/read 确认")


# ============================================================================
# calls
# ============================================================================

@app.command()
def calls(
    symbol: str = typer.Argument(..., help="要查询的符号名"),
    inbound: bool = typer.Option(False, "--inbound", "-i", help="谁调用了这个符号（默认）"),
    outbound: bool = typer.Option(False, "--outbound", "-o", help="这个符号调了谁"),
    depth: int = typer.Option(1, "--depth", "-d", help="追溯深度"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """查询调用关系：谁调用了这个符号，或这个符号调了谁。

    示例：
      tws-graph calls calculateTotal --inbound
      tws-graph calls UserService --outbound --depth 2
    """
    # Default: inbound if neither flag is set
    if not inbound and not outbound:
        inbound = True

    direction = "both" if (inbound and outbound) else ("inbound" if inbound else "outbound")

    db = _get_db(db_path) if os.path.exists(db_path or DEFAULT_DB) else None
    if not db:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db.conn)
    node = _resolve_node(queries, symbol)
    if not node:
        typer.echo(f"未找到符号: {symbol}", err=True)
        raise typer.Exit(1)

    traverser = GraphTraverser(queries)
    result = traverser.get_calls(node["id"], direction=direction, max_depth=depth)

    if json_output:
        typer.echo(json.dumps(_serialize(result), ensure_ascii=False, indent=2))
    else:
        _print_calls_result(result, node, direction, depth, queries=queries)


def _print_calls_result(result: dict, focal_node, direction: str, depth: int, queries=None):
    """Format calls result as a tree."""
    nodes = result["nodes"]
    edges = result["edges"]
    root_id = result["roots"][0]

    root = nodes.get(root_id, focal_node)
    label = f"{root.get('name', '?')} ({root.get('file_path', '?')}:{root.get('start_line', '?')})"
    typer.echo(f"\n{label}")

    if not edges:
        typer.echo("  (没有找到已解析的调用关系)")
        if queries:
            _show_unresolved_hint_in_calls(queries, focal_node, direction)
        return

    # Group edges by depth / direction
    callers = [e for e in edges
               if (direction == "inbound" and e["target"] == root_id)
               or (direction == "outbound" and e["source"] == root_id)]

    if direction == "inbound":
        typer.echo(f"  ↑ 被以下 {len(callers)} 个符号调用:")
    elif direction == "outbound":
        typer.echo(f"  ↓ 调用了以下 {len(callers)} 个符号:")
    else:
        typer.echo(f"  ↔ 调用关系 ({len(edges)} 条边):")

    for edge in callers[:30]:
        neighbor_id = edge["source"] if direction == "inbound" else edge["target"]
        neighbor = nodes.get(neighbor_id, {})
        name = neighbor.get("name", neighbor_id[:16])
        fpath = neighbor.get("file_path", "?")
        line = neighbor.get("start_line", "?")
        kind = neighbor.get("kind", "?")
        vis = neighbor.get("visibility", "")
        vis_tag = f"({vis}) " if vis else ""
        typer.echo(f"    {vis_tag}{name} ({kind})  {fpath}:{line}")

    total_others = sum(1 for nid in nodes if nid != root_id and nid not in
                       {e.get("source") if direction == "inbound" else e.get("target")
                        for e in callers})
    if total_others > 0:
        typer.echo(f"  ... 还有 {total_others} 个间接相关符号 (depth={depth})")

    if queries:
        _show_unresolved_hint_in_calls(queries, focal_node, direction)


# ============================================================================
# impact
# ============================================================================

@app.command()
def impact(
    symbol: str = typer.Argument(..., help="要评估影响的符号名"),
    depth: int = typer.Option(2, "--depth", "-d", help="影响传播深度 (默认 2)"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """分析修改一个符号的影响范围。

    从符号出发沿调用者方向展开，按模块分组输出。

    示例：
      tws-graph impact calculateTotal
      tws-graph impact UserService.createOrder --depth 3
    """
    db = _get_db(db_path) if os.path.exists(db_path or DEFAULT_DB) else None
    if not db:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db.conn)
    node = _resolve_node(queries, symbol)
    if not node:
        typer.echo(f"未找到符号: {symbol}", err=True)
        raise typer.Exit(1)

    traverser = GraphTraverser(queries)
    result = traverser.get_impact_radius(node["id"], max_depth=depth)

    if json_output:
        typer.echo(json.dumps(_serialize(result), ensure_ascii=False, indent=2))
    else:
        _print_impact_result(result, node, depth, queries=queries)


def _print_impact_result(result: dict, focal_node, depth: int, queries=None):
    """Format impact result grouped by file."""
    nodes = result["nodes"]
    modules = result.get("modules", {})
    root_id = result["roots"][0]
    root = nodes.get(root_id, focal_node)

    name = root.get("name", "?")
    fpath = root.get("file_path", "?")
    line = root.get("start_line", "?")
    vis = root.get("visibility", "")
    vis_tag = f" ({vis})" if vis else ""

    typer.echo(f"\n{name}{vis_tag}  ({fpath}:{line})")

    if not modules:
        typer.echo("  (未发现依赖者)")
        if queries:
            _show_unresolved_hint_in_calls(queries, focal_node, "inbound")
        return

    # Direct callers (depth 1)
    direct_edges = [e for e in result["edges"] if e["target"] == root_id]
    direct_ids = {e["source"] for e in direct_edges}
    direct_nodes = {nid: n for nid, n in nodes.items() if nid in direct_ids}

    if direct_nodes:
        typer.echo(f"  直接调用者 ({len(direct_nodes)}):")
        for nid, n in sorted(direct_nodes.items(), key=lambda x: x[1].get("name", "")):
            n_vis = n.get("visibility", "")
            n_vis_tag = f"({n_vis}) " if n_vis else ""
            typer.echo(f"    {n_vis_tag}{n.get('name', '?')}"
                       f"  →  {n.get('file_path', '?')}:{n.get('start_line', '?')}")

    # Indirect (depth > 1)
    indirect = {nid: n for nid, n in nodes.items()
                if nid not in direct_ids and nid != root_id}
    if indirect:
        typer.echo(f"\n  间接影响 (depth={depth}, {len(indirect)} 个符号):")
        for mod, mod_nodes in sorted(modules.items()):
            rel = [n for n in mod_nodes if n["id"] in indirect]
            if rel:
                typer.echo(f"    [{mod}]  ({len(rel)} 个符号)")
                for n in rel[:5]:
                    typer.echo(f"      {n.get('name', '?')} ({n.get('kind', '?')})")
                if len(rel) > 5:
                    typer.echo(f"      ... 还有 {len(rel) - 5} 个")

    if queries:
        _show_unresolved_hint_in_calls(queries, focal_node, "inbound")


# ============================================================================
# trace
# ============================================================================

@app.command()
def trace(
    from_symbol: str = typer.Argument(..., help="入口符号"),
    to_symbol: str = typer.Argument(..., help="目标符号（报错点）"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """查找两个符号之间的完整调用链。

    如果某个符号对应多个节点（同名但不同文件），会尝试找到有路径的一对。

    示例：
      tws-graph trace main handleRequest
      tws-graph trace router.getUser validateInput
    """
    db = _get_db(db_path) if os.path.exists(db_path or DEFAULT_DB) else None
    if not db:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db.conn)

    from_nodes = queries.get_nodes_by_name(from_symbol) or queries.search_nodes(from_symbol, limit=3)
    to_nodes = queries.get_nodes_by_name(to_symbol) or queries.search_nodes(to_symbol, limit=3)

    if not from_nodes:
        typer.echo(f"未找到入口符号: {from_symbol}", err=True)
        raise typer.Exit(1)
    if not to_nodes:
        typer.echo(f"未找到目标符号: {to_symbol}", err=True)
        raise typer.Exit(1)

    traverser = GraphTraverser(queries)

    # Try all combinations to find a path
    for fn in from_nodes:
        for tn in to_nodes:
            path = traverser.find_path(fn["id"], tn["id"])
            if path:
                if json_output:
                    typer.echo(json.dumps(_serialize(path), ensure_ascii=False, indent=2))
                else:
                    _print_trace_result(path)
                return

    typer.echo(f"未找到从 '{from_symbol}' 到 '{to_symbol}' 的调用路径。"
               f"\n（可能是动态调度、闭包、回调，或跨模块引用未解析。）", err=True)
    raise typer.Exit(1)


def _print_trace_result(path: list[dict]):
    """Format trace path as numbered steps."""
    typer.echo(f"\n调用链 ({len(path)} 步):")
    for i, step in enumerate(path):
        node = step["node"]
        name = node.get("name", "?")
        fpath = node.get("file_path", "?")
        line = node.get("start_line", "?")
        kind = node.get("kind", "?")

        marker = "→ " if i == 0 else "   "
        if i > 0 and step.get("via_edge"):
            edge = step["via_edge"]
            typer.echo(f"     │  [{edge.get('kind', 'calls')}]"
                       f"  {edge.get('source_loc', '')}")
        typer.echo(f"  {i+1}. {marker}{name} ({kind})  {fpath}:{line}")


# ============================================================================
# snapshot
# ============================================================================

@app.command()
def snapshot(
    name: str = typer.Argument(..., help="快照名称（如 before、after、initial）"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """保存当前索引数据库的命名副本。

    示例：
      tws-graph snapshot before   # 改代码前拍快照
      tws-graph snapshot after    # 改代码后拍快照
      tws-graph diff before after # 对比差异
    """
    src = os.path.abspath(db_path or DEFAULT_DB)
    if not os.path.exists(src):
        typer.echo(f"错误: 索引数据库不存在 ({src})。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    snapshots_dir = os.path.dirname(src)
    dest = save_snapshot(src, name, snapshots_dir)
    typer.echo(f"快照已保存: {dest}")


# ============================================================================
# diff
# ============================================================================

@app.command()
def diff(
    before: Optional[str] = typer.Argument(None, help="改前快照名称"),
    after: Optional[str] = typer.Argument(None, help="改后快照名称"),
    brief: bool = typer.Option(False, "--brief", "-b", help="精简输出：仅 changed/unchanged"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径（用于解析快照目录）"),
):
    """对比两个索引快照，输出符号级差异报告。

    在改代码前用 tws-graph snapshot before 拍快照，
    改代码后用 tws-graph index + tws-graph snapshot after 拍快照，
    然后用此命令对比。

    示例：
      tws-graph diff              # 列出所有快照
      tws-graph diff before after
      tws-graph diff before after --brief  # 仅输出 changed/unchanged
    """
    base = os.path.abspath(db_path or DEFAULT_DB)
    snapshots_dir = os.path.dirname(base)

    # No arguments: list snapshots
    if not before and not after:
        available = list_snapshots(snapshots_dir)
        if available:
            typer.echo("可用快照:")
            for s in available:
                typer.echo(f"  {s}")
        else:
            typer.echo("无可用快照。请先运行 tws-graph snapshot <name>。")
        return

    if not before or not after:
        typer.echo("错误: 需要同时提供 BEFORE 和 AFTER 快照名称。", err=True)
        typer.echo("用法: tws-graph diff <before> <after>", err=True)
        raise typer.Exit(1)

    before_path = os.path.join(snapshots_dir, f"index-{before}.db")
    after_path = os.path.join(snapshots_dir, f"index-{after}.db")

    try:
        report = compare_snapshots(before_path, after_path)
    except FileNotFoundError as e:
        typer.echo(f"错误: {e}", err=True)
        available = list_snapshots(snapshots_dir)
        if available:
            typer.echo(f"可用快照: {', '.join(available)}")
        else:
            typer.echo("无可用快照。请先运行 tws-graph snapshot <name>。")
        raise typer.Exit(1)

    if json_output:
        typer.echo(json.dumps({
            "added_symbols": report.added_symbols,
            "removed_symbols": report.removed_symbols,
            "signature_changed": report.signature_changed,
            "added_edges": report.added_edges,
            "removed_edges": report.removed_edges,
            "affected_file_count": len(report.affected_files),
        }, ensure_ascii=False, indent=2))
    else:
        typer.echo(format_diff_report(report, brief=brief))


# ============================================================================
# helpers
# ============================================================================

def _serialize(obj):
    """Recursively convert sqlite3.Row objects to JSON-serializable types."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if hasattr(obj, "keys"):  # sqlite3.Row
        return {k: _serialize(obj[k]) for k in obj.keys()}
    return obj


# ============================================================================
# sync
# ============================================================================

@app.command()
def sync(
    project_path: str = typer.Argument(".", help="项目根目录"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """增量同步：仅重建 mtime 或 content hash 发生变化的文件。

    比 `tws-graph index` 快，适合放在 git hooks 中自动运行。

    示例：
      tws-graph sync
      tws-graph sync /path/to/project
    """
    root_dir = os.path.abspath(project_path)
    default_db = os.path.join(root_dir, DEFAULT_DB)
    db_path_resolved = db_path or default_db

    # 1. Scan source files
    files = scan_directory(root_dir)

    # 2. Create Store and clean up deleted files
    store = _get_store(db_path_resolved)
    _cleanup_deleted_files(store, files)

    if not files:
        from .indexer.registry import get_all_extensions
        exts = sorted(get_all_extensions())
        typer.echo(f"  警告: 未找到源文件 ({', '.join(exts)})", err=True)
        store.close()
        raise typer.Exit(1)

    # 3. Build and execute pipeline (force=False for incremental stat filter)
    engine = _build_pipeline_engine(store)
    ctx = engine.execute(files=list(files), root_dir=root_dir, force=False)

    # 4. Manage file records for changed files
    _upsert_file_records(store, ctx.files, root_dir)

    # 5. Post-processing
    store.rebuild_fts()
    store.optimize()

    # 6. Map pipeline context to output
    files_indexed = len(ctx.files)
    files_skipped = ctx.metadata.get("filtered_out", 0)
    nodes_created = ctx.metadata.get("node_count", 0)
    edges_created = ctx.metadata.get("edge_count", 0)

    if files_indexed == 0:
        typer.echo(f"  同步完成: 无变化 ({files_skipped} 个文件无需更新)"
                   f" 耗时 {ctx.duration_ms}ms")
    else:
        typer.echo(f"  同步完成: {files_indexed} 个文件更新"
                   f"{f' (+{files_skipped} 跳过)' if files_skipped else ''},"
                   f" {nodes_created} 符号, {edges_created} 关系,"
                   f" 耗时 {ctx.duration_ms}ms")

    # 7. Resolve stats
    cross_file = ctx.metadata.get("cross_file_resolved", {})
    if cross_file:
        resolved = cross_file.get("resolved", 0)
        ambiguous = cross_file.get("ambiguous", 0)
        unresolved = cross_file.get("unresolved", 0)
        total_checked = resolved + ambiguous + unresolved
        if total_checked > 0:
            typer.echo(f"  边解析: {resolved} 补全, {ambiguous} 歧义, {unresolved} 未解析")

    store.close()


# ============================================================================
# lint
# ============================================================================

@app.command()
def lint(
    project_path: str = typer.Argument(".", help="TWS Skills 项目根目录"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
):
    """检查 TWS skill 文件的结构完整性。

    检查规则：
      - YAML frontmatter 必须有 name/description
      - entry/flow/team 层必须有 <SUBAGENT-STOP> 标签
      - component/foundation 层不应有 <SUBAGENT-STOP> 标签
      - 交叉引用必须指向存在的 skill
      - 目录名前缀必须匹配 skill 层级
      - 检测未被引用的孤立 skill

    示例：
      tws-graph lint .
      tws-graph lint /path/to/TWS-Skills --json
    """
    from .skill_linter import lint_skills

    root_dir = os.path.abspath(project_path)
    report = lint_skills(root_dir)

    if json_output:
        typer.echo(json.dumps({
            "files_checked": report.files_checked,
            "errors": len(report.errors),
            "warnings": len(report.warnings),
            "items": [
                {
                    "skill": w.skill_name,
                    "file": w.file_path,
                    "rule": w.rule,
                    "message": w.message,
                    "severity": w.severity,
                }
                for w in report.warnings
            ]
        }, ensure_ascii=False, indent=2))
    else:
        errors = report.errors
        warnings = [w for w in report.warnings if w.severity == "warning"]

        if not report.warnings:
            typer.echo(f"  检查 {report.files_checked} 个文件，全部通过")
            return

        if errors:
            typer.echo(f"\n  {len(errors)} 个错误:")
            for w in errors:
                typer.echo(f"    [{w.rule}] {w.file_path}: {w.message}")

        if warnings:
            typer.echo(f"\n  {len(warnings)} 个警告:")
            for w in warnings:
                typer.echo(f"    [{w.rule}] {w.file_path}: {w.message}")

        typer.echo(f"\n  检查 {report.files_checked} 个文件:"
                   f" {len(errors)} 错误, {len(warnings)} 警告")

        if errors:
            raise typer.Exit(1)


# ============================================================================
# search
# ============================================================================

@app.command()
def search(
    query: list[str] = typer.Argument(..., help="搜索关键词。支持 field:value 限定语法，多个词用空格分隔"),
    limit: int = typer.Option(20, "--limit", "-n", help="最大结果数"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """全文搜索代码符号。

    示例：
      tws-graph search calculateTotal
      tws-graph search kind:function api
      tws-graph search lang:python kind:class controller --limit 10
    """
    query_str = " ".join(query)
    db = _get_db(db_path) if os.path.exists(db_path or DEFAULT_DB) else None
    if not db:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db.conn)

    # Detect if query has field qualifiers
    has_qualifiers = any(
        f in query_str for f in ("kind:", "lang:", "language:", "path:", "visibility:", "framework:")
    )
    if has_qualifiers:
        results = queries.search_nodes_field_qualified(query_str, limit=limit)
    else:
        results = queries.search_nodes(query_str, limit=limit)

    if not results:
        typer.echo(f"未找到匹配: {query_str}")
        return

    if json_output:
        typer.echo(json.dumps(_serialize(results), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"\n找到 {len(results)} 个结果:")
        for row in results:
            name = row["name"]
            kind = row["kind"]
            fpath = row["file_path"]
            line = row["start_line"]
            lang = row["language"]
            sig = row["signature"] or ""
            sig_short = f"  ({sig[:50]}...)" if sig and len(sig) > 50 else f"  ({sig})" if sig else ""
            typer.echo(f"  {name} [{kind}] ({lang}) {fpath}:{line}{sig_short}")


# ============================================================================
# unresolved
# ============================================================================

@app.command()
def unresolved(
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """列出所有未解析的引用。

    大部分未解析引用是外部 SDK/库的符号（如 Android SDK、JDK、第三方库），
    这些符号不在项目源码里，无法解析是正常的。关注项目内部未能解析的引用即可。
    """
    db = _get_db(db_path) if os.path.exists(db_path or DEFAULT_DB) else None
    if not db:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db.conn)
    refs = queries.get_all_unresolved_refs()

    if not refs:
        typer.echo("没有未解析的引用。")
        return

    if json_output:
        typer.echo(json.dumps(_serialize(refs), ensure_ascii=False, indent=2))
    else:
        # Classify: external (SDK/lib) vs internal (project-level)
        ext_count = 0
        int_count = 0
        for r in refs:
            if r["is_external"]:
                ext_count += 1
            else:
                int_count += 1

        typer.echo(f"\n{len(refs)} 条未解析的引用:")
        typer.echo(f"  [external] 外部 SDK/库: {ext_count} 条（正常，这些符号不在项目源码里）")
        if int_count > 0:
            typer.echo(f"  [internal] 内部引用:   {int_count} 条（需要关注，项目内符号未能解析）")
        else:
            typer.echo(f"  [internal] 内部引用:   0 条")

        # Group by file
        from collections import defaultdict
        by_file = defaultdict(list)
        for r in refs:
            by_file[r["file_path"]].append(r)
        for fpath, file_refs in sorted(by_file.items()):
            typer.echo(f"  [{fpath}] ({len(file_refs)} 条)")
            for r in file_refs[:5]:
                tag = "[external]" if r["is_external"] else "[internal]"
                typer.echo(f"    {tag} {r['reference_name']} ({r['reference_kind']})"
                           f"  来自 {r['from_node_id'][:12]}...")
            if len(file_refs) > 5:
                typer.echo(f"    ... 还有 {len(file_refs) - 5} 条")


# ============================================================================
# hooks
# ============================================================================

@app.command()
def hooks(
    action: str = typer.Argument(..., help="install | remove | status"),
    project_path: str = typer.Argument(".", help="项目根目录（git 仓库）"),
):
    """管理 git hooks，自动增量同步索引。

    install: 安装 post-commit / post-merge / post-checkout hooks
    remove:  卸载 TWS 管理的 hooks
    status:  查看 hooks 安装状态

    示例：
      tws-graph hooks install
      tws-graph hooks status
      tws-graph hooks remove
    """
    from .hooks import install_hooks, remove_hooks, status_hooks

    root = os.path.abspath(project_path)

    if action == "install":
        try:
            inst, skipped = install_hooks(root)
            typer.echo(f"已安装 {inst} 个 hooks"
                       f"{f' (+{skipped} 已存在跳过)' if skipped else ''}")
        except FileNotFoundError as e:
            typer.echo(f"错误: {e}", err=True)
            raise typer.Exit(1)

    elif action == "remove":
        try:
            cleaned = remove_hooks(root)
            typer.echo(f"已卸载 {cleaned} 个 hooks")
        except FileNotFoundError as e:
            typer.echo(f"错误: {e}", err=True)
            raise typer.Exit(1)

    elif action == "status":
        try:
            st = status_hooks(root)
            for name, installed in st.items():
                tag = "已安装" if installed else "未安装"
                typer.echo(f"  {name}: {tag}")
        except FileNotFoundError as e:
            typer.echo(f"错误: {e}", err=True)
            raise typer.Exit(1)

    else:
        typer.echo(f"未知动作: {action}。可用: install, remove, status", err=True)
        raise typer.Exit(1)


# ============================================================================
# analyze
# ============================================================================

@app.command()
def analyze(
    algorithm: str = typer.Option("all", help="Algorithm: clone/community/centrality/cycle/all"),
    edge_kinds: str = typer.Option("calls", help="Edge kinds to consider (comma-separated)"),
    threshold: float = typer.Option(0.8, help="Clone detection similarity threshold"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """Run graph analysis algorithms on the indexed code graph.

    示例：
      tws-graph analyze --algorithm clone
      tws-graph analyze --algorithm community --edge-kinds calls,imports
      tws-graph analyze --algorithm all --json
    """
    resolved_db = os.path.abspath(db_path) if db_path else os.path.abspath(DEFAULT_DB)

    if not os.path.exists(resolved_db):
        typer.echo(
            f"错误: 索引数据库不存在 ({resolved_db})。"
            f"请先运行 tws-graph index。",
            err=True,
        )
        raise typer.Exit(1)

    edge_kinds_list = [k.strip() for k in edge_kinds.split(",") if k.strip()]

    algorithms_map: dict[str, object] = {
        "clone": CloneDetector(threshold=threshold),
        "community": CommunityDetector(edge_kinds=edge_kinds_list),
        "centrality": CentralityComputer(algorithm="pagerank", edge_kinds=edge_kinds_list),
        "cycle": CycleDetector(),
    }

    if algorithm == "all":
        to_run = algorithms_map
    elif algorithm in algorithms_map:
        to_run = {algorithm: algorithms_map[algorithm]}
    else:
        typer.echo(
            f"未知算法: {algorithm}。可用: clone, community, centrality, cycle, all",
            err=True,
        )
        raise typer.Exit(1)

    store = _get_store(resolved_db)
    try:
        if json_output:
            results: dict[str, object] = {}
            for algo_name, algo in to_run.items():
                result = algo.run(store)
                results[algo_name] = {
                    "algorithm": result.algorithm,
                    "success": result.success,
                    "data": result.data,
                    "errors": result.errors,
                    "duration_ms": result.duration_ms,
                }
            typer.echo(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            for algo_name, algo in to_run.items():
                result = algo.run(store)
                typer.echo(f"\n--- {algo.description} ---")
                typer.echo(f"Duration: {result.duration_ms:.0f}ms")
                _print_algorithm_result(algo_name, result)
    finally:
        store.close()


def _print_algorithm_result(algo_name: str, result) -> None:
    """Format algorithm result for terminal display."""
    data = result.data

    if algo_name == "clone":
        pairs = data.get("similar_pairs", [])
        total = data.get("total_functions_checked", 0)
        thr = data.get("threshold", 0.0)
        typer.echo(f"  Functions checked: {total}")
        typer.echo(f"  Threshold: {thr}")
        typer.echo(f"  Similar pairs found: {len(pairs)}")
        for pair in pairs[:10]:
            typer.echo(
                f"    {pair['name_a']} <-> {pair['name_b']} "
                f"(similarity: {pair['similarity']:.4f})"
            )
        if len(pairs) > 10:
            typer.echo(f"    ... and {len(pairs) - 10} more pairs")

    elif algo_name == "community":
        communities = data.get("communities", [])
        mod = data.get("modularity", 0.0)
        typer.echo(f"  Communities: {len(communities)}")
        typer.echo(f"  Modularity: {mod:.4f}")
        for comm in communities[:5]:
            size = comm.get("size", 0)
            members = comm.get("members", [])
            preview = ", ".join(m[:40] for m in members[:5])
            typer.echo(f"    {comm['id']}: {size} members [{preview}...]")
        if len(communities) > 5:
            typer.echo(f"    ... and {len(communities) - 5} more communities")

    elif algo_name == "centrality":
        scores = data.get("scores", {})
        method = data.get("method", "pagerank")
        typer.echo(f"  Method: {method}")
        typer.echo(f"  Nodes scored: {len(scores)}")
        items = list(scores.items())[:10]
        for nid, score in items:
            typer.echo(f"    {nid[:32]}...: {score:.6f}")
        if len(scores) > 10:
            typer.echo(f"    ... and {len(scores) - 10} more nodes")

    elif algo_name == "cycle":
        cycles = data.get("cycles", [])
        total = data.get("total_cycles", 0)
        typer.echo(f"  Cycles found: {total}")
        for c in cycles[:5]:
            names = c.get("cycle", [])
            length = c.get("length", 0)
            typer.echo(f"    Length {length}: {' -> '.join(names[:5])}")
        if len(cycles) > 5:
            typer.echo(f"    ... and {len(cycles) - 5} more cycles")
        if data.get("max_cycles_reached"):
            typer.echo(f"  (result capped at max_cycles limit)")


# ============================================================================
# watch
# ============================================================================

@app.command()
def watch(
    path: str = typer.Option(".", help="Directory to watch"),
    interval: float = typer.Option(2.0, help="Polling interval in seconds"),
    debounce_ms: int = typer.Option(300, help="Debounce window in milliseconds"),
    json_output: bool = typer.Option(False, "--json", help="JSON Lines 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """Watch directory for changes and auto-sync the index.

    Uses file polling to detect changes, with debounce to avoid
    repeated indexing.  Changed files are re-indexed via ``tws-graph sync``.

    示例：
      tws-graph watch
      tws-graph watch --path /path/to/project --interval 3.0
      tws-graph watch --debounce-ms 500 --json
    """
    import signal
    import subprocess
    import threading
    from datetime import datetime

    root_dir = os.path.abspath(path)
    resolved_db = db_path if db_path else os.path.join(root_dir, DEFAULT_DB)

    if not os.path.exists(resolved_db):
        typer.echo(
            f"错误: 索引数据库不存在 ({resolved_db})。"
            f"请先运行 tws-graph index。",
            err=True,
        )
        raise typer.Exit(1)

    # Track changed files for debounced sync
    changed_files: list[str] = []
    changed_files_lock = threading.Lock()

    def _on_file_change(event: FileChangeEvent) -> None:
        """Handler called after debounce window expires."""
        nonlocal changed_files
        with changed_files_lock:
            changed_files.append(event.path)

    def _on_debounce_flush(event: FileChangeEvent) -> None:
        """Handler called when debounce queue flushes."""
        nonlocal changed_files
        with changed_files_lock:
            if event.path not in changed_files:
                changed_files.append(event.path)

    # Create debounce queue
    debounce_config = DebounceConfig(window_ms=debounce_ms, max_wait_ms=2000)
    debounce = DebounceQueue(debounce_config, callback=_on_debounce_flush)

    def _handle_raw_event(event: FileChangeEvent) -> None:
        """Raw file change event from watcher."""
        if json_output:
            record = {
                "timestamp": datetime.now().isoformat(),
                "event": "changed",
                "path": event.path,
                "change_type": event.change_type,
            }
            typer.echo(json.dumps(record, ensure_ascii=False))
        else:
            ts = datetime.now().strftime("%H:%M:%S")
            typer.echo(f"[{ts}] [{event.change_type}] {event.path}")

        # Attempt incremental sync via subprocess for robustness
        try:
            subprocess.run(
                ["tws-graph", "sync", root_dir, "--db", resolved_db],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            pass

        debounce.push(event)

    # Create watcher (polling-based, no external dependencies)
    watcher = PollingFileWatcher(polling_interval=interval)

    # Signal handling
    stop_event = threading.Event()

    def _signal_handler(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except ValueError:
        pass  # Not in main thread

    if json_output:
        record = {
            "timestamp": datetime.now().isoformat(),
            "event": "started",
            "backend": "polling",
            "debounce_ms": debounce_ms,
            "interval": interval,
            "root_dir": root_dir,
        }
        typer.echo(json.dumps(record, ensure_ascii=False))
    else:
        ts = datetime.now().strftime("%H:%M:%S")
        typer.echo(f"[{ts}] Watcher started (backend: polling, interval: {interval}s, debounce: {debounce_ms}ms)")
        typer.echo(f"[{ts}] Watching {root_dir} for changes... (Ctrl+C to stop)")

    watcher.start(root_dir, callback=_handle_raw_event)

    try:
        stop_event.wait()
    finally:
        debounce.stop()
        watcher.stop()

        if json_output:
            record = {
                "timestamp": datetime.now().isoformat(),
                "event": "stopped",
            }
            typer.echo(json.dumps(record, ensure_ascii=False))
        else:
            ts = datetime.now().strftime("%H:%M:%S")
            typer.echo(f"[{ts}] Watcher stopped.")


# ============================================================================
# query
# ============================================================================

@app.command()
def query(
    query_str: str = typer.Argument(..., help="Cypher 查询字符串（如 'MATCH (n) RETURN n'）"),
    limit: int = typer.Option(100, "--limit", "-n", help="最大结果行数"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """用 Cypher 语法查询代码图。

    支持 MATCH / WHERE / RETURN / ORDER BY / SKIP / LIMIT。

    示例：
      tws-graph query "MATCH (n) RETURN n LIMIT 10"
      tws-graph query "MATCH (n:Function) RETURN n.name, n.file_path"
      tws-graph query "MATCH (n) WHERE n.name = 'main' RETURN n"
      tws-graph query "MATCH (n) RETURN n ORDER BY n.name ASC LIMIT 5"
    """
    from .cypher import CypherEngine
    from .cypher.errors import (
        CypherLexerError,
        CypherSyntaxError,
        CypherSemanticError,
        CypherExecutionError,
    )

    resolved_db = os.path.abspath(db_path) if db_path else os.path.abspath(DEFAULT_DB)

    # Check if database exists before trying to open
    if not os.path.exists(resolved_db):
        typer.echo(
            f"错误: 索引数据库不存在 ({resolved_db})。"
            f"请先运行 tws-graph index。",
            err=True,
        )
        raise typer.Exit(1)

    store = _get_store(resolved_db)
    try:
        engine = CypherEngine()
        result = engine.execute(query_str, store)

        if json_output:
            rows_data = []
            for row in result.rows:
                row_dict = {}
                for k, v in row.data.items():
                    row_dict[k] = _serialize(v)
                rows_data.append(row_dict)
            typer.echo(json.dumps({
                "columns": result.columns,
                "rows": rows_data,
                "total_count": result.total_count,
            }, ensure_ascii=False, indent=2))
        else:
            if not result.rows:
                typer.echo("(无结果)")
            else:
                _print_query_table(result, limit)
    except CypherLexerError as e:
        typer.echo(f"词法错误: {e}", err=True)
        raise typer.Exit(1)
    except CypherSyntaxError as e:
        typer.echo(f"语法错误: {e}", err=True)
        raise typer.Exit(1)
    except CypherSemanticError as e:
        typer.echo(f"语义错误: {e}", err=True)
        raise typer.Exit(1)
    except CypherExecutionError as e:
        typer.echo(f"执行错误: {e}", err=True)
        raise typer.Exit(1)
    finally:
        store.close()


def _print_query_table(result, limit: int) -> None:
    """Print query result as an aligned text table.

    Args:
        result: The ResultSet to print.
        limit: Maximum number of rows to display.
    """
    rows_to_print = result.rows[:limit]
    cols = result.columns

    if not cols:
        typer.echo(f"(结果列: {result.total_count} 行)")
        return

    # Calculate column widths (capped at 60 for readability)
    widths: dict[str, int] = {}
    for col in cols:
        widths[col] = len(col)
    for row in rows_to_print:
        for col in cols:
            val = row.data.get(col)
            if val is None:
                val_str = "None"
            else:
                val_str = str(val)
            widths[col] = max(widths[col], min(len(val_str), 60))

    # Print header
    header = " | ".join(col.ljust(widths[col]) for col in cols)
    typer.echo(header)
    typer.echo("-" * min(len(header), 120))

    # Print rows (truncate long values for display)
    for row in rows_to_print:
        parts: list[str] = []
        for col in cols:
            val = row.data.get(col, "")
            val_str = str(val) if val is not None else "None"
            if len(val_str) > widths[col]:
                val_str = val_str[:widths[col] - 3] + "..."
            parts.append(val_str.ljust(widths[col]))
        typer.echo(" | ".join(parts))

    remaining = result.total_count - len(rows_to_print)
    if remaining > 0:
        typer.echo(f"\n(显示前 {len(rows_to_print)} 行，共 {result.total_count} 行)"
                   f"{'，使用 --limit 调整' if limit == 100 else ''}")


# ============================================================================
# lsp setup
# ============================================================================


@lsp_app.command("setup")
def lsp_setup(
    json_output: bool = typer.Option(
        False, "--json",
        help="JSON 格式输出",
    ),
):
    """扫描注册的适配器，检测各 LSP server 可用性。

    输出终端表格（语言 / binary / 状态 / 路径），支持 --json 输出 JSON 格式。

    示例：
      tws-graph lsp setup
      tws-graph lsp setup --json
    """
    from tws_graph.lsp.discovery import discover_all
    from tws_graph.lsp.adapters import get_all_adapters

    adapters = get_all_adapters()
    results = discover_all(adapters)

    if json_output:
        _print_lsp_setup_json(results)
    else:
        _print_lsp_setup_table(results)


def _print_lsp_setup_json(results: dict) -> None:
    """Output discovery results as pretty-printed JSON."""
    serialized: dict[str, dict] = {}
    for language, r in results.items():
        serialized[language] = {
            "language": r.language,
            "binary": r.binary,
            "available": r.available,
            "path": r.path,
            "error": r.error,
        }
    typer.echo(json.dumps(serialized, ensure_ascii=False, indent=2))


def _print_lsp_setup_table(results: dict) -> None:
    """Output discovery results as a terminal table.

    Columns: language | binary | status | path/error
    """
    if not results:
        typer.echo("(没有注册的 LSP 适配器)")
        return

    # Column widths
    lang_width = max(max(len(r.language) for r in results.values()), len("语言"))
    binary_width = max(max(len(r.binary) for r in results.values()), len("Binary"))
    status_width = max(len("状态"), 8)
    path_width = 60

    # Header
    header = (
        f"{'语言':<{lang_width}}  "
        f"{'Binary':<{binary_width}}  "
        f"{'状态':<{status_width}}  "
        f"{'路径 / 错误':<{path_width}}"
    )
    typer.echo(header)
    typer.echo("-" * len(header))

    # Rows sorted by language name
    for language, r in sorted(results.items()):
        if r.available:
            status = typer.style("OK", fg=typer.colors.GREEN)
        else:
            status = typer.style("FAIL", fg=typer.colors.RED)
        detail = r.path if r.available else (r.error or "未知错误")
        # Truncate detail if too long
        detail_str = str(detail) if detail else "-"
        if len(detail_str) > path_width:
            detail_str = detail_str[:path_width - 3] + "..."

        row = (
            f"{r.language:<{lang_width}}  "
            f"{r.binary:<{binary_width}}  "
            f"{status:<{status_width}}  "
            f"{detail_str:<{path_width}}"
        )
        typer.echo(row)


def main():
    app()


if __name__ == "__main__":
    main()
