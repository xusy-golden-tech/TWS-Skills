# KNOWN_ISSUES.md

目标达成后统一处理的非阻塞问题。

---

## B2 Benchmark 基础设施阶段发现

### 1. `tws-graph search --semantic --db <path>` 崩溃
- 发现阶段：B2 Benchmark 基础设施搭建
- 现象：`tws-graph search --semantic --db <custom_path>` 在 cli.py:596 崩溃，`_get_db` 未定义
- 不阻塞原因：Benchmark 不使用 `--semantic`，仅影响语义搜索 + 自定义 db 的组合场景

### 2. `tws-graph analyze --run dead-code --include` 不生效
- 发现阶段：B2 Benchmark 基础设施搭建
- 现象：`tws-graph analyze --run dead-code --include "engine/coding_task_service.py"` 返回全量结果，`--include` 参数被忽略
- 不阻塞原因：可以用全项目 dead-code 替代单文件 dead-code，不影响 benchmark 核心目标

### 3. g-ass-source 跨文件调用关系稀疏
- 发现阶段：B2 Benchmark 基础设施搭建
- 现象：`tws-graph calls CodingTaskService --inbound` 仅返回 file 级 caller，无 function 级调用者；trace 找不到路径
- 不阻塞原因：可能需要运行 `tws-graph resolve` 建立跨文件引用，或者 g-ass-source 项目本身就以间接调用为主（event-driven/DI pattern）
- 建议：B2 正式 benchmark 前先运行 `tws-graph resolve --db D:/g-ass-source/.tws/codegraph/index.db`
