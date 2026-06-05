# 设计书：TWS Code Graph 引擎

## 背景

TWS 当前在代码影响分析和设计书同步两个环节，依赖 agent 自己读文件、追引用链来完成。这跟 CodeGraph benchmark 里"不用 CodeGraph"那条线一样——agent 靠 grep + read 探索代码，然后自己推理。

**问题：**
- 影响分析靠 agent 推理，可能漏、可能偏
- 设计书同步靠 agent 自觉逐行比对，没有可验证的手段
- 项目结构索引（project-map.md）靠人工生成，代码变了就过时
- 根因分析时 agent 要在几千个文件里找调用链路，token 消耗大

CodeGraph 已经证明了另一个路线：**预建代码符号关系图 → agent 查图而非搜索**。平均减少 47% token、58% 工具调用。

TWS 不做外部 MCP 依赖，自己做闭环。

## 涉及模块

- **新增**：`tws-graph` Python 包（独立仓库，pip 安装）
- **修改**：TWS 四个技能文件（impact-assessment、design-sync、root-cause-analysis、flow-new-project）
- **不变**：TWS 其他技能、规约体系、流程架构

## 目标

```
TWS agent 做影响分析时：
  改前 → tws-graph impact <函数名> → 「这个函数被 6 个模块调用，涉及 3 条 API 路由」
  改后 → tws-graph diff snap-before snap-after → 「以下符号发生了变化，对应设计书需要更新」

不再是「我读了这些文件，觉得应该影响这些地方」
而是「图告诉我的，以下是多少调用者、什么改了」
```

## 架构总览

```
                      TWS 技能层（微调，不改核心逻辑）
┌─────────────────────────────────────────────────────────┐
│                                                         │
│  comp-impact-assessment   →  改前：tws-graph impact     │
│  comp-design-sync         →  改后：tws-graph diff       │
│  comp-root-cause-analysis →  回溯：tws-graph trace      │
│  flow-new-project         →  init：tws-graph index      │
│                                                         │
└──────────────────────┬──────────────────────────────────┘
                       │ CLI 子命令
┌──────────────────────▼──────────────────────────────────┐
│                                                         │
│                   tws-graph CLI                         │
│                                                         │
│  index     tree-sitter 解析 → SQLite 图                 │
│  callers   谁调了这个符号                                │
│  callees   这个符号调了谁                                │
│  impact    影响半径（从符号出发 N 层展开）               │
│  trace     两端之间的调用路径                            │
│  diff      两次索引快照对比 → 输出差异报告               │
│  watch     文件监听 → 自动增量索引                       │
│  map       自动生成/更新 project-map.md                  │
│                                                         │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                                                         │
│  索引层                                                 │
│                                                         │
│  tree-sitter (Python binding)                           │
│  ├─ tree-sitter-python   → Python 源码                 │
│  ├─ tree-sitter-typescript → TS/TSX 源码               │
│  └─ tree-sitter-java     → Java 源码                   │
│                                                         │
│  SQLite (Python 内置 sqlite3)                           │
│  ├─ nodes:  符号表（函数/类/接口/路由/...）             │
│  ├─ edges:  关系表（calls/imports/extends/...）         │
│  └─ files:  文件索引（路径/哈希/解析时间）              │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 技术选型

| 决策 | 选择 | 理由 |
|------|------|------|
| 语言 | Python | 与用户技术栈一致（G-Ass 后端、YOLO、ASR/TTS） |
| 语法解析 | tree-sitter + Python binding | 确定性的 AST 解析，不依赖 LLM |
| 存储 | SQLite（Python 内置 sqlite3） | 零配置、单文件、够快 |
| 部署 | pip 包 + CLI 入口 | `pip install tws-graph` 一条命令 |
| 索引粒度 | 函数/类/方法/接口 + 路由映射 | 够用，不追求 CodeGraph 的 20+ 语言全量覆盖 |

### 与 CodeGraph 的差异

| | CodeGraph | TWS Graph |
|---|---|---|
| 定位 | 通用代码探索引擎 | **影响分析 + 设计验证** |
| 语言 | 20+ 种 | Python / TypeScript / Java |
| 框架识别 | 14 个 web 框架路由 | FastAPI / Express / Spring 路由映射 |
| 代理接口 | MCP 协议（外部服务） | **CLI 子命令**（TWS 技能直接调） |
| 独有能力 | — | **快照 diff**（改前改后对比） |
| 独有能力 | — | **project-map 自动生成** |

## 核心模块设计

### 1. Indexer（索引器）

```
输入：项目根目录
处理：
  1. 遍历 .py / .ts / .tsx / .java 文件
  2. tree-sitter 解析 AST
  3. 提取符号节点：function_definition, class_definition, method_definition,
     interface_declaration, variable_declaration, route_decorator 等
  4. 提取关系边：函数调用、类继承、接口实现、导入引用
  5. 框架路由识别（FastAPI @app.get、Express app.get、Spring @GetMapping）
  6. 写入 SQLite

输出：.tws/codegraph/index.db
```

### 2. Graph DB Schema

```sql
-- 节点表（符号）
CREATE TABLE nodes (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,          -- 符号名
    kind        TEXT NOT NULL,          -- function/class/method/interface/route/...
    file_path   TEXT NOT NULL,          -- 相对于项目根目录
    line_start  INTEGER,
    line_end    INTEGER,
    signature   TEXT,                   -- 函数签名（如果有）
    docstring   TEXT,                   -- docstring 摘要（如果有）
    framework   TEXT                    -- fastapi/express/spring（如果是路由）
);

-- 边表（关系）
CREATE TABLE edges (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES nodes(id),
    target_id   INTEGER NOT NULL REFERENCES nodes(id),
    kind        TEXT NOT NULL,          -- calls/imports/extends/implements/references
    source_loc  TEXT                    -- 调用发生的位置（文件:行号）
);

-- 文件表
CREATE TABLE files (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    hash        TEXT NOT NULL,
    indexed_at  INTEGER NOT NULL
);

-- 索引
CREATE INDEX idx_nodes_name ON nodes(name);
CREATE INDEX idx_nodes_kind ON nodes(kind);
CREATE INDEX idx_nodes_file ON nodes(file_path);
CREATE INDEX idx_edges_source ON edges(source_id);
CREATE INDEX idx_edges_target ON edges(target_id);
```

### 3. Query Engine（查询引擎）

```
tws-graph callers <symbol>
  → SELECT n.* FROM nodes n JOIN edges e ON n.id = e.source_id
    WHERE e.target_id = (SELECT id FROM nodes WHERE name = '<symbol>')
  → 输出：函数名、所在文件、调用位置

tws-graph callees <symbol>
  → 反向：symbol 调用了哪些符号
  → 输出：被调用者列表

tws-graph impact <symbol> --depth 3
  → BFS/DFS 从 symbol 出发，沿 callers 方向展开 N 层
  → 合并去重，按模块分组输出
  → 如果涉及路由节点，标注对应的 API 端点

tws-graph trace <from_symbol> <to_symbol>
  → BFS 最短路径，找到 from 到 to 的调用链
  → 输出：每一步的调用关系

tws-graph diff <snap_before> <snap_after>
  → 对比两个快照的差异
  → 输出：
    新增符号：{列表}
    删除符号：{列表}
    签名变更：{列表（旧签名 → 新签名）}
    新增调用关系：{列表}
    断开的调用关系：{列表}
```

### 4. Snapshot（快照机制）

```
改前：tws-graph index → 自动保存为 .tws/codegraph/snap-before/
改后：tws-graph index → 自动保存为 .tws/codegraph/snap-after/
对比：tws-graph diff snap-before snap-after

用途：
  - impact-assessment：改前拍快照作为 baseline
  - design-sync：改后再拍，diff 出变化 → 对照设计书
```

### 5. File Watcher（自动同步）

```
tws-graph watch
  → 监听项目文件变更（利用操作系统原生事件）
  → 文件保存 → 增量重新解析 → 更新 SQLite 中的对应记录
  → 无需手动重新索引

可选：git hook 触发
  → post-commit / post-merge 自动 tws-graph index
```

### 6. Project Map 自动生成

```
tws-graph map
  → 从 SQLite 图中提取高层结构
  → 按目录分组、按框架识别入口
  → 生成 .tws/project-map.md（替代手工编写）

输出格式不变，兼容现有 TWS 规约
```

## TWS 技能改动

### comp-impact-assessment

```
改前（agent 自己推理）：
  1. 涉及哪些模块？→ agent 读文件判断
  2. 改什么接口？→ agent 查 tws-graph impact
  3. 消费者是谁？→ agent 全局搜索引用
  4. 会不会有连锁影响？→ agent 追着读
  5. 要不要回测？→ agent 判断

改后（查图 + agent 判断结合）：
  0. tws-graph index             ← 确保索引是最新的
  1. tws-graph impact <改动的符号>
  2. tws-graph callers <改动的接口>
  3. 如果有路由变更 → tws-graph callees <路由handler>
  4. agent 根据图输出 → 形成完整报告
  5. tws-graph 拍快照（供改后 diff 用）

原则：
  「图告诉 agent 客观事实（谁调了谁、影响半径多大），
    agent 做主观判断（风险等级、是否需要通知团队）」
```

### comp-design-sync

```
改前（agent 逐行比对）：
  1. 列出所有修改的文件
  2. 匹配每个文件对应的设计书
  3. 逐行比对：代码写的是什么、设计书写的是什么
  4. 不一致就更新

改后（图 diff + agent 判断）：
  0. tws-graph index              ← 重新索引（生成新快照）
  1. tws-graph diff snap-before snap-after
  2. diff 输出直接告诉 agent：
     哪些函数签名变了 → 哪些设计书需要更新
     新增了哪些符号 → 是否需要新建设计书
  3. agent 根据 diff 结果 + 设计书对照表 → 逐项同步
  4. 验证：tws-graph diff 确认无差异后，标记完成

原则：
  改了什么，diff 直接告诉你，不用 agent 用眼睛去比对。
  判断"需要同步到哪个设计书"仍然由 agent 做。
```

### comp-root-cause-analysis

```
改前：
  agent 读文件 → 搜调用者 → 搜调用者的调用者 → 一层层钻

改后：
  tws-graph trace <报错函数> <入口函数>
  → 一条完整的调用链，每一步在哪个文件、哪一行

  agent 只需沿着 trace 结果的每一层看代码 → 定位根因
```

### flow-new-project

```
init 阶段增加一步：
  tws-graph index
  tws-graph map          ← 自动生成 .tws/project-map.md
  tws-graph watch &      ← 后台启动文件监听（可选）
```

## 分阶段实施计划

### Phase 1：核心引擎（最小可用）

**目标：能 index，能查 callers/callees/impact**

```
tws-graph/                    # 新仓库
├── tws_graph/
│   ├── __init__.py
│   ├── cli.py                # CLI 入口（click）
│   ├── indexer.py            # tree-sitter 解析 + 建索引
│   ├── graph.py              # SQLite 建表 + 查询
│   ├── query.py              # callers/callees/impact/trace
│   ├── diff.py               # 快照对比
│   └── schema.sql            # DDL
├── pyproject.toml
└── README.md

支持语言：Python + TypeScript
覆盖语法：函数/类/方法/接口/导入/调用
CLI 命令：index, callers, callees, impact

工期预估：2-3 天（单人）
```

### Phase 2：技能集成

```
TWS 改动：
├── comp-impact-assessment/SKILL.md    # 加 tws-graph 调用步骤
├── comp-design-sync/SKILL.md          # 加 diff 步骤
├── comp-root-cause-analysis/SKILL.md  # 加 trace 步骤
└── flow-new-project/SKILL.md          # init 加 index + map

G-Ass 项目上实战验证：
  1. tws-graph index（约 643 个 TS 文件）
  2. 模拟一次改动 → impact-assessment 走图查询
  3. 改代码 → diff → design-sync
  4. 跟不用图的基线对比（准确度、耗时）

工期预估：1-2 天 + 实战胜场
```

### Phase 3：完整覆盖

```
- Java 支持（如果当时有 Spring 项目）
- 框架路由识别（FastAPI/Express/Spring）
- Project-map 自动生成
- 文件监听自动同步
- 性能优化（大项目 > 10000 文件）

工期预估：2-3 天
```

## 决策记录

- **用 Python 而非 Node**：与用户的 G-Ass 后端、YOLO、ASR/TTS 技术栈一致。tree-sitter Python binding 成熟。SQLite 是 Python 标准库内置。
- **用 tree-sitter 而非正则/LLM**：确定性的 AST 解析，不会"幻觉"。CodeGraph 已验证了这条路线。
- **CLI 而非 MCP**：TWS 不需要外部 MCP 服务。CLI 子命令直接可被 Claude Code 或其他 agent 调用，零网络依赖。
- **只做 Python/TS/Java 三种语言**：够覆盖用户当前所有项目。不做 CodeGraph 的 20+ 语言广度。
- **专注 TWS 的两个薄弱环节**：影响分析 + 设计同步。不做通用代码浏览器。

## 风险与应对

| 风险 | 应对 |
|------|------|
| tree-sitter 解析大文件慢（>5000行） | 增量索引 + 单文件超时保护 |
| 动态调度/回调无法静态分析 | 学 CodeGraph：标注 provenance=heuristic，不做假边 |
| 索引数据库膨胀（超大项目） | SQLite 本身轻量，10 万符号约几 MB |
| agent 不按指令用图查询 | 跟 CodeGraph 一样：工具描述 + 使用示例嵌入 SKILL.md |

## 验收标准

```
Phase 1 验收：
  □ tws-graph index 能在 G-Ass 项目上跑通（~643 TS 文件）
  □ tws-graph callers <某函数> 返回准确的调用者列表
  □ tws-graph impact <某函数> --depth 2 返回影响半径

Phase 2 验收：
  □ impact-assessment 用图之后，agent 不再需要 grep 来查调用者
  □ design-sync 的 diff 输出与实际代码变更一致
  □ 改动流程中，图查询次数 ≤ 5 次（小项目 1-2 次）
```

---

*本方案基于对 CodeGraph v0.9.9 的完整源码分析，提取了其核心理念（预建索引 → agent 查图），按 TWS 的"自己做闭环"原则重新设计。技术栈选 Python、规模聚焦三种语言、输出形式用 CLI 而非 MCP、独有的快照 diff 能力为 TWS design-sync 量身定制。*
