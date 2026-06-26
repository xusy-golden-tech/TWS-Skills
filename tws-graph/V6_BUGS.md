# tws-graph v6.0.0 非阻塞 Bug 记录

> 阻塞级 bug 直接追加到 V6_SPEC.md 并用 TDD 方式解决。
> 此文件记录非阻塞级 bug，待合适时机修复。

---

## BUG-001: search 命令 --db 参数与 or 运算符优先级
- **文件**: `src/tws_graph/cli.py:1245`
- **代码**: `os.path.exists(db_path or DEFAULT_DB)`
- **问题**: `or` 优先级导致当 `db_path` 为 truthy 时可能跳过 DEFAULT_DB 回退
- **影响**: 指定 --db 参数时 search 可能报错
- **发现时间**: 2026-06-26
- **优先级**: 低（workaround: 不指定 --db，在项目根目录运行）
