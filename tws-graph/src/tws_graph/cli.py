"""TWS Code Graph CLI — typer-based command-line interface.

v7.0.0: Rust core is the only supported backend. Python fallback code has
been removed. All commands require the _core native library.

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

import json
import os
import sys
from typing import Optional

import typer

from . import __version__
from .store import SqliteStore

app = typer.Typer(
    name="tws-graph",
    help="TWS Code Graph — 预建代码符号关系图，agent 查图而非搜索",
)

# LSP sub-command group
lsp_app = typer.Typer(help="LSP (Language Server Protocol) 集成管理")
app.add_typer(lsp_app, name="lsp", help="LSP 集成管理")

# MCP serve sub-command group
serve_app = typer.Typer(help="MCP (Model Context Protocol) 服务器")
app.add_typer(serve_app, name="serve", help="启动 MCP 服务器")


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
        from .store.connection import configure_connection

        conn = _sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = _sqlite3.Row
        configure_connection(conn)
        runner = MigrationRunner(conn)
        runner.migrate()
        conn.close()
    else:
        # Existing database: ensure properties column exists
        # (old schema created by DatabaseConnection.initialize() lacks it)
        from .store.connection import configure_connection

        conn = _sqlite3.connect(db_path, isolation_level=None)
        conn.row_factory = _sqlite3.Row
        configure_connection(conn)
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


def _sync_files_from_nodes(store: SqliteStore, root_dir: str = "") -> None:
    """Populate the files table from the nodes table.

    The Rust index creates nodes/edges but does not populate the files tracking
    table. This function fills it so that get_file_count() returns correct
    results and incremental sync can detect changes properly.
    """
    import time as _time
    now = int(_time.time())
    try:
        conn = store._conn_mgr.conn
        # Collect distinct file paths from nodes
        rows = conn.execute(
            "SELECT DISTINCT file_path FROM nodes WHERE file_path IS NOT NULL"
        ).fetchall()
        for (file_path,) in rows:
            # Try to get real file stats from disk
            size = 0
            modified_at = 0
            full_path = os.path.join(root_dir, file_path) if root_dir else file_path
            try:
                st = os.stat(full_path)
                size = st.st_size
                modified_at = int(st.st_mtime)
            except OSError:
                pass
            conn.execute(
                "INSERT OR IGNORE INTO files (path, content_hash, language, node_count, indexed_at, size, modified_at) "
                "VALUES (?, '', 'python', 0, ?, ?, ?)",
                (file_path, now, size, modified_at),
            )
        conn.commit()
    except Exception:
        pass  # files table may not exist or have different schema


# ============================================================================
# index
# ============================================================================

@app.command()
def index(
    project_path: str = typer.Argument(".", help="项目根目录"),
    force: bool = typer.Option(False, "--force", help="强制全量重建索引（跳过 content-hash 检查）"),
    deep: bool = typer.Option(False, "--deep", help="启用深度分析（代码克隆检测 similar_to 边，耗时较长）"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径（默认: 项目目录/.tws/codegraph/index.db）"),
    twsignore: Optional[str] = typer.Option(None, "--twsignore", help=".twsignore 文件路径（默认: 项目根/.twsignore；传空串禁用忽略规则）"),
    include_patterns: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_patterns: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """索引项目的所有源文件，构建代码关系图。

    默认自动读取项目根目录的 .twsignore 文件来排除文件。
    使用 --twsignore 可指定自定义文件路径；使用 --twsignore '' 可禁用忽略规则。

    --include / --exclude 支持 glob 模式，可重复指定。
    例: tws-graph index --include "src/" --exclude "tests/"
    """
    root_dir = os.path.abspath(project_path)
    default_db = os.path.join(root_dir, DEFAULT_DB)
    db_path_resolved = db_path or default_db

    # Rust core is the only backend
    from .rust_bridge import rust_index, _rust_available
    if not _rust_available():
        typer.echo("错误: Rust 核心库不可用。请重新安装 tws-graph。", err=True)
        raise typer.Exit(1)

    # Resolve twsignore path
    twsignore_resolved: str | None = twsignore
    import time as _time
    typer.echo(f"正在索引: {root_dir}")
    _t0 = _time.time()
    result = rust_index(str(db_path_resolved), str(root_dir), twsignore_resolved,
                        include_patterns, exclude_patterns)
    _duration_ms = int((_time.time() - _t0) * 1000)
    # Populate files table from nodes (Rust index fills nodes but not files)
    store = _get_store(db_path_resolved)
    _sync_files_from_nodes(store, root_dir)
    # Handle empty project (no source files found)
    if store.count_nodes() == 0:
        from .indexer.registry import get_all_extensions
        exts = sorted(get_all_extensions())
        typer.echo(f"  警告: 未找到源文件 ({', '.join(exts)})", err=True)
        store.close()
        raise typer.Exit(1)
    skill_count = _index_skills(root_dir, store)
    store.rebuild_fts()
    store.optimize()
    stats = store.stats()
    typer.echo(f"  索引完成: {stats['file_count']} 个文件,"
               f" {stats['node_count']} 个符号,"
               f" {stats['edge_count']} 条关系,"
               f" 耗时 {_duration_ms}ms")
    typer.echo(f"  数据库: {stats['node_count']} 节点, {stats['edge_count']} 边,"
               f" {stats['file_count']} 文件")
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

    # Flush buffered writes (FTS rebuild is handled by caller)
    store.flush()

    return count


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
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """查询调用关系：谁调用了这个符号，或这个符号调了谁。

    示例：
      tws-graph calls calculateTotal --inbound
      tws-graph calls UserService --outbound --depth 2
    """
    if not inbound and not outbound:
        inbound = True

    resolved_db = os.path.abspath(db_path or DEFAULT_DB)

    if not os.path.exists(resolved_db):
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    from .rust_bridge import rust_calls, _rust_available
    if not _rust_available():
        typer.echo("错误: Rust 核心库不可用。", err=True)
        raise typer.Exit(1)

    typer.echo(rust_calls(resolved_db, symbol, inbound, depth, include_paths, exclude_paths))


# ============================================================================
# impact
# ============================================================================

@app.command()
def impact(
    symbol: str = typer.Argument(..., help="要评估影响的符号名"),
    depth: int = typer.Option(2, "--depth", "-d", help="影响传播深度 (默认 2)"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """分析修改一个符号的影响范围。"""
    resolved_db = os.path.abspath(db_path or DEFAULT_DB)
    if not os.path.exists(resolved_db):
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)
    from .rust_bridge import rust_impact, _rust_available
    if not _rust_available():
        typer.echo("错误: Rust 核心库不可用。", err=True)
        raise typer.Exit(1)
    typer.echo(rust_impact(resolved_db, symbol, depth, include_paths, exclude_paths))


# ============================================================================
# trace
# ============================================================================

@app.command()
def trace(
    from_symbol: str = typer.Argument(..., help="入口符号"),
    to_symbol: str = typer.Argument(..., help="目标符号（报错点）"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """查找两个符号之间的完整调用链。"""
    resolved_db = os.path.abspath(db_path or DEFAULT_DB)
    if not os.path.exists(resolved_db):
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)
    from .rust_bridge import rust_trace, _rust_available
    if not _rust_available():
        typer.echo("错误: Rust 核心库不可用。", err=True)
        raise typer.Exit(1)
    typer.echo(rust_trace(resolved_db, from_symbol, to_symbol, include_paths, exclude_paths))


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
    from .rust_bridge import rust_snapshot_create, _rust_available
    if not _rust_available():
        typer.echo("错误: Rust 核心库不可用。", err=True)
        raise typer.Exit(1)
    typer.echo(rust_snapshot_create(src, name))


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
    """对比两个索引快照，输出符号级差异报告。"""
    base = os.path.abspath(db_path or DEFAULT_DB)
    from .rust_bridge import rust_snapshot_list, rust_snapshot_diff, _rust_available
    if not _rust_available():
        typer.echo("错误: Rust 核心库不可用。", err=True)
        raise typer.Exit(1)
    if not before and not after:
        typer.echo(rust_snapshot_list(base))
        return
    if before and after:
        typer.echo(rust_snapshot_diff(base, before, after))
        return

    typer.echo("错误: 需要同时提供 BEFORE 和 AFTER 快照名称。", err=True)
    raise typer.Exit(1)


# ============================================================================
# sync
# ============================================================================

@app.command()
def sync(
    project_path: str = typer.Argument(".", help="项目根目录"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
    twsignore: Optional[str] = typer.Option(None, "--twsignore", help=".twsignore 文件路径（默认: 项目根/.twsignore；传空串禁用忽略规则）"),
    include_patterns: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_patterns: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """增量同步。v7.0.0: 调用 Rust index（Rust 核心内置增量逻辑）。

    默认自动读取项目根目录的 .twsignore 文件来排除文件。
    使用 --twsignore 可指定自定义文件路径；使用 --twsignore '' 可禁用忽略规则。
    """
    root_dir = os.path.abspath(project_path)
    default_db = os.path.join(root_dir, DEFAULT_DB)
    db_path_resolved = db_path or default_db

    from .rust_bridge import rust_index, _rust_available
    if not _rust_available():
        typer.echo("错误: Rust 核心库不可用。", err=True)
        raise typer.Exit(1)

    # Resolve twsignore path
    twsignore_resolved: str | None = twsignore
    import time as _time
    typer.echo(f"正在同步: {root_dir}")
    _t0 = _time.time()
    result = rust_index(str(db_path_resolved), str(root_dir), twsignore_resolved,
                        include_patterns, exclude_patterns)
    _duration_ms = int((_time.time() - _t0) * 1000)
    store = _get_store(db_path_resolved)
    _sync_files_from_nodes(store, root_dir)
    skill_count = _index_skills(root_dir, store)
    if skill_count > 0 or store.count_nodes() > 0:
        store.rebuild_fts()
        store.optimize()
    stats = store.stats()
    typer.echo(f"  同步完成: {stats['file_count']} 文件,"
               f" {stats['node_count']} 符号,"
               f" {stats['edge_count']} 关系,"
               f" 耗时 {_duration_ms}ms")
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
    root_dir = os.path.abspath(project_path)

    # Try Rust acceleration
    from .rust_bridge import rust_lint, _rust_available
    if _rust_available():
        result = rust_lint(root_dir, json_output)
        typer.echo(result)
        # Check JSON for errors to set exit code
        if json_output:
            import json
            try:
                parsed = json.loads(result)
                if parsed.get("errors", 0) > 0:
                    raise typer.Exit(1)
            except json.JSONDecodeError:
                pass
        return

    from .skill_linter import lint_skills
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
    semantic: bool = typer.Option(False, "--semantic", help="启用 11-signal 语义搜索"),
    signal_weights: Optional[str] = typer.Option(None, "--weights", help="JSON 格式信号权重覆盖"),
    embeddings: bool = typer.Option(False, "--embeddings", help="启用向量增强"),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """全文搜索代码符号。

    示例：
      tws-graph search calculateTotal
      tws-graph search kind:function api
      tws-graph search lang:python kind:class controller --limit 10
      tws-graph search tax --semantic --limit 10
      tws-graph search auth --semantic --weights '{"BM25":2.0}'
    """
    query_str = " ".join(query)

    # ------------------------------------------------------------------
    # Semantic search path
    # ------------------------------------------------------------------
    if semantic:
        db = _get_db(db_path) if os.path.exists(db_path or DEFAULT_DB) else None
        if not db:
            typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
            raise typer.Exit(1)

        # Parse optional signal weights
        weights_dict = None
        if signal_weights:
            try:
                weights_dict = json.loads(signal_weights)
            except json.JSONDecodeError as e:
                typer.echo(f"错误: 无效的 --weights JSON: {e}", err=True)
                raise typer.Exit(1)

        # Pass QueryBuilder so _build_ctx can create GraphTraverser
        queries = QueryBuilder(db.conn)

        result = semantic_query(
            query=query_str,
            store_or_db_path=queries,
            limit=limit,
            signal_weights=weights_dict,
            use_embeddings=embeddings,
        )

        if not result.results:
            typer.echo(f"未找到匹配: {query_str}")
            return

        if json_output:
            output = {
                "query": result.query,
                "candidate_count": result.candidate_count,
                "duration_ms": result.duration_ms,
                "embeddings_enabled": result.embeddings_enabled,
                "results": _serialize(result.results),
            }
            typer.echo(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            typer.echo(
                f"\n语义搜索: \"{result.query}\" "
                f"(候选 {result.candidate_count}, "
                f"{result.duration_ms:.1f}ms)"
            )
            for row in result.results:
                name = row.get("name")
                kind = row.get("kind")
                fpath = row.get("file_path")
                line = row.get("start_line")
                lang = row.get("language")
                score = row.get("_score", 0.0)
                sig = row.get("signature") or ""
                sig_short = f"  ({sig[:50]}...)" if sig and len(sig) > 50 else f"  ({sig})" if sig else ""
                typer.echo(
                    f"  {name} [{kind}] ({lang}) {fpath}:{line}{sig_short} "
                    f"score={score:.3f}"
                )
        return

    # ------------------------------------------------------------------
    # Existing FTS5 / field-qualified search path
    # ------------------------------------------------------------------
    resolved_db = os.path.abspath(db_path or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_search, _rust_available
        if _rust_available():
            results = rust_search(resolved_db, query_str, limit, include_paths, exclude_paths)
            if not results:
                typer.echo(f"未找到匹配: {query_str}")
                return
            if json_output:
                typer.echo(json.dumps(_serialize(results), ensure_ascii=False, indent=2))
            else:
                typer.echo(f"\n找到 {len(results)} 个结果:")
                for row in results:
                    name = row.get("name", row.get("qualified_name", ""))
                    kind = row.get("kind", "?")
                    fpath = row.get("file_path", "?")
                    line = row.get("start_line", "?")
                    lang = row.get("language", "?")
                    sig = row.get("signature") or ""
                    sig_short = f"  ({sig[:50]}...)" if sig and len(sig) > 50 else f"  ({sig})" if sig else ""
                    typer.echo(f"  {name} [{kind}] ({lang}) {fpath}:{line}{sig_short}")
            return

    db = _get_db(db_path) if os.path.exists(db_path or DEFAULT_DB) else None
    if not db:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db.conn)

    # Check for federation
    from .federation import load_federation
    fed_registry = load_federation(".") if db_path is None else load_federation(
        os.path.dirname(os.path.dirname(os.path.abspath(db_path)))
    )

    # Detect if query has field qualifiers
    has_qualifiers = any(
        f in query_str for f in ("kind:", "lang:", "language:", "path:", "visibility:", "framework:")
    )
    if has_qualifiers:
        results = list(queries.search_nodes_field_qualified(query_str, limit=limit))
    else:
        results = list(queries.search_nodes(query_str, limit=limit))

    # Federation: cross-repo search
    if fed_registry.is_active():
        current_db = db_path or DEFAULT_DB
        if not os.path.isabs(current_db):
            current_db = os.path.join(os.getcwd(), current_db)
        fed_results = _cross_repo_search_federated(
            query_str, limit, queries, fed_registry, current_db,
        )
        if fed_results:
            # Merge and deduplicate by node id
            seen_ids = {r["id"] for r in results}
            for fr in fed_results:
                if fr.get("id") and fr["id"] not in seen_ids:
                    seen_ids.add(fr["id"])
                    results.append(fr)
            # Re-sort by any score if available, then limit
            results = results[:limit]

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
            repo_tag = f" [{row['_repo']}]" if row.keys() and "_repo" in row.keys() else ""
            sig_short = f"  ({sig[:50]}...)" if sig and len(sig) > 50 else f"  ({sig})" if sig else ""
            typer.echo(f"  {name} [{kind}] ({lang}) {fpath}:{line}{repo_tag}{sig_short}")


# ============================================================================
# unresolved
# ============================================================================

@app.command()
def unresolved(
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """列出所有未解析的引用。

    大部分未解析引用是外部 SDK/库的符号（如 Android SDK、JDK、第三方库），
    这些符号不在项目源码里，无法解析是正常的。关注项目内部未能解析的引用即可。
    """
    resolved_db = os.path.abspath(db_path or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_unresolved, _rust_available
        if _rust_available():
            typer.echo(rust_unresolved(resolved_db, include_paths, exclude_paths))
            return

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
    root = os.path.abspath(project_path)

    # Try Rust acceleration
    from .rust_bridge import _rust_available
    if _rust_available():
        if action == "install":
            from .rust_bridge import rust_hooks_install
            typer.echo(rust_hooks_install(root))
            return
        elif action == "remove":
            from .rust_bridge import rust_hooks_remove
            typer.echo(rust_hooks_remove(root))
            return
        elif action == "status":
            from .rust_bridge import rust_hooks_status
            typer.echo(rust_hooks_status(root))
            return

    from .hooks import install_hooks, remove_hooks, status_hooks

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

# Analyzer registry for --run dispatch.
# Maps analyzer names to (class_attr, method, registration_info).
_ANALYZER_REGISTRY: dict[str, tuple[str, str, dict]] = {
    "entry-point": (
        "EntryPointDetector", "detect",
        {"file_patterns": ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.go", "**/*.java", "**/*.kt", "**/*.rs"],
         "node_kinds": ["function", "method"]},
    ),
    "dead-code": (
        "DeadCodeDetector", "detect",
        {"file_patterns": ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.go", "**/*.java", "**/*.kt", "**/*.rs"],
         "node_kinds": ["function", "method"]},
    ),
    "complexity": (
        "ComplexityAnalyzer", "analyze",
        {"file_patterns": ["**/*.py", "**/*.ts", "**/*.tsx"],
         "node_kinds": ["function", "method"]},
    ),
    "test-edges": (
        "TestEdgeAnalyzer", "analyze",
        {"file_patterns": ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.go", "**/*.java", "**/*.kt", "**/*.rs"],
         "node_kinds": ["function", "method", "class"]},
    ),
    "config-links": (
        "ConfigLinkAnalyzer", "analyze",
        {"file_patterns": ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.go", "**/*.java", "**/*.kt", "**/*.rs",
                           "**/*.yaml", "**/*.yml", "**/*.json", "**/*.toml", "**/*.properties"],
         "node_kinds": ["constant", "variable"]},
    ),
    "git-diff": (
        "GitDiffAnalyzer", "analyze",
        {"file_patterns": ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.go", "**/*.java", "**/*.kt", "**/*.rs"],
         "node_kinds": ["function", "method", "class"]},
    ),
}

# Tracking database path (relative to project root).
_ANALYSIS_TRACKER_DB = ".tws/codegraph/analysis_tracker.db"


@app.command()
def analyze(
    algorithm: str = typer.Option("all", help="Algorithm: clone/community/centrality/cycle/all"),
    edge_kinds: str = typer.Option("calls", help="Edge kinds to consider (comma-separated)"),
    threshold: float = typer.Option(0.8, help="Clone detection similarity threshold"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
    run_analyzer: Optional[str] = typer.Option(
        None, "--run",
        help="运行 P9 分析器: entry-point, dead-code, complexity, test-edges, config-links, git-diff, all",
    ),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """Run graph analysis algorithms on the indexed code graph.

    示例：
      tws-graph analyze --algorithm clone
      tws-graph analyze --algorithm community --edge-kinds calls,imports
      tws-graph analyze --algorithm all --json
      tws-graph analyze --run entry-point
      tws-graph analyze --run all
      tws-graph analyze --run dead-code --exclude "tests/"
    """
    # ------------------------------------------------------------------
    # P9 Analysis Suite path (--run)
    # ------------------------------------------------------------------
    if run_analyzer is not None:
        _run_p9_analyzer(run_analyzer, db_path, include_paths, exclude_paths)
        return

    # ------------------------------------------------------------------
    # Legacy algorithm path (--algorithm)
    # ------------------------------------------------------------------
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


def _run_p9_analyzer(analyzer_name: str, db_path: Optional[str],
                     include_paths=None, exclude_paths=None) -> None:
    """Dispatch --run requests to P9 analysis modules with invalidation tracking.

    Supports: entry-point, dead-code, complexity, test-edges, config-links,
    git-diff, and ``all`` (runs every registered analyzer).
    """
    import dataclasses
    import time

    resolved_db = os.path.abspath(db_path) if db_path else os.path.abspath(DEFAULT_DB)

    if not os.path.exists(resolved_db):
        typer.echo(
            f"错误: 索引数据库不存在 ({resolved_db})。"
            f"请先运行 tws-graph index。",
            err=True,
        )
        raise typer.Exit(1)

    # Determine which analyzers to run.
    if analyzer_name == "all":
        names = list(_ANALYZER_REGISTRY.keys())
    elif analyzer_name in _ANALYZER_REGISTRY:
        names = [analyzer_name]
    else:
        available = ", ".join(sorted(_ANALYZER_REGISTRY.keys()))
        typer.echo(
            f"未知分析器: {analyzer_name}。可用: {available}, all",
            err=True,
        )
        raise typer.Exit(1)

    store = _get_store(resolved_db)

    # --- Invalidation tracking ---
    tracker_db = os.path.join(os.path.dirname(resolved_db), "analysis_tracker.db")
    from tws_graph.analysis import InvalidationTracker, AnalyzerRegistration

    tracker = InvalidationTracker(tracker_db)
    stale_info: dict[str, list[str]] = {}
    try:
        # Register each selected analyzer.
        for name in names:
            _, _, reg_info = _ANALYZER_REGISTRY[name]
            tracker.register(AnalyzerRegistration(
                analyzer_name=name,
                file_patterns=list(reg_info["file_patterns"]),
                node_kinds=list(reg_info["node_kinds"]),
                version=1,
            ))

        # Check which files are stale.
        stale_info = tracker.check_invalidation(store)
    except Exception as exc:
        typer.echo(f"警告: InvalidationTracker 初始化失败: {exc}", err=True)

    # --- Run analyzers ---
    output: dict = {}
    try:
        for name in names:
            start = time.perf_counter()
            class_attr, method_name, reg_info = _ANALYZER_REGISTRY[name]

            module = __import__(
                f"tws_graph.analysis.{_ANALYZER_TO_MODULE[name]}",
                fromlist=[class_attr],
            )
            analyzer_cls = getattr(module, class_attr)
            analyzer_instance = analyzer_cls()

            # Special handling: dead-code needs entry points from entry-point.
            if name == "dead-code":
                from tws_graph.analysis import EntryPointDetector
                ep = EntryPointDetector()
                ep_results = ep.detect(store)
                entry_points = {r.node_id for r in ep_results}
                result = analyzer_instance.detect(store, entry_points=entry_points)
            # Special handling: config-links needs project_root.
            elif name == "config-links":
                project_root = os.path.dirname(resolved_db).replace("\\", "/")
                # Navigate up from .tws/codegraph to project root
                if project_root.endswith("/.tws/codegraph"):
                    project_root = project_root.rsplit("/.tws/codegraph", 1)[0]
                elif project_root.endswith("/.tws"):
                    project_root = project_root.rsplit("/.tws", 1)[0]
                # Default to cwd if path looks wrong
                if not os.path.isdir(project_root):
                    project_root = "."
                result = analyzer_instance.analyze(store, project_root=project_root)
            # Special handling: git-diff needs baseline_store for test mode.
            elif name == "git-diff":
                result = analyzer_instance.analyze(store, baseline="HEAD")
            else:
                method = getattr(analyzer_instance, method_name)
                result = method(store)

            elapsed_ms = (time.perf_counter() - start) * 1000

            # Serialize results (dataclasses -> dicts).
            if isinstance(result, list):
                serialized = [_dc_to_dict(r) for r in result]
            elif dataclasses.is_dataclass(result):
                serialized = _dc_to_dict(result)
            else:
                serialized = result

            stale_files = stale_info.get(name, [])

            output[name] = {
                "analyzer": name,
                "count": len(result) if isinstance(result, list) else 1,
                "duration_ms": round(elapsed_ms, 2),
                "stale_files": stale_files,
                "stale_file_count": len(stale_files),
                "result": serialized,
            }

            # Mark as valid after successful run.
            try:
                matched_files = tracker._match_patterns(
                    reg_info["file_patterns"],
                    set(store.get_file_stats().keys()),
                )
                tracker.mark_valid(name, sorted(matched_files))
            except Exception:
                pass  # Non-critical: tracking update failure should not block output.

    finally:
        store.close()
        tracker.close()

    typer.echo(json.dumps(output, ensure_ascii=False, indent=2))


# Map analyzer names to their module names (without package prefix).
_ANALYZER_TO_MODULE: dict[str, str] = {
    "entry-point": "entry_point",
    "dead-code": "dead_code",
    "complexity": "complexity",
    "test-edges": "test_edges",
    "config-links": "config_links",
    "git-diff": "git_diff",
}


def _dc_to_dict(obj) -> dict:
    """Convert a dataclass instance (including nested) to a plain dict."""
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_dc_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _dc_to_dict(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        import dataclasses
        return {f.name: _dc_to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    # Handle Enum values.
    if hasattr(obj, "value"):
        return obj.value
    return obj


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

    # Try Rust acceleration
    from .rust_bridge import rust_watch_start, _rust_available
    if _rust_available():
        typer.echo(rust_watch_start(resolved_db, root_dir, interval))
        return

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
    # Try Rust acceleration
    from .rust_bridge import rust_lsp_setup, _rust_available
    if _rust_available():
        typer.echo(rust_lsp_setup(os.path.abspath(DEFAULT_DB)))
        return

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


# ============================================================================
# serve command group — MCP server
# ============================================================================


@serve_app.callback(invoke_without_command=True)
def _serve_callback(
    ctx: typer.Context,
    root: str = typer.Option(
        None, "--root", "-r",
        help="项目根目录 (默认: 当前目录)",
    ),
    db: str = typer.Option(
        None, "--db", "-d",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
):
    """启动 MCP stdio 服务器，通过 stdin/stdout 与 MCP client 通信。

    示例：
      tws-graph serve                    # 开启 MCP 服务器，连接当前项目索引
      tws-graph serve --root /my/project # 指定项目根目录
      tws-graph serve --db /path/to/index.db # 指定数据库路径
    """
    if ctx.invoked_subcommand is not None:
        return

    # Determine database path
    if db:
        db_path = db
    elif root:
        db_path = os.path.join(root, ".tws", "codegraph", "index.db")
    else:
        db_path = DEFAULT_DB

    # Check index health
    if not os.path.exists(db_path):
        typer.echo(
            f"错误: 索引数据库不存在: {db_path}",
            err=True,
        )
        typer.echo(
            "请先运行 'tws-graph index' 构建代码符号关系图。",
            err=True,
        )
        raise typer.Exit(code=1)

    # Redirect stderr to /dev/null to avoid contaminating stdio with log output
    # The MCP protocol uses stdout for JSON-RPC messages; nothing else should
    # print to stdout/stderr.
    import sys as _sys
    try:
        _sys.stderr = open(os.devnull, "w")
    except Exception:
        pass

    try:
        from .mcp.server import run_server
        run_server(db_path)
    except FileNotFoundError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        pass


@serve_app.command(name="mcp-config")
def _mcp_config(
    root: str = typer.Option(
        None, "--root", "-r",
        help="项目根目录 (默认: 当前目录)",
    ),
    db: str = typer.Option(
        None, "--db", "-d",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
):
    """输出 Claude Code MCP 配置 JSON，可直接粘贴到 claude_desktop_config.json。

    示例：
      tws-graph serve mcp-config                    # 输出当前项目配置
      tws-graph serve mcp-config --root /my/project # 指定项目根目录
    """
    import json as _json

    # Determine database path
    if db:
        db_path = db
    elif root:
        db_path = os.path.join(os.path.abspath(root), ".tws", "codegraph", "index.db")
    else:
        db_path = os.path.abspath(DEFAULT_DB)

    # Reasonable executable path
    tws_graph_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "bin", "tws-graph")
    )
    # Fall back to module invocation
    if not os.path.exists(tws_graph_path):
        tws_graph_path = "tws-graph"

    config = {
        "mcpServers": {
            "tws-graph": {
                "command": tws_graph_path,
                "args": ["serve", "--db", db_path],
                "description": (
                    "tws-graph MCP server — zero-dependency, offline-capable "
                    "code symbol graph queries. 16 tools + 3 resources."
                ),
            }
        }
    }

    typer.echo(_json.dumps(config, indent=2, ensure_ascii=False))


# ============================================================================
# Architecture analysis commands (P30 v5.3.0)
# ============================================================================

@app.command()
def cycles(
    max_cycles: int = typer.Option(
        50, "--max", "-m",
        help="最大环数 (0=无限制)",
    ),
    db: str | None = typer.Option(
        None, "--db", "-d",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """检测调用图中的循环依赖。

    基于 DFS 三色标记法检测 calls 边形成的有向环。
    例：
      tws-graph cycles
      tws-graph cycles --max 10
      tws-graph cycles --exclude "tests/"
    """
    resolved_db = os.path.abspath(db or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_cycles, _rust_available
        if _rust_available():
            typer.echo(rust_cycles(resolved_db, include_paths, exclude_paths))
            return

    db_conn = _get_db(db) if os.path.exists(db or DEFAULT_DB) else None
    if not db_conn:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    from tws_graph.analysis.architecture import detect_cycles
    queries = QueryBuilder(db_conn.conn)
    result = detect_cycles(queries, max_cycles=max_cycles)

    if not result:
        typer.echo("(未检测到循环依赖)")
        return

    typer.echo(f"检测到 {len(result)} 个循环依赖:\n")
    for i, cycle in enumerate(result, 1):
        names = cycle.get("names", cycle.get("cycle", []))
        files = cycle.get("files", [])
        length = cycle.get("length", len(names) - 1)
        typer.echo(f"  #{i} 长度={length}, 文件数={len(files)}")
        typer.echo(f"      路径: {' → '.join(names)}")
        if files:
            typer.echo(f"      涉及文件: {', '.join(files[:5])}")
        typer.echo()


@app.command()
def layers(
    layer_def: str | None = typer.Option(
        None, "--layers", "-l",
        help="层次定义 JSON: '{\"ui\": {\"pattern\": \"src/ui/**\", \"level\": 1}, ...}'",
    ),
    db: str | None = typer.Option(
        None, "--db", "-d",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """检测架构层次违规。

    根据层定义文件或 --layers 参数，检测违反层次方向的调用。
    默认方向：高层级（小数字）可调用低层级（大数字），反之是违规。

    例：
      tws-graph layers --layers '{"ui": {"pattern": "src/ui/**", "level": 1}, "data": {"pattern": "src/data/**", "level": 3}}'
      tws-graph layers --exclude "tests/"
    """
    resolved_db = os.path.abspath(db or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_layers, _rust_available
        if _rust_available():
            typer.echo(rust_layers(resolved_db, include_paths, exclude_paths))
            return

    db_conn = _get_db(db) if os.path.exists(db or DEFAULT_DB) else None
    if not db_conn:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)
    import json as _json

    if layer_def:
        layers_dict = _json.loads(layer_def)
    else:
        typer.echo("提示: 使用 --layers 提供层次定义 JSON", err=True)
        typer.echo('示例: tws-graph layers --layers \'{"ui":{"pattern":"src/ui/**","level":1},"svc":{"pattern":"src/service/**","level":2}}\'')
        raise typer.Exit(1)

    from tws_graph.analysis.architecture import detect_layer_violations
    queries = QueryBuilder(db_conn.conn)
    violations = detect_layer_violations(queries, layers_dict)

    if not violations:
        typer.echo("(未检测到层次违规)")
        return

    typer.echo(f"检测到 {len(violations)} 个层次违规:\n")
    for i, v in enumerate(violations, 1):
        typer.echo(
            f"  #{i} [{v['source_layer']}(L{v['source_level']})] "
            f"{v['source_name']} ({v['source_file']})"
        )
        typer.echo(
            f"       → [{v['target_layer']}(L{v['target_level']})] "
            f"{v['target_name']} ({v['target_file']})"
        )
        typer.echo()


@app.command()
def metrics(
    module: str | None = typer.Option(
        None, "--module", "-m",
        help="只显示指定模块的度量 (file_path 片段匹配)",
    ),
    sort_by: str = typer.Option(
        "instability", "--sort", "-s",
        help="排序字段: instability, cohesion, internal_calls, external_calls, afferent_coupling, efferent_coupling",
    ),
    limit: int = typer.Option(
        30, "--limit", "-n",
        help="显示前 N 个结果",
    ),
    db: str | None = typer.Option(
        None, "--db", "-d",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """计算模块内聚/耦合/不稳定性度量。

    内聚 = internal_calls / total_calls
    不稳定性 = efferent_coupling / (afferent_coupling + efferent_coupling)

    例：
      tws-graph metrics
      tws-graph metrics --module src/tws_graph --sort cohesion -n 20
      tws-graph metrics --exclude "tests/"
    """
    resolved_db = os.path.abspath(db or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_metrics, _rust_available
        if _rust_available():
            typer.echo(rust_metrics(resolved_db, include_paths, exclude_paths))
            return

    db_conn = _get_db(db) if os.path.exists(db or DEFAULT_DB) else None
    if not db_conn:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    from tws_graph.analysis.architecture import compute_module_metrics
    queries = QueryBuilder(db_conn.conn)
    all_metrics = compute_module_metrics(queries)

    if not all_metrics:
        typer.echo("(无数据)")
        return

    # Filter by module name
    if module:
        all_metrics = [m for m in all_metrics if module in m["module"]]

    # Sort
    valid_sort_keys = {
        "instability", "cohesion", "internal_calls", "external_calls",
        "afferent_coupling", "efferent_coupling",
    }
    if sort_by in valid_sort_keys:
        all_metrics.sort(key=lambda m: m.get(sort_by, 0), reverse=True)

    # Display
    typer.echo(f"{'模块':<50} {'内聚':>6} {'不稳定':>8} {'传入':>5} {'传出':>5} {'内部调':>7} {'外部调':>7}")
    typer.echo("-" * 95)

    for m in all_metrics[:limit]:
        mod_display = m["module"]
        if len(mod_display) > 49:
            mod_display = "..." + mod_display[-46:]
        typer.echo(
            f"{mod_display:<50} "
            f"{m['cohesion']:>6.3f} "
            f"{m['instability']:>8.3f} "
            f"{m['afferent_coupling']:>5} "
            f"{m['efferent_coupling']:>5} "
            f"{m['internal_calls']:>7} "
            f"{m['external_calls']:>7}"
        )

    remaining = len(all_metrics) - limit
    if remaining > 0:
        typer.echo(f"\n(显示前 {limit} 个，共 {len(all_metrics)} 个模块，使用 -n 调整)")


# ============================================================================
# Taint analysis command (P31 v5.3.0)
# ============================================================================

@app.command()
def taint(
    max_depth: int = typer.Option(
        5, "--depth", "-d",
        help="BFS 最大搜索深度",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="JSON 格式输出",
    ),
    db: str | None = typer.Option(
        None, "--db",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """安全污点分析：追踪敏感数据从来源到危险操作的完整路径。

    通过 data_flows 边做 BFS，查找 source→sink 路径。
    Source: 环境变量、文件读取、用户输入、HTTP 请求体
    Sink: 命令执行、SQL、代码注入、文件写入、网络外泄

    例：
      tws-graph taint
      tws-graph taint --depth 10 --json
    """
    resolved_db = os.path.abspath(db or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_taint, _rust_available
        if _rust_available():
            typer.echo(rust_taint(resolved_db, include_paths, exclude_paths))
            return

    db_conn = _get_db(db) if os.path.exists(db or DEFAULT_DB) else None
    if not db_conn:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    from tws_graph.analysis.taint import find_taint_paths, find_sources, find_sinks
    import json as _json

    queries = QueryBuilder(db_conn.conn)

    sources = find_sources(queries)
    sinks = find_sinks(queries)
    paths = find_taint_paths(queries, max_depth=max_depth)

    if json_output:
        typer.echo(_json.dumps({
            "source_count": len(sources),
            "sink_count": len(sinks),
            "path_count": len(paths),
            "paths": paths,
        }, ensure_ascii=False, indent=2, default=str))
        return

    typer.echo(f"Sources: {len(sources)} | Sinks: {len(sinks)} | Paths found: {len(paths)}\n")

    if not paths:
        typer.echo("(未发现 source→sink 路径)")
        return

    for i, p in enumerate(paths[:20], 1):
        nodes = p["path"]
        typer.echo(f"  #{i} [{p['source_type']} → {p['sink_type']}] depth={p['depth']}")
        typer.echo(f"      source: {p['source']}")
        typer.echo(f"      sink:   {p['sink']}")
        typer.echo(f"      路径: {' → '.join(n.get('name', '?') for n in nodes)}")
        typer.echo()

    remaining = len(paths) - min(len(paths), 20)
    if remaining > 0:
        typer.echo(f"(显示前 20 条路径，共 {len(paths)} 条)")


# ============================================================================
# Export commands (P32 v5.3.0)
# ============================================================================

# Shared export options
_export_db_opt = typer.Option(None, "--db", "-d", help="数据库路径")
_export_depth_opt = typer.Option(5, "--depth", help="BFS 导出深度 (用 --from 指定起始节点时)")
_export_kind_opt = typer.Option(None, "--kind", "-k", help="只导出指定边类型 (如 calls, data_flows)")
_export_from_opt = typer.Option(None, "--from", "-f", help="起始节点 ID，导出以该节点为中心的子图")
_export_limit_opt = typer.Option(500, "--limit", "-l", help="最大导出边数")

# Create a parent app for export subcommands
export_app = typer.Typer(name="export", help="导出图到标准格式")


@export_app.command("dot")
def export_dot_cmd(
    db: str | None = _export_db_opt,
    depth: int = _export_depth_opt,
    kind: str | None = _export_kind_opt,
    from_node: str | None = _export_from_opt,
    limit: int = _export_limit_opt,
    output: str | None = typer.Option(None, "--output", "-o", help="输出文件路径 (默认: stdout)"),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """导出为 Graphviz DOT 格式。

    例：
      tws-graph export dot --kind calls --depth 3 > graph.dot
      tws-graph export dot --from NODE_ID --depth 2 -o subgraph.dot
      tws-graph export dot --exclude "tests/"
    """
    _run_export("dot", db, depth, kind, from_node, limit, output, include_paths, exclude_paths)


@export_app.command("mermaid")
def export_mermaid_cmd(
    db: str | None = _export_db_opt,
    depth: int = _export_depth_opt,
    kind: str | None = _export_kind_opt,
    from_node: str | None = _export_from_opt,
    limit: int = _export_limit_opt,
    output: str | None = typer.Option(None, "--output", "-o", help="输出文件路径 (默认: stdout)"),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """导出为 Mermaid 格式（可嵌入 Markdown）。

    例：
      tws-graph export mermaid --kind imports > deps.md
      tws-graph export mermaid --from NODE_ID -o arch.mermaid
    """
    _run_export("mermaid", db, depth, kind, from_node, limit, output, include_paths, exclude_paths)


@export_app.command("json")
def export_json_cmd(
    db: str | None = _export_db_opt,
    depth: int = _export_depth_opt,
    kind: str | None = _export_kind_opt,
    from_node: str | None = _export_from_opt,
    limit: int = _export_limit_opt,
    output: str | None = typer.Option(None, "--output", "-o", help="输出文件路径 (默认: stdout)"),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """导出为 JSON 格式。

    例：
      tws-graph export json --kind calls > calls.json
      tws-graph export json -o graph.json
    """
    _run_export("json", db, depth, kind, from_node, limit, output, include_paths, exclude_paths)


def _run_export(fmt: str, db, depth, kind, from_node, limit, output,
                include_paths=None, exclude_paths=None):
    """Common export logic."""
    resolved_db = os.path.abspath(db or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import _rust_available
        if _rust_available():
            result = None
            if fmt == "dot":
                from .rust_bridge import rust_export_dot
                result = rust_export_dot(resolved_db, from_node or "", depth, kind,
                                        include_paths, exclude_paths)
            elif fmt == "mermaid":
                from .rust_bridge import rust_export_mermaid
                result = rust_export_mermaid(resolved_db, from_node or "", depth, kind,
                                            include_paths, exclude_paths)
            elif fmt == "json":
                from .rust_bridge import rust_export_json
                result = rust_export_json(resolved_db, kind, limit,
                                         include_paths, exclude_paths)
            if result is not None:
                if output:
                    with open(output, "w", encoding="utf-8") as f:
                        f.write(result)
                    typer.echo(f"已写入: {output}")
                else:
                    typer.echo(result)
                return

    db_conn = _get_db(db) if os.path.exists(db or DEFAULT_DB) else None
    if not db_conn:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db_conn.conn)
    import json as _json

    if fmt == "dot":
        from tws_graph.export import export_dot
        result = export_dot(queries, from_node=from_node, depth=depth, kind=kind, limit=limit)
    elif fmt == "mermaid":
        from tws_graph.export import export_mermaid
        result = export_mermaid(queries, from_node=from_node, depth=depth, kind=kind, limit=limit)
    elif fmt == "json":
        from tws_graph.export import export_json
        data = export_json(queries, from_node=from_node, depth=depth, kind=kind, limit=limit)
        result = _json.dumps(data, ensure_ascii=False, indent=2)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(result)
        typer.echo(f"已写入: {output}")
    else:
        typer.echo(result)


# Register export subcommands
app.add_typer(export_app)


# ============================================================================
# GQL query command (P38 v5.5.0)
# ============================================================================

@app.command()
def query(
    gql_query: str = typer.Argument(..., help="GQL 查询语句"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db: str | None = typer.Option(
        None, "--db",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """图查询语言 (GQL) —— 人性化的代码图查询。

    无需 SQL，用自然语言风格查询代码图。

    例：
      tws-graph query "FIND function WHERE name MATCHES 'auth'"
      tws-graph query "FIND class WHERE file_path MATCHES 'src/api/' LIMIT 10"
      tws-graph query "FIND * WHERE lang = 'python' RETURN name, file_path"
      tws-graph query "IMPACT OF MyClass.my_method"
    """
    from .gql import parse_gql, execute_gql

    resolved_db = os.path.abspath(db or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_gql_query, _rust_available
        if _rust_available():
            typer.echo(rust_gql_query(resolved_db, gql_query, include_paths, exclude_paths))
            return

    db_conn = _get_db(db) if os.path.exists(db or DEFAULT_DB) else None
    if db_conn is None:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db_conn.conn)

    try:
        result = execute_gql(queries, gql_query)
    except ValueError as e:
        typer.echo(f"GQL 语法错误: {e}", err=True)
        raise typer.Exit(1)

    if not result:
        typer.echo("(无结果)")
        return

    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for row in result:
            name = row.get("name", row.get("qualified_name", ""))
            fpath = row.get("file_path", "")
            kind = row.get("kind", "")
            line = row.get("start_line", "")
            typer.echo(f"{kind:<12} {name:<40} {fpath}:{line}")


# ============================================================================
# Impact prediction command (P40 v5.5.0)
# ============================================================================

@app.command()
def predict_impact(
    symbol: str = typer.Argument(..., help="要分析的符号（名称或 qualified_name）"),
    depth: int = typer.Option(3, "--depth", "-d", help="最大传播深度"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db: str | None = typer.Option(
        None, "--db",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """预测修改某符号的影响范围与风险。

    综合影响分析 + 测试覆盖 + 风险评分，帮助评估重构风险。

    例：
      tws-graph predict-impact MyClass.my_method
      tws-graph predict-impact auth_login --depth 5 --json
    """
    resolved_db = os.path.abspath(db or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_predict_impact, _rust_available
        if _rust_available():
            typer.echo(rust_predict_impact(resolved_db, symbol, include_paths, exclude_paths))
            return

    from .analysis.impact_prediction import predict_impact as do_predict

    db_conn = _get_db(db) if os.path.exists(db or DEFAULT_DB) else None
    if db_conn is None:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db_conn.conn)

    # Search for the symbol if not a node ID
    symbol_id = symbol
    if len(symbol_id) != 32:  # Not a hash → search by name
        results = queries._exec(
            "SELECT id FROM nodes WHERE name = ? OR qualified_name LIKE ? LIMIT 1",
            (symbol, f"%{symbol}%")
        ).fetchall()
        if not results:
            typer.echo(f"未找到符号: {symbol}", err=True)
            raise typer.Exit(1)
        symbol_id = results[0]["id"]

    result = do_predict(queries, symbol_id, depth=depth)

    if result["total_affected"] == 0:
        typer.echo(f"未找到受 {symbol} 影响的节点。")
        return

    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        sym = result["symbol"] or {}
        typer.echo(f"\n{'='*60}")
        typer.echo(f"  影响预测: {sym.get('name', symbol)}")
        typer.echo(f"  文件: {sym.get('file_path', '?')}:{sym.get('start_line', '?')}")
        typer.echo(f"  风险评分: {result['risk_score']}/100")
        typer.echo(f"{'='*60}")
        typer.echo(f"\n  直接影响 ({result['direct_count']} 个):")
        for d in result["direct_dependents"][:10]:
            typer.echo(f"    - {d['name']} ({d['file_path']}:{d.get('start_line','')})")
        if result["direct_count"] > 10:
            typer.echo(f"    ... 还有 {result['direct_count'] - 10} 个")
        typer.echo(f"\n  间接影响 ({result['indirect_count']} 个):")
        for d in result["indirect_dependents"][:5]:
            typer.echo(f"    - {d['name']} ({d['file_path']}:{d.get('start_line','')})")
        if result["indirect_count"] > 5:
            typer.echo(f"    ... 还有 {result['indirect_count'] - 5} 个")
        typer.echo(f"\n  受影响文件 ({len(result['affected_files'])} 个):")
        for f in result["affected_files"][:5]:
            typer.echo(f"    - {f}")
        if len(result['affected_files']) > 5:
            typer.echo(f"    ... 还有 {len(result['affected_files']) - 5} 个")
        typer.echo(f"\n  建议回归测试 ({len(result['affected_tests'])} 个):")
        for t in result["affected_tests"][:5]:
            typer.echo(f"    - {t['name']} ({t['file_path']})")
        if len(result['affected_tests']) > 5:
            typer.echo(f"    ... 还有 {len(result['affected_tests']) - 5} 个")
        typer.echo()


# ============================================================================
# Code health command (P41 v5.5.0)
# ============================================================================

# ============================================================================
# federate
# ============================================================================

@app.command()
def federate(
    action: str = typer.Argument(..., help="add | remove | list"),
    path: Optional[str] = typer.Argument(None, help="仓库路径 (add) 或仓库名 (remove)"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="联邦仓库的逻辑名称"),
    db_path: Optional[str] = typer.Option(None, "--db", help="索引数据库路径"),
):
    """管理多仓库联邦。

    将多个已索引的仓库加入联邦，使 search/calls/impact/trace 等命令
    能够跨仓库查询。

    示例：
      tws-graph federate add /path/to/other-repo --name repo-b
      tws-graph federate remove repo-b
      tws-graph federate list
    """
    from .federation import FederationRegistry, load_federation

    # Determine project root from db_path or cwd
    if db_path:
        resolved_db = os.path.abspath(db_path)
        # Navigate from .tws/codegraph/index.db to project root
        codegraph_dir = os.path.dirname(resolved_db)
    else:
        codegraph_dir = os.path.abspath(DEFAULT_DB).replace("\\", "/")
        # DEFAULT_DB is relative; resolve from cwd
        cwd = os.getcwd()
        codegraph_dir = os.path.join(cwd, ".tws", "codegraph")

    # Ensure codegraph_dir exists
    os.makedirs(codegraph_dir, exist_ok=True)

    registry = FederationRegistry(codegraph_dir)

    if action == "add":
        if not path:
            typer.echo("错误: 'add' 需要提供仓库路径。用法: tws-graph federate add <path> [--name <name>]", err=True)
            raise typer.Exit(1)
        repo_path = os.path.abspath(path)
        repo_name = name or os.path.basename(repo_path)
        try:
            info = registry.add(repo_name, repo_path)
            typer.echo(f"已添加联邦仓库: '{info.name}' -> {info.path}")
            typer.echo(f"  索引库: {info.db}")
        except FileNotFoundError as e:
            typer.echo(f"错误: {e}", err=True)
            raise typer.Exit(1)
        except ValueError as e:
            typer.echo(f"错误: {e}", err=True)
            raise typer.Exit(1)

    elif action == "remove":
        if not path:
            typer.echo("错误: 'remove' 需要提供仓库名。用法: tws-graph federate remove <name>", err=True)
            raise typer.Exit(1)
        try:
            registry.remove(path)
            typer.echo(f"已移除联邦仓库: '{path}'")
        except KeyError as e:
            typer.echo(f"错误: {e}", err=True)
            raise typer.Exit(1)

    elif action == "list":
        repos = registry.list_all()
        if not repos:
            typer.echo("(无联邦仓库)")
        else:
            typer.echo(f"联邦仓库 ({len(repos)} 个):")
            for rname, rinfo in repos.items():
                db_exists = "OK" if os.path.isfile(rinfo["db"]) else "MISSING"
                typer.echo(f"  {rname}: {rinfo['path']}  [{db_exists}]")

    else:
        typer.echo(f"未知动作: {action}。可用: add, remove, list", err=True)
        raise typer.Exit(1)


def _cross_repo_search_federated(
    query_str: str,
    limit: int,
    queries,
    federation,
    current_db_path: str,
) -> list[dict]:
    """Search across federated databases using SQLite ATTACH + UNION.

    Args:
        query_str: The raw query string (may contain field:value qualifiers).
        limit: Maximum results.
        queries: QueryBuilder for the local database (used for parsing qualifiers).
        federation: FederationRegistry instance.
        current_db_path: Path to the local index.db (for the 'local' alias).

    Returns:
        List of result rows with an extra ``_repo`` key indicating source.
    """
    import sqlite3 as _sqlite3

    # Build a temporary in-memory connection and ATTACH all DBs
    mem = _sqlite3.connect(":memory:")
    mem.row_factory = _sqlite3.Row

    fed_aliases: list[str] = []
    local_attached = False

    try:
        # ATTACH local DB
        if os.path.isfile(current_db_path):
            mem.execute(f"ATTACH DATABASE '{current_db_path}' AS local")
            local_attached = True

        # ATTACH federated DBs
        fed_aliases = federation.attach_all(mem, alias_prefix="fed_")

        if not fed_aliases:
            return []

        # Build UNION ALL query
        from .store.query_builder import _parse_field_qualifiers
        from .store.query_builder import QueryBuilder as QB

        qualifiers = _parse_field_qualifiers(query_str)
        text_terms = qualifiers["text"]
        filters = qualifiers["filters"]

        # Build FTS5 match expression
        fts_text = " ".join(text_terms)
        fts_match = QB._build_fts_query(fts_text) if fts_text else "*"

        # Build WHERE clause for field filters
        where_parts: list[str] = []
        for field, value in filters.items():
            if field == "kind":
                # Escape single quotes in value
                safe_val = value.replace("'", "''")
                where_parts.append(f"n.kind = '{safe_val}'")
            elif field in ("lang", "language"):
                safe_val = value.replace("'", "''")
                where_parts.append(f"n.language = '{safe_val}'")
            elif field == "path":
                safe_val = value.replace("'", "''")
                where_parts.append(f"n.file_path LIKE '%{safe_val}%'")
        where_clause = (" AND " + " AND ".join(where_parts)) if where_parts else ""

        # Build UNION ALL across all DBs.
        # Each subquery uses a derived table to allow per-DB ORDER BY + LIMIT,
        # then the outer query UNIONs and applies a final LIMIT.
        union_parts: list[str] = []
        inner_template = (
            "SELECT * FROM ("
            "SELECT n.*, '{repo}' AS _repo "
            "FROM {schema}.nodes_fts AS f "
            "JOIN {schema}.nodes AS n ON f.rowid = n.rowid "
            "WHERE nodes_fts MATCH '{match}'{where} "
            "ORDER BY rank "
            "LIMIT {lim}"
            ")"
        )
        # Local DB
        if local_attached and os.path.isfile(current_db_path):
            union_parts.append(inner_template.format(
                repo="local", schema="local", match=fts_match,
                where=where_clause, lim=limit,
            ))
        # Federated DBs
        for alias in fed_aliases:
            union_parts.append(inner_template.format(
                repo=alias, schema=alias, match=fts_match,
                where=where_clause, lim=limit,
            ))

        union_sql = " UNION ALL ".join(union_parts) + f" LIMIT {limit}"
        rows = mem.execute(union_sql).fetchall()
        return [dict(r) for r in rows]

    except Exception:
        return []
    finally:
        federation.detach_all(mem, fed_aliases)
        if local_attached:
            try:
                mem.execute("DETACH DATABASE local")
            except Exception:
                pass
        mem.close()


@app.command()
def health(
    top: int = typer.Option(0, "--top", help="只显示前 N 个最健康的文件"),
    worst: int = typer.Option(0, "--worst", help="只显示后 N 个最不健康的文件"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出"),
    db: str | None = typer.Option(
        None, "--db",
        help="数据库路径 (默认: .tws/codegraph/index.db)",
    ),
    include_paths: Optional[list[str]] = typer.Option(None, "--include", "-I", help="Include files matching glob pattern (repeatable)"),
    exclude_paths: Optional[list[str]] = typer.Option(None, "--exclude", "-X", help="Exclude files matching glob pattern (repeatable)"),
):
    """代码健康度评分 —— 综合测试覆盖、死代码、耦合度评估每个文件。

    评分维度:
      测试覆盖 (40%) + 活代码率 (25%) + 耦合度 (20%) + 文件规模 (15%)

    例：
      tws-graph health
      tws-graph health --worst 10
      tws-graph health --worst 10 --exclude "tests/"
      tws-graph health --top 5 --json
    """
    resolved_db = os.path.abspath(db or DEFAULT_DB)

    # Try Rust acceleration
    if os.path.exists(resolved_db):
        from .rust_bridge import rust_health, _rust_available
        if _rust_available():
            typer.echo(rust_health(resolved_db, worst if worst > 0 else 10, include_paths, exclude_paths))
            return

    from .analysis.code_health import compute_health_scores

    db_conn = _get_db(db) if os.path.exists(db or DEFAULT_DB) else None
    if db_conn is None:
        typer.echo("错误: 索引数据库不存在。请先运行 tws-graph index。", err=True)
        raise typer.Exit(1)

    queries = QueryBuilder(db_conn.conn)
    scores = compute_health_scores(queries)

    if not scores:
        typer.echo("(无生产文件数据)")
        return

    if worst > 0:
        scores = scores[-worst:]
    elif top > 0:
        scores = scores[:top]

    if json_output:
        typer.echo(json.dumps(scores, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(f"\n{'文件':<50} {'评分':>5} {'函数':>5} {'已测':>5} {'死码':>5} {'覆盖%':>7} {'外部依赖':>8}")
        typer.echo("-" * 95)
        for s in scores:
            typer.echo(
                f"{s['file_path']:<50} {s['score']:>5} {s['func_count']:>5} "
                f"{s['tested_count']:>5} {s['dead_count']:>5} {s['coverage_pct']:>6.1f}% "
                f"{s['external_deps']:>8}"
            )


def main():
    app()


if __name__ == "__main__":
    main()
