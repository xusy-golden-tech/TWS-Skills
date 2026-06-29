## 当前流程
- 流程名：tws-graph v7.1.0 性能优化
- flow skill：flow-refactor
- 开始时间：待开始
- 前置版本：v7.0.0（refactor/tws-graph-rust-v7）

## 进度总览
- [ ] Phase 1: Bug 修复（markdown Unicode panic）
- [ ] Phase 2: DB 层优化（批量事务 + prepared stmts + parser pool）
- [ ] Phase 3: rayon 并行提取
- [ ] Phase 4: 算法级优化（xxhash + TreeCursor）
- [ ] Phase 5: 收尾 + 版本号 → 7.1.0

## 关键文件
- SPEC: .tws/goal-specs/tws-graph-v7.1-perf-optimize.md
- 本文件: .tws/sessions/refactor-tws-graph-rust-v7.1.md
