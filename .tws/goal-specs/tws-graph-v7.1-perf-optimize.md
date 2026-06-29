# Goal: tws-graph v7.1.0 性能优化 — 缩小与 CBM 的差距

## 元信息
- 创建时间：2026-06-29
- 关联 flow：flow-refactor
- 关联 session：.tws/sessions/refactor-tws-graph-rust-v7.1.md
- 关联分支：refactor/tws-graph-rust-v7.1
- 目标状态：✅ 已完成
- 前置版本：v7.0.0

## 总体目标

v7.1.0 目标：性能优化 + Bug 修复。

## 执行结果

### Phase 1: Bug 修复 ✅
- 修复 3 文件 6 处字节切片 Unicode panic (markdown.rs, css.rs, sql.rs)
- Commit: b2e2aa3

### Phase 2: DB 层优化 ✅
- 批量事务 (每 100 文件一组) + Prepared Statements + Parser Pool
- Commit: 731b2f5

### Phase 3: rayon 并行提取 ✅
- par_chunks 文件级并行，TWS_USE_PARALLEL=0 串行回退
- 新建 parallel.rs (152 行)，+7 新测试
- Commit: ea1b00b

### Phase 4a: xxhash ✅
- XXH3-64 替代 SHA256 做节点/边 ID (16 hex chars)
- body_hash 保留 SHA256
- Commit: 2f1f026

### Phase 4b: TreeCursor ✅
- Python, Rust, TypeScript, Java, Go 5 个提取器 top-level body walking 转换
- Commit: 7de7648

### Phase 5: 收尾 ✅
- 版本号 → 7.1.0 (pyproject.toml, __init__.py)
- 1032 Rust tests pass (+7 new)
- 2816 Python tests pass
- Commit: 0d3f104

## 测试结果
- Rust: **1032 passed** (1025 existing + 7 new parallel tests)
- Python: **2816 passed, 45 skipped**
- tws-graph lint: 33 errors + 138 warnings (全部预先存在，非本次引入)

## 功能退化检查
- 所有阶段 snapshot diff 验证通过
- 0 removed, 0 changed, 仅新增代码符号
- 串行 vs 并行输出 100% 一致

## Goal 验证
- BLOCKER: 0
- WARNING: 1 (Cargo.toml 内部 Rust crate 版本保持 0.1.0，与对外 tws-graph 7.1.0 独立，合理设计)
- 总体: ✅ 通过

## 执行日志
- 2026-06-29 — 🎯 目标接收
- 2026-06-29 — ✅ 阶段 1 完成：Bug 修复
- 2026-06-29 — ✅ 阶段 2 完成：DB 层优化
- 2026-06-29 — ✅ 阶段 3 完成：rayon 并行化
- 2026-06-29 — ✅ 阶段 4a 完成：xxhash
- 2026-06-29 — ✅ 阶段 4b 完成：TreeCursor
- 2026-06-29 — ✅ 阶段 5 完成：版本号 + 测试验证
- 2026-06-29 — ✅ Goal 回溯验证通过
- 2026-06-29 — 🎯 目标达成
