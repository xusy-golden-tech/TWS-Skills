# KNOWN_ISSUES.md

目标达成后统一处理的非阻塞问题。

---

## 已解决（2026-07-02）

### 1. `tws-graph search --semantic --db <path>` 崩溃 ✅ 已修复
- 发现阶段：B2 Benchmark 基础设施搭建
- 现象：`tws-graph search --semantic --db <custom_path>` 在 cli.py:596 崩溃，`_get_db` 未定义
- 根因：v7.x 重构遗漏 — `_get_db` 函数未定义、`QueryBuilder` 和 `semantic_query` 未导入、`SqliteStore.conn` 属性缺失、`_serialize` 函数未定义
- 修复：添加 `_get_db()` 函数、补充顶层 import、添加 `SqliteStore.conn` 属性、添加 `_serialize()` 序列化函数
- 提交：fix/known-issues-md-bugs

### 2. `tws-graph analyze --run dead-code --include` 不生效 ✅ 已修复
- 发现阶段：B2 Benchmark 基础设施搭建
- 现象：`tws-graph analyze --run dead-code --include "xxx.py"` 返回全量结果，`--include` 参数被忽略
- 根因：`_run_p9_analyzer` 接收了 `include_paths` / `exclude_paths` 参数但从未使用
- 修复：在分析结果序列化前增加 `_filter_results_by_path()` 按 `file_path` 字段过滤
- 提交：fix/known-issues-md-bugs

### 3. g-ass-source 跨文件调用关系稀疏 ✅ 已验证
- 发现阶段：B2 Benchmark 基础设施搭建
- 验证结果：`tws-graph resolve` 正常工作。在 g-ass-source 上运行后解析成功 51,989 个跨文件引用
- 解决方法：运行 `tws-graph resolve --db D:/g-ass-source/.tws/codegraph/index.db` 即可建立跨文件引用
