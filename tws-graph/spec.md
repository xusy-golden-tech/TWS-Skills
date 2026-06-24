# tws-graph v4.0.0 Specification

> 2026-06-24 | 全面超越 CBM — 边类型补齐 + 性能追平
> 原则：TDD 驱动、每边可验证、质量门禁先于数量

---

## 零、v3.0.0 收官验证（已完成）

### 质量门禁全通过

| 门禁 | TWS-Skills | g-ass-source | 状态 |
|------|-----------|-------------|------|
| G1: self/cls writes = 0 | 0 | 0 | PASS |
| G2: dangling extends < 10% | 0/77 (0%) | 0/177 (0%) | PASS |
| G3: built-in types not in [internal] | 0 | 0 | PASS |
| G4: test_edge 持久性 | 4,484→4,484 | 19,767→19,767 | PASS |
| G5: 跨项目 | N/A | 2,810 files, 564,808 edges | PASS |
| G7: 全量回归 | 3643 passed, 0 failed | N/A | PASS |

### v4.0.0 最终与 CBM 的差距 (g-ass-source)

| 维度 | tws-graph v4.0.0 | CBM | 差距 |
|------|-----------------|-----|------|
| 边类型（有产出） | **15** | ~20 | -5 |
| 索引速度 | 283s | 14.1s | 20x |
| 文件覆盖 | 2,828 | 3,241 | -413 |
| 节点数 | 88,125 | 66,221 | +21,904 |
| 边总数 | 611,585 | 280,121 | +331,464 |
| 语言数 | 26 (15 extractors) | 158 | 渐近追赶 |

**v4.0.0 核心成果：**
- 边类型从 10 → 15（+50%），差距从 -10 → -5（缩小 50%）
- 新增: implements, http_calls, env_accesses, grpc_server, grpc_client
- config_link: TWS-Skills 上 5,196 edges
- JS/JSX/MJS 文件纳入索引（+595 文件）
- 数据流分析集成到主提取（消除 re-parsing）

---

## 一、v4.0.0 目标

**在边类型覆盖和索引速度上全面追平/超越 CBM，同时保持 tws-graph 独有的数据流分析优势。**

三个 Phase：

```
P17: 边类型补齐（结构+跨服务）    → 10 → 16 种有产出边类型
P18: 边类型补齐（动态+语义）      → 16 → 20 种有产出边类型
P19: 性能追平                     → 235s → ≤ 30s（目标 8x，差距缩小到 2x 内）
P20: 文件覆盖 + 独有能力巩固      → 文件数追平 CBM + data_flow 深化
```

---

## 二、P17: 结构边类型补齐

### 2.1 implements 边

**问题**：Java/Kotlin/TypeScript/Python 接口实现关系未产出 `implements` 边。

**实现范围**：
- Python: `class Foo(Bar)` → 只查 ABC/Protocol 基类
- Java: `class Foo implements Bar` → tree-sitter 已有此节点类型
- TypeScript: `class Foo implements Bar` → tree-sitter 已有
- Kotlin: `class Foo : Bar` where Bar is interface

**关键技术**：在各语言 extractor 的 class 处理中添加接口检测。Python 中检查基类是否继承自 `abc.ABC` 或 `typing.Protocol`。

### 2.2 http_calls 边

**问题**：HTTP 调用关系未跨文件连接。

**实现范围**：
- Python: `requests.get/post/put/delete`, `httpx.*`, `urllib.request.*`
- TypeScript/JavaScript: `fetch()`, `axios.*`
- Java: `HttpClient.send`, `RestTemplate.*`
- 边结构: source=调用者函数, target=URL 字符串节点（新建 url 类节点）

### 2.3 grpc_service / grpc_client / grpc_server 边

**问题**：gRPC 服务定义和调用关系未产出。

**实现范围**：
- `.proto` 文件: 解析 service 定义 → 创建 service 节点 + rpc 方法节点
- Python: `grpc.insecure_channel` / `*_pb2_grpc.*Stub` 调用
- TypeScript: `@grpc/grpc-js` 调用
- 边: `grpc_service`(proto→impl), `grpc_client`(caller→stub), `grpc_server`(impl→proto)

### 2.4 env_accesses 边

**问题**：环境变量/配置访问未追踪。

**实现范围**：
- Python: `os.environ.get()`, `os.getenv()`, `environ[]`
- TypeScript: `process.env.*`
- Java: `System.getenv()`, `System.getProperty()`
- 边: source=访问函数, target=环境变量名字符串节点

### 2.5 测试门禁 (P17)

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| implements 边 > 0 | 有接口/ABC 的项目上产出 implements 边 | `test_implements_edges` |
| http_calls 边 > 0 | 含 HTTP 调用代码的项目上产出 http_calls 边 | `test_http_calls_edges` |
| grpc_* 边 > 0 | 含 .proto 的项目上产出三类 gRPC 边 | `test_grpc_edges` |
| env_accesses 边 > 0 | 含环境变量访问的项目上产出 env_accesses 边 | `test_env_accesses_edges` |
| 不引入回归 | 全部现有 3643 测试继续通过 | `pytest --tb=short` |
| g-ass-source 10→14+ | 在 g-ass-source 上边类型 ≥ 14 种 | `SELECT COUNT(DISTINCT kind) FROM edges` |

---

## 三、P18: 动态+语义边类型补齐

### 3.1 emits / listens_on 边（事件系统）

**问题**：事件发射/监听关系未追踪。

**实现范围**：
- Python: `signal.send()`, `@receiver`, `EventBus.emit`, `blinker.signal`
- TypeScript: `EventEmitter.emit()`, `.on()`, `.addEventListener()`
- 边: `emits`(emitter→event_name), `listens_on`(listener→event_name)

### 3.2 similar_to 边（代码克隆）

**问题**：代码克隆检测已有 MinHash LSH 实现但未产出边。

**解决**：将 CloneDetectPass 的结果写入 edges 表（`kind='similar_to'`），阈值默认 0.7。在全量索引后运行（--deep flag）。

### 3.3 config_link 边

**问题**：ConfigLinkPass 已有但未产出边。

**解决**：修复 ConfigLinkAnalysisPass，使其产出 `config_link` 边连接源码和配置文件（YAML/JSON/TOML）。

### 3.4 测试门禁 (P18)

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| emits/listens_on 边 | Python signal + TS EventEmitter 项目上产出 | `test_event_edges` |
| similar_to 边 > 0 | --deep 模式下产出克隆边 | `test_clone_edges` |
| config_link 边 > 0 | 含 YAML/JSON 配置的项目上产出 config_link 边 | `test_config_link_edges` |
| g-ass-source 16→20 | 边类型达 20 种 | `SELECT COUNT(DISTINCT kind) FROM edges` |
| 不引入回归 | 全部测试通过 | `pytest --tb=short` |

---

## 四、P19: 性能追平

### 4.1 目标

索引速度从 235s 降至 ≤ 30s（~8x 提升），在 g-ass-source 上与 CBM 的 14.1s 差距缩小到 2x 内。

### 4.2 优化方向

**A. 文件扫描优化**：
- 用 `os.scandir()` 替代 `os.walk()` (已有，确认)
- 减少 stat 调用次数
- 批量文件读取

**B. 提取器性能**：
- Python extractor 热点分析（cProfile）
- tree-sitter parse 复用（同语言文件共享 Language 对象）
- 减少正则编译开销

**C. 数据库写入优化**：
- WAL 模式确认（已有）
- 增大 batch INSERT 阈值（当前 10,000，可调至 50,000）
- 减少事务开销（合并小事务）

**D. 并行度优化**：
- 增加 worker 数的自适应调整
- chunk size 优化（50→100 可能减少调度开销）

**E. 低挂果实**：
- 移除 resolve_edges 中的冗余 SQL 查询
- `is_call_target_external` 的 project_files 查找优化（用 frozenset 已有）
- FTS rebuild 延迟（索引结束后一次性）

### 4.3 测试门禁 (P19)

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| g-ass-source 索引速度 | ≤ 30s（8x 提升） | `time tws-graph index --force` |
| TWS-Skills 索引速度 | ≤ 10s | `time tws-graph index --force` |
| 0-change 增量 | < 100ms（不退化） | `time tws-graph index` |
| 正确性 | 节点数/边数不变 | 对比优化前后 |
| 不引入回归 | 3643+ 测试通过 | `pytest --tb=short` |

---

## 五、P20: 文件覆盖 + 独有能力

### 5.1 文件扫描改进

**问题**：g-ass-source 上 tws-graph 扫描 2,810 文件，CBM 扫描 3,241（差 431 文件）。

**方向**：
- 检查 scanner.py 的排除规则是否有过度过滤
- 对比 CBM 多扫描的文件类型
- 确保所有主流源码文件类型未被过滤

### 5.2 独有能力深化

**tws-graph 独有的能力，CBM 不具备**：

1. **data_flows 深化** — arg→param 精确映射（37,232 edges on g-ass-source），CBM 无此能力
2. **reads/writes 精确追踪** — 变量级读写（300,683 reads+writes on g-ass-source），CBM 无此能力
3. **throws 追踪** — 异常传播路径（4,531 on g-ass-source），CBM 无此能力
4. **Cypher 查询** — 图数据库查询语言，比 MCP 工具更灵活

**深化方向**：
- data_flows 增加返回值和 yield 追踪
- reads/writes 增加跨函数传播（当前仅限于函数内）
- throws 增加跨函数异常传播链

### 5.3 测试门禁 (P20)

| 门禁 | 标准 |
|------|------|
| g-ass-source 文件数 ≥ 3,000 | 文件覆盖 ≥ CBM 的 92% |
| data_flows 不退化 | g-ass-source 上 ≥ 37,000 |
| reads+writes 不退化 | g-ass-source 上 ≥ 300,000 |
| 全量回归 | 全部测试通过 |

---

## 六、集成测试场景

### 6.1 边类型完整体验

```
场景: 开发者查询所有边类型
  Given: g-ass-source 已全量索引
  When: SELECT DISTINCT kind FROM edges
  Then: 返回 ≥ 20 种边类型（从当前 10 种）
```

### 6.2 性能不退化

```
场景: 0-change 增量索引速度
  Given: 索引已是最新
  When: tws-graph index
  Then: 耗时 < 100ms (actual work, excl. Python startup)
```

### 6.3 实用性验证

```
场景: 用边类型完成实用查询
  Given: g-ass-source 已全量索引
  When: 查询 HTTP 调用链: SELECT * FROM edges WHERE kind='http_calls'
    And: 查询事件流: SELECT * FROM edges WHERE kind='emits' OR kind='listens_on'
    And: 查询环境变量: SELECT * FROM edges WHERE kind='env_accesses'
    And: 查询 gRPC: SELECT * FROM edges WHERE kind IN ('grpc_service', 'grpc_client', 'grpc_server')
    And: 查询克隆: SELECT * FROM edges WHERE kind='similar_to'
    And: 查询配置: SELECT * FROM edges WHERE kind='config_link'
  Then: 每种边类型均有合理产出
```

### 6.4 全量回归

```
场景: 所有测试通过
  Given: P17+P18+P19+P20 全部实现
  When: pytest --tb=short
  Then: 0 failed
```

---

## 七、实现顺序

```
P17 (结构边) ──┐
               ├── 顺序实施（P17 改 extractor，需回归）
P18 (动态边) ──┘
               │
               ▼
P19 (性能追平) ── 独立实施（优化不改逻辑）
               │
               ▼
P20 (文件覆盖 + 独有能力) ── 收尾优化
```

P17 和 P18 有依赖关系（都改 extractor），顺序实施避免冲突。
P19 独立于 P17/P18（优化不改逻辑），可在 P18 后实施。
P20 收尾，确认最终数据。

---

## 八、v5.0.0 目标

> 2026-06-24 | 全面超越 CBM — 五维度并行推进

**核心目标：在边类型、性能、文件覆盖、MCP 服务、独有能力五个维度全面超越 CBM。**

```
P21: 边类型补齐（剩余 4 种）     → 15 → 20 种有产出边类型（追平 CBM）
P22: 性能突破                     → 283s → ≤ 30s（10x 提升）
P23: 文件覆盖                     → 2,828 → 3,000+（追平 CBM）
P24: MCP Server 完成              → 完整可用的脱网 MCP 服务
P25: 独有能力深化                 → data_flows/reads/writes/throws 升级
```

---

## 九、P21: 边类型补齐（剩余 4 种）

### 9.1 similar_to 边（代码克隆）

**现状**：CloneDetector (MinHash + LSH) 已实现 (`graph/algorithms/similarity.py`)，
CloneDetectionPass 已实现 (`pipeline/passes/clone_detect_pass.py`)，
但 `iter_nodes_by_kind` 返回的节点缺少 `body` 字段，导致检测无法运行。

**实现**：
1. 在 extractor 的 function/method 节点中存储 `body` 文本（AST 节点的源代码）
2. 确保 `iter_nodes_by_kind("function")` 和 `iter_nodes_by_kind("method")` 返回 `body` 字段
3. CloneDetectionPass 已在 --deep 模式下注册，确认其正常工作
4. 阈值默认 0.8，边属性含 similarity 分数

### 9.2 emits / listens_on 边（事件系统）

**实现范围**：
- Python: `signal.send()`, `@receiver`, `EventBus.emit`, `blinker.signal`, `dispatch`
- TypeScript: `EventEmitter.emit()`, `.on()`, `.addEventListener()`, `EventTarget.dispatchEvent`
- 边: `emits`(emitter→event_name), `listens_on`(listener→event_name)
- 检测方式: 在 call_expression 中添加事件模式匹配（与 http_calls/grpc 相同的内联模式）

**Python 事件模式**：
```
emit patterns: signal.send, EventBus.emit, blinker.signal, .emit(
listen patterns: @receiver, .on(, .addEventListener(, .connect(
```

**TypeScript 事件模式**：
```
emit patterns: .emit(, .dispatchEvent(, .fire(
listen patterns: .on(, .addEventListener(, .once(, .subscribe(
```

### 9.3 grpc_service 边（proto 文件解析）

**现状**：grpc_server/grpc_client 已通过内联模式检测实现。grpc_service 需要解析 .proto 文件。

**实现**：
1. 创建 proto extractor（tree-sitter-proto 或正则解析）
2. 解析 `service` 定义 → 创建 service 节点
3. 解析 `rpc` 方法 → 创建 rpc 方法节点
4. 边: `grpc_service`(proto service→rpc method), `grpc_server`(impl→proto rpc)

### 9.4 测试门禁 (P21)

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| similar_to 边 > 0 | --deep 模式下产出克隆边 | `test_clone_edges_producing` |
| emits 边 > 0 | Python signal + TS EventEmitter 项目上产出 | `test_emits_edges` |
| listens_on 边 > 0 | Python receiver + TS .on() 项目上产出 | `test_listens_on_edges` |
| grpc_service 边 > 0 | 含 .proto 的项目上产出 grpc_service 边 | `test_grpc_service_edges` |
| g-ass-source 边类型 ≥ 18 | 新增 4 种中至少 3 种在 g-ass-source 上有产出 | `SELECT COUNT(DISTINCT kind) FROM edges` |
| 不引入回归 | 全部 3663+ 测试继续通过 | `pytest --tb=short` |

---

## 十、P22: 性能突破（10x 提升）

### 10.1 目标

索引速度从 283s 降至 ≤ 30s（~10x 提升），在 g-ass-source 上与 CBM 的 14.1s 差距缩小到 2x 内。

### 10.2 优化方向

**A. Parser 池化（预估 -40%）**：
- 每个 worker 进程启动时创建一次 Parser+Language 对象，复用于同语言文件
- 当前每个文件都调用 `get_parser(lang_name)` → 每次创建新 Parser 对象
- 池化后：同语言文件共享 Parser 实例

**B. 跳过非代码文件解析（预估 -25%）**：
- 当前所有文件都走 `extract_full()`（含 tree-sitter parse）
- YAML/JSON/Dockerfile/CSS/HTML 等不需要 tree-sitter 解析
- 对这些文件只用简单的文本提取（已有 extractor 但不该走 tree-sitter）
- 检查 registry 中哪些 extractor 不需要 tree-sitter → 跳过 parse

**C. SQLite 批量写入优化（预估 -15%）**：
- 当前逐文件 BEGIN/COMMIT（每个文件一个事务）
- 改为：收集 N 个文件的 nodes/edges，批量在一个事务中写入
- WAL 模式已开启，synchronous=NORMAL 已设
- batch size 从当前逐文件 → 每 100 文件一个事务

**D. 文件扫描优化（预估 -5%）**：
- git ls-files 已优先使用（快）
- `_scan_fs` fallback 使用 os.walk → 改用 os.scandir
- 减少 stat 调用：合并 size+mtime 获取

**E. 低挂果实（预估 -5%）**：
- `resolve_edges` 中的 suffix index 构建可延迟
- FTS rebuild 延迟到索引结束后一次性执行
- `_populate_import_unresolved` 的多次 SQL 查询可合并

### 10.3 性能剖析基线

```bash
# 建立性能基线
python -m cProfile -o profile.dat -m tws_graph index --force  # (在 g-ass-source)
# 分析热点
python -c "import pstats; s=pstats.Stats('profile.dat'); s.sort_stats('cumtime').print_stats(30)"
```

### 10.4 测试门禁 (P22)

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| g-ass-source 索引速度 | ≤ 30s | `time tws-graph index --force` |
| TWS-Skills 索引速度 | ≤ 10s | `time tws-graph index --force` |
| 0-change 增量 | < 100ms | `time tws-graph index` |
| 正确性 | 节点数/边数不变 | 对比优化前后计数 |
| 不引入回归 | 3663+ 测试通过 | `pytest --tb=short` |

---

## 十一、P23: 文件覆盖

### 11.1 目标

g-ass-source 文件数从 2,828 → ≥ 3,000（追平 CBM 的 3,241）。

### 11.2 实现

**A. Scanner 排除规则审计**：
- 检查 `SKIP_DIRS` 是否有过度排除
- 对比 CBM 多索引的文件类型，找出遗漏的扩展名

**B. 新增 extractor 覆盖**：
- `.sh` / `.bash` — Shell 脚本 (tree-sitter-bash)
- `.lua` — Lua (tree-sitter-lua)
- 检查 g-ass-source 中被排除但应索引的文件类型

**C. 当前 extractor 扩展名审计**：
- 逐个检查所有 extractor 的 `extensions` 列表
- 确保覆盖了该语言的所有常见扩展名

### 11.3 测试门禁 (P23)

| 门禁 | 标准 |
|------|------|
| g-ass-source 文件数 ≥ 3,000 | 文件覆盖 ≥ CBM 的 92% |
| 不丢失任何当前已索引的文件类型 | 对比 v4.0.0 文件类型分布 |
| 全量回归 | 全部测试通过 |

---

## 十二、P24: MCP Server 完成

### 12.1 目标

完成 MCP Server 的开发、测试和部署配置，确保**纯脱网可用**（零外部 HTTP 依赖）。

### 12.2 当前状态

MCP Server 已有完整实现：
- `protocol.py` — JSON-RPC 2.0 消息类型 ✓
- `transport.py` — stdio 传输层 ✓
- `registry.py` — 工具/资源注册表 ✓
- `server.py` — MCP 服务器主类 ✓
- `resources.py` — 3 资源（stats, languages, health）✓
- `tools/` — 15 工具（search, code, analysis, advanced, query）✓
- `tws-graph serve` CLI 入口 ✓

### 12.3 待完成工作

**A. MCP 工具补全**：
- 新增 `query_cypher` 工具 — 通过 MCP 执行 Cypher 图查询
- 新增 `get_edge_distribution` 工具 — 返回边类型分布统计
- 确认所有 15 个现有工具的 `input_schema` 完整且正确

**B. MCP 集成测试**：
- 端到端测试：start server → initialize → tools/list → tools/call → shutdown
- 每个工具的基本功能测试
- 错误处理测试（未初始化、无效工具名、无效参数）

**C. 脱网保证验证**：
- 审查所有代码路径，确保 0 HTTP 调用
- 所有 import 必须是标准库或 tws_graph 内部模块
- 移除/有条件化任何可能触发网络访问的代码

**D. Claude Code 配置生成**：
- 生成 Claude Code MCP 配置文件
- `tws-graph mcp-config` 命令 — 输出可用的 MCP 配置 JSON

### 12.4 测试门禁 (P24)

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| MCP 协议合规 | initialize → tools/list → tools/call → shutdown 全流程 | `test_mcp_lifecycle` |
| 所有工具可调用 | 17 个工具全部 tools/call 成功 | `test_mcp_all_tools` |
| 所有资源可读取 | 3 个资源全部 resources/read 成功 | `test_mcp_all_resources` |
| 错误处理 | 无效工具名/参数 → 返回规范错误 | `test_mcp_error_handling` |
| 脱网验证 | 代码库中 0 处 HTTP 调用 | `grep -r "http://\|https://\|urllib.request\|requests\." mcp/` → 0 |
| CLI 入口 | `tws-graph serve --help` 正常输出 | `test_serve_cli` |
| mcp-config 命令 | `tws-graph mcp-config` 输出有效 JSON | `test_mcp_config` |

---

## 十三、P25: 独有能力深化

### 13.1 目标

强化 tws-graph 相比 CBM 的独有优势（data_flows, reads/writes, throws）。

### 13.2 data_flows 深化 — 返回值追踪

**现状**：data_flows 追踪参数→参数传递（37,232 edges on g-ass-source），未追踪 return 值。

**实现**：
- 在 `DataFlowExtractor` 中增加 `return_statement` 处理
- 追踪 return 表达式中引用的变量 → 创建 `data_flows` 边（source=变量节点, target=函数返回被使用处）
- 检测 `result = foo()` → `result` 是函数返回值的使用处

### 13.3 reads/writes 跨函数传播

**现状**：reads/writes 仅限于函数内变量访问（331,201 on g-ass-source）。

**实现**：
- 模块级变量：跟踪跨函数的读写关系
- 类属性：通过 `self.attr` 跟踪跨方法的读写
- 全局变量：通过 `global` 声明跟踪跨函数读写

### 13.4 throws 跨函数异常传播链

**现状**：throws 追踪函数内 raise 语句（4,682 on g-ass-source）。

**实现**：
- 追踪 try/except 中的异常传播
- 跨函数异常链：A raises X → B calls A 且不捕获 X → B also throws X
- 新增 `throws` 边连接调用链中的异常传播

### 13.5 测试门禁 (P25)

| 门禁 | 标准 |
|------|------|
| data_flows 返回值 ≥ 新产出 | 在 g-ass-source 上 data_flows 增加 return/yield 边 |
| reads+writes 跨函数 ≥ 新产出 | 模块级/类属性跨函数读写有产出 |
| throws 跨函数 ≥ 新产出 | 异常传播链有产出 |
| 不退化 | data_flows ≥ 37,000, reads+writes ≥ 300,000, throws ≥ 4,500 |
| 全量回归 | 全部测试通过 |

---

## 十四、集成测试场景

### 14.1 v5.0.0 边类型完整体验

```
场景: 开发者查询所有边类型
  Given: g-ass-source 已全量索引
  When: SELECT DISTINCT kind FROM edges
  Then: 返回 ≥ 20 种边类型
```

### 14.2 性能不退化

```
场景: 0-change 增量索引速度
  Given: 索引已是最新
  When: tws-graph index
  Then: 耗时 < 100ms
```

### 14.3 MCP 端到端

```
场景: MCP 服务器全生命周期
  Given: tws-graph index 已完成
  When: tws-graph serve 启动
    And: 客户端发送 initialize → tools/list → tools/call → resources/read → shutdown
  Then: 所有消息按 JSON-RPC 2.0 规范正确响应
```

### 14.4 独有能力查询

```
场景: 用独有能力完成实用查询
  Given: g-ass-source 已全量索引
  When: 查询数据流: SELECT * FROM edges WHERE kind='data_flows'
    And: 查询返回值追踪: data_flows 中含 return 来源边
    And: 查询异常传播链: throws 中含跨函数传播边
  Then: 每种分析均有合理产出
```

### 14.5 全量回归

```
场景: 所有测试通过
  Given: P21+P22+P23+P24+P25 全部实现
  When: pytest --tb=short
  Then: 0 failed
```

---

## 十五、实现顺序

```
P21 (边类型补齐) ── 先实施（改动广度最大，影响 extractor）
       │
       ▼
P25 (独有能力深化) ── 次之（改动 dataflow extractor，依赖 P21 稳定）
       │
       ▼
P22 (性能突破) ── 独立实施（优化不改逻辑，依赖前两阶段稳定后测量基准）
       │
       ▼
P23 (文件覆盖) ── 收尾（调整 scanner + 新增 extractor）
       │
       ▼
P24 (MCP Server) ── 并行可做（独立模块，不依赖前四阶段）
```

P24 可在任何时间点并行开发（MCP Server 是完全独立的模块）。

---

## 十六、完成定义 (DoD)

- [ ] P21: 6/6 测试门禁通过，边类型 ≥ 18 种（target 20）
- [ ] P22: 5/5 测试门禁通过，g-ass-source 索引 ≤ 30s
- [ ] P23: 3/3 测试门禁通过，文件覆盖 ≥ 3,000
- [ ] P24: 7/7 测试门禁通过，MCP Server 完整可用
- [ ] P25: 4/4 测试门禁通过，独有能力不退化 + 新产出
- [ ] g-ass-source 边类型 ≥ 20 种
- [ ] g-ass-source 索引速度 ≤ 30s
- [ ] TWS-Skills 索引速度 ≤ 10s
- [ ] 全量回归: 0 failed, 测试数 ≥ 3663
- [ ] `tws-graph lint`: 0 errors
- [ ] quality-gates.md 门禁全通过（不退化）
- [ ] MCP Server: 脱网可用，17 工具 3 资源全功能
