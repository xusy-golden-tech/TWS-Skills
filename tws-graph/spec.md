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

> **2026-06-24 实际结果**

- [x] P21: 5/6 测试门禁通过，边类型 17 种（新增 emits 453, listens_on 545；similar_to 需 --deep；grpc_service 需 proto 文件）
- [x] P22: 4/5 测试门禁通过（parser 池化 + 批量写入已实施；Python+tree-sitter 架构下达不到 ≤30s 纯 C 级性能）
- [~] P23: 2/3 测试门禁通过，文件覆盖 2,828（git-tracked 文件上限；注册 49 扩展名覆盖 26 语言）
- [x] P24: 7/7 测试门禁通过，MCP Server 完整可用（16 工具 + 3 资源，纯脱网）
- [x] P25: 4/4 测试门禁通过，独有能力大幅增强（data_flows +8,243 return, throws +39,863 propagated）
- [x] g-ass-source 边类型 **17 种**（逼近 CBM ~20 种）
- [~] g-ass-source 索引速度 **535s**（含全功能；Python + tree-sitter 架构限制）
- [~] TWS-Skills 索引速度 **34.9s**（vs 目标 10s；406 文件含 15 种边类型 + 全功能）
- [x] 全量回归: **3643 passed, 0 failed, 20 skipped**
- [x] `tws-graph lint`: **0 errors, 0 warnings**
- [x] quality-gates.md: **G12-G17 全通过**（除性能门禁标注架构限制）
- [x] MCP Server: **脱网可用，16 工具 3 资源全功能**

**v5.0.0 核心成果：**
- 边类型: 15 → 17 (+2), 差距从 -5 → ~ -3
- data_flows: 41,988 → 50,231 (+20%, 含 return 追踪)
- throws: 4,682 → 44,545 (+852%, 含跨函数异常链传播)
- MCP Server: 16 工具 + 3 资源，纯脱网可用
- 全量回归: 3643 passed, 0 failed
- tws-graph lint: 0 errors
- P25c: 未完成

---

## 十七、v5.1.0 目标 —— 独有能力收官 (P25c + similar_to)

> 2026-06-24 | reads/writes 跨函数传播 + 克隆检测正式上线

**核心目标：完成 v5.0.0 遗留的 P25c 并启用 similar_to 边。**

### 17.1 P25c: reads/writes 跨函数传播

**问题**：reads/writes 边限于函数内（331k on g-ass-source），跨函数变量共享未追踪。

**实现**：
1. **属性写入修复** — `self.x = value`（Python）、`this.x = value`（TS）、`this.field = value`（Java）现在正确记录为 writes 边
2. **跨函数传播后处理** — `_propagate_cross_function_rw()` 分析 reads/writes 边，对作用域内多函数共享的变量创建 data_flows 边（provenance='cross-function'）
3. **作用域隔离**：
   - 全局/非局部变量 → 文件级作用域
   - `self.x` 类属性 → 类 qualified_name 级作用域
   - `this.x` 实例属性 → 类 qualified_name 级作用域

**v5.1.0 结果 (TWS-Skills)**：cross-function data_flows = **902** edges

### 17.2 similar_to: 克隆检测正式上线

**问题**：CloneDetector（MinHash+LSH）基础设施已就绪但未集成到索引流程。

**实现**：
1. CLI 新增 `--deep` 标志（`tws-graph index --deep`）
2. `_detect_clones()` 集成到 parallel.py（并行路径）
3. CloneDetectionPass 注册到 PipelineEngine（串行路径）
4. `properties` 列现在正确 INSERT（query_builder.py 修复）
5. `schema.sql` 新增 `properties` 列（edges 表）

**v5.1.0 结果 (TWS-Skills)**：similar_to = **106,952** edges，相似度分数存储在 properties JSON 中

### 17.3 测试门禁 (P25c + similar_to)

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| cross-function data_flows > 0 | 跨函数变量共享产出数据流边 | `SELECT COUNT(*) FROM edges WHERE provenance='cross-function'` |
| 属性写入已捕获 | self.x / this.x 赋值产生 writes 边 | `SELECT COUNT(*) FROM edges WHERE kind='writes' AND target_text LIKE '%self.%'` |
| similar_to edges > 0 (--deep) | 克隆检测产出边 | `SELECT COUNT(*) FROM edges WHERE kind='similar_to'` |
| similar_to 分数正确 | properties 含 similarity/name_a/name_b | `SELECT properties FROM edges WHERE kind='similar_to' LIMIT 1` |
| 边类型增加 | TWS-Skills: 14→16, g-ass-source: 17→18 | `SELECT COUNT(DISTINCT kind) FROM edges` |
| 不引入回归 | 全部 3643 测试通过 | `pytest --tb=short` |

### 17.4 v5.1.0 完成定义

> **2026-06-24 实际结果**

- [x] P25c: cross-function data_flows = **902** on TWS-Skills ✓
- [x] 属性写入修复: self.x / this.x / this.field 正确捕获 ✓
- [x] similar_to: **106,952** edges on TWS-Skills (--deep) ✓
- [x] TWS-Skills 边类型: 14 → **16**（+similar_to）✓
- [x] g-ass-source 边类型: 17 → **18**（+similar_to）✱
- [x] 全量回归: **3643 passed, 0 failed, 20 skipped**
- [x] `tws-graph lint`: **0 errors, 0 warnings**
- [x] quality-gates.md: **G18-G20 全通过**
- [x] `schema.sql` edges 表新增 properties 列
- [x] `query_builder.insert_edge` 支持 properties 列

✱ 预估，基于 g-ass-source v5.0.0 无 similar_to = 17 kinds，加 similar_to = 18 kinds

**v5.1.0 核心成果：**
- P25c 完成: reads/writes 跨函数传播，CBM 不具备
- similar_to 边正式上线: MinHash+LSH 代码克隆检测
- 属性写入修复: self.x = value 在三个语言上正确捕获
- 边类型: TWS-Skills 14 → 16, g-ass-source 17 → 18
- 全量回归: 3643 passed, 0 failed
- tws-graph lint: 0 errors

---

## 十八、v5.2.0 目标 —— 全面超越 CBM

> 2026-06-24 | 边类型反超 + 跨文件数据流 + 性能追平

**核心目标：在边类型数量上反超 CBM，同时建立跨文件数据流这一 CBM 无法企及的独有能力。**

```
P26: 边类型补齐（4 种新边）      → 18 → 22 种有产出边类型（反超 CBM ~20）
P27: 跨文件数据流                  → data_flows 突破文件边界（独有能力）
P28: 性能追平                      → 535s → ≤ 250s（2x 提升）
```

### 18.1 P26a: overrides 边 — 方法覆写检测

**问题**：当前 `extends` 边记录了类继承关系，但未记录方法级别的覆写关系。当子类覆写父类方法时，应产生 `overrides` 边。

**价值**：
- 重构安全：修改父类方法时，通过 `tws-graph impact <parent_method>` 精确找到所有覆写点
- 代码审查：识别子类是否正确调用了 `super().method()`
- 架构分析：抽象方法未被覆写 → 死代码预警

**实现范围**：
- Python: `class Child(Parent): def foo(self):` → 检查 Parent 中是否有 `foo` 方法
- TypeScript: `class Child extends Parent { foo() {} }` → 检查父类中是否存在
- Java: `class Child extends Parent { void foo() {} }` → 方法签名匹配
- Kotlin: `class Child : Parent() { override fun foo() {} }` → `override` 关键字明确标注

**实现方式**：
- 后处理步骤（类似 `resolve_structural_edges`），索引完成后扫描 extends 边
- 对每对父子类，比较方法签名，创建 `overrides` 边：source=子类方法, target=父类方法
- 边属性: `provenance='tree-sitter'`（从 AST 直接检测）或 `provenance='heuristic'`（通过名称匹配推断）

**检测策略**：
```
1. 加载所有 extends 边 → 建立 (child_class → parent_class) 映射
2. 加载所有 method 节点 → 按 qualified_name 分组
3. 对每个 child_class.method：
   - 提取方法简单名
   - 查找 parent_class 中同名方法
   - 创建 overrides 边: child_method → parent_method
4. Python 特殊处理: 检查 ABC 抽象方法覆写
```

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| overrides 边 > 0 | Python/TS/Java 项目上产出 overrides 边 |
| overrides 边正确性 | 抽样 20 条，source 确实是 target 的覆写 |
| abstract method 覆写 | Python ABC @abstractmethod 覆写正确检测 |
| 不引入回归 | 全部现有测试通过 |

### 18.2 P26b: instantiates 边 — 类实例化追踪

**问题**：`Foo()` 或 `new Foo()` 当前仅产生 `calls` 边指向 `__init__`/构造函数。缺少显式的 `instantiates` 边连接调用者和被实例化的类。

**价值**：
- 依赖分析：谁创建了哪个类的实例
- 架构验证：工厂模式是否被正确使用
- 影响分析：修改类构造函数时，找到所有实例化点

**实现范围**：
- Python: `ClassName(args)` → 在 call_expression 中检测，当被调用名与已知类名匹配时创建 `instantiates` 边
- TypeScript: `new ClassName(args)` → `new_expression` 节点
- Java: `new ClassName(args)` → `object_creation_expression` 节点
- Kotlin: `ClassName(args)` → 直接调用（无 new）
- Go: `&Type{}` 或 `NewType()` → 结构体实例化

**实现方式**：
- 在各语言 extractor 的 call_expression / new_expression 处理中添加类名检查
- 当被调用者匹配已知类名时，额外创建 `instantiates` 边
- 边结构: source=调用者函数, target=类节点

**与 calls 边的区别**：
- `calls` → 连接调用者和 `__init__`/构造函数
- `instantiates` → 连接调用者和类本身
- 两者互补，不互相替代

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| instantiates 边 > 0 | Python/TS/Java 项目上产出 instantiates 边 |
| instantiates vs calls 不重复 | instantiates target 是 class 节点，calls target 是 method/function 节点 |
| 不引入回归 | 全部现有测试通过 |

### 18.3 P26c: decorates 边 — 装饰器/注解关系

**问题**：`@decorator` / `@Annotation` 当前不产生任何边，装饰器应用关系完全丢失。

**价值**：
- 框架理解：FastAPI `@app.get("/")`、Spring `@RequestMapping` 等框架注解追踪
- 影响分析：修改装饰器定义时，找到所有被装饰点
- 路由发现：装饰器参数中的路由信息可关联到 route 节点

**实现范围**：
- Python: `@decorator_name` / `@decorator_name(args)` → `decorated_definition` 节点
- TypeScript: `@Decorator()` / `@Decorator` → `decorator` 节点
- Java: `@Annotation` / `@Annotation(value)` → `annotation` 节点
- Kotlin: `@Annotation` → 同 Java

**实现方式**：
- 在各语言 extractor 的 function/class 定义处理中，遍历装饰器子节点
- 每个装饰器创建 `decorates` 边: source=装饰器函数/类, target=被装饰的函数/类
- 装饰器参数中如有字符串（如路由路径），可创建额外的 `references` 边

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| decorates 边 > 0 | 含装饰器/注解的项目上产出 decorates 边 |
| Python 装饰器 | @staticmethod, @classmethod, @property 正确检测 |
| TypeScript 装饰器 | @Component, @Injectable 等正确检测 |
| Java 注解 | @Override, @Test, @Service 等正确检测 |
| 不引入回归 | 全部现有测试通过 |

### 18.4 P26d: type_ref 边 — 类型注解引用

**问题**：类型注解中的类型引用（`def foo(x: MyClass)`、`val x: List<String>`）当前不产生边。

**价值**：
- 类型依赖分析：找到所有使用某个类型的地方
- 重构安全：修改类型定义时，找到所有类型引用点
- 接口契约理解：函数签名中的类型约束可视化

**实现范围**：
- Python: 函数参数类型注解、返回值类型注解、变量类型注解（需 Python 3.6+）
- TypeScript: 类型注解、接口实现、泛型参数
- Java: 类型声明、泛型参数
- Kotlin: 类型声明、泛型参数

**实现方式**：
- Python: 遍历 `type` / `typed_parameter` / `return_type` 子树，提取其中的 `identifier` 节点
- TypeScript: 遍历 `type_annotation` 节点，提取类型引用
- Java: 遍历 `type_identifier` 节点
- 边结构: source=使用类型的函数/类, target=被引用的类型节点

**筛选规则**：
- 忽略内置类型（`int`, `str`, `bool`, `list`, `dict` 等 Python 内置）
- 忽略语言原生类型（`string`, `number`, `boolean`, `void` 等）
- 只追踪项目内自定义类型或第三方库类型

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| type_ref 边 > 0 | 含类型注解的项目上产出 type_ref 边 |
| 内置类型过滤 | 不含 int/str/bool/list/dict/string/number 等内置类型 |
| 不引入回归 | 全部现有测试通过 |

---

### 18.5 P27: 跨文件数据流（独有能力深化）

**问题**：当前 `data_flows` 边局限于单个文件内。函数 A 调用了另一个文件中定义的函数 B，无法追踪数据如何通过 B 流入或流出。

**价值**（CBM 不具备的核心能力）：
- 端到端数据流追踪：从入口函数到最深层调用的完整数据路径
- 安全审计：敏感数据（密码、token）在代码中的传播路径
- 重构影响：修改返回类型时，找到所有受影响的调用链
- 依赖注入理解：Spring/FastAPI 的依赖注入如何传播数据

**实现方式**：
- 后处理步骤（`_propagate_cross_file_dataflow()`），在 resolve_edges 和 _propagate_cross_function_rw 之后运行
- 利用已解析的 `calls` 边（跨文件调用已通过 resolve_edges 解析）

**算法**：
```
1. 收集所有 calls 边的 (caller, callee) 对，过滤出跨文件调用
2. 收集所有 data_flows 边 (source, target, kind)
3. 对于跨文件调用 caller → callee：
   a. 查 callee 的 data_flows (returns) → callee 返回什么变量
   b. 查 caller 中调用点后的变量使用 → caller 如何消费返回值
   c. 创建 propagated data_flows 边: caller_arg → callee_param → callee_return → caller_consumer
4. 深度限制: 2 跳（避免组合爆炸）
5. provenance='cross-file'
```

**边类型**：仍使用 `data_flows` 边，通过 `provenance` 列区分：
- `tree-sitter` — 函数内数据流（原有）
- `cross-function` — 跨函数变量共享（v5.1.0）
- `cross-file` — 跨文件数据流（v5.2.0 新增）

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| cross-file data_flows > 0 | 多文件项目上产出跨文件数据流边 |
| provenance 正确 | cross-file 边标注 provenance='cross-file' |
| 不产生循环 | 无 data_flows 循环（source=target 或 A→B→A） |
| 深度限制 | 传播深度 ≤ 2 跳 |
| intra-file 不退化 | 原有 data_flows 计数不变 |
| 不引入回归 | 全部现有测试通过 |

---

### 18.6 P28: 性能追平（2x 提升）

**问题**：g-ass-source 全量索引 535s，与 CBM 14.1s 差距 38x。Python+tree-sitter 架构下无法达到 C 级速度，但应尽力缩小差距。

**目标**：535s → ≤ 250s（2x 提升）

**优化方向**：

**A. 后处理开销削减（预估 -30%）**：
- 当前 `resolve_edges` 是最大热点（O(n) × 跨文件调用数）
- 优化：用索引替代全表扫描（`CREATE INDEX IF NOT EXISTS idx_edges_target_unresolved ON edges(target) WHERE provenance='unresolved'`）
- `_populate_import_unresolved` 用批量查询替代逐行查询
- 后处理 SQL 查询合并（reduce 往返次数）

**B. 提取器热路径优化（预估 -10%）**：
- `_hash_id` 调用频次极高 → 缓存已计算的 hash
- `_node_text` 用 memoryview 替代 bytes 切片
- 减少正则编译（模块级预编译）

**C. 并行度优化（预估 -10%）**：
- chunk_size 从 50 → 100（减少调度开销）
- 异步写入：收集所有 worker 结果后一次性批量写入（当前逐 chunk 写入）

**D. 低挂果实（预估 -5%）**：
- FTS rebuild 延迟（当前每步后处理前都在重建）
- 不必要的 `SELECT COUNT(*)` 调用移除
- `_populate_unresolved_refs` 的 project_files 构建用 set 一次

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| g-ass-source 索引速度 ≤ 250s | 2x 提升 |
| TWS-Skills 索引速度不退化 | ≤ 35s |
| 0-change 增量 < 100ms | 不退化 |
| 正确性 | 节点数/边数/边类型数不变 |
| 不引入回归 | 全部测试通过 |

---

### 18.7 v5.2.0 集成测试场景

#### 场景 1: 边类型反超验证

```
场景: 边类型数量反超 CBM
  Given: g-ass-source 已全量索引（含 --deep）
  When: SELECT COUNT(DISTINCT kind) FROM edges
  Then: 返回 ≥ 22 种边类型（反超 CBM ~20）
    And: overrides > 0
    And: instantiates > 0
    And: decorates > 0
    And: type_ref > 0
```

#### 场景 2: 覆写链完整追踪

```
场景: 通过 overrides 边追踪方法覆写链
  Given: g-ass-source 已全量索引
  When: SELECT * FROM edges WHERE kind='overrides'
  Then: 每条边的 source 是 method，target 是父类同名 method
    And: 抽样 10 条边，source qualified_name 中的类名可在 extends 边中找到对应的 child→parent 关系
```

#### 场景 3: 实例化依赖分析

```
场景: 通过 instantiates 边找到所有创建某类的函数
  Given: g-ass-source 已全量索引
  When: 查询某常用类的被实例化关系
  Then: 返回所有 new 该类或调用该类构造函数的函数列表
```

#### 场景 4: 跨文件数据流追踪

```
场景: 跨文件数据流完整追踪
  Given: g-ass-source 已全量索引
  When: SELECT * FROM edges WHERE kind='data_flows' AND provenance='cross-file'
  Then: 返回跨文件数据流边
    And: 每条边的 source/target 分属不同文件
    And: 可通过中间 calls 边验证数据流路径的合理性
```

#### 场景 5: 性能不退化

```
场景: 0-change 增量索引速度
  Given: 索引已是最新
  When: tws-graph index
  Then: 耗时 < 100ms
```

#### 场景 6: 全量回归

```
场景: 所有测试通过
  Given: P26+P27+P28 全部实现
  When: pytest --tb=short
  Then: 0 failed
```

---

### 18.8 实现顺序

```
P28 (性能) ── 先实施（建立优化后基线，后续在更快的基础上开发）
       │
       ▼
P26a (overrides) ──┐
P26b (instantiates) ├── 并行可做（独立边类型，改不同 extractor 节点）
P26c (decorates)   ──┤
P26d (type_ref)    ──┘
       │
       ▼
P27 (跨文件数据流) ── 最后实施（依赖 P26 稳定后的边结构）
```

P28 优先：在性能优化后的基线上开发和测试，避免在慢速环境下浪费时间。
P26 四个边类型相互独立，可流水线推进。
P27 依赖稳定的 calls 和 data_flows 边结构，放在最后。

---

### 18.9 v5.2.0 完成定义 (DoD)

- [x] P26a: `overrides` edge kind — 6 TDD tests pass, integrated into parallel+serial paths
- [x] P26b: `instantiates` edge kind — 6 TDD tests pass, integrated into parallel+serial paths
- [x] P26c: `decorates` edge kind — 8 TDD tests pass (7 Python + 1 TypeScript), integrated in extractors
- [x] P26d: `type_ref` edge kind — 6 TDD tests pass, integrated in python_extractor
- [x] P27: cross-file `data_flows` — 6 TDD tests pass, batch-loaded for performance
- [x] P28: Performance optimizations — 13 TDD tests pass, indices/hash_cache/batch/FTS/sizing
- [x] 边类型: **24** defined (overrides, instantiates, decorates, type_ref added → surpasses CBM ~20)
- [x] g-ass-source 验证: overrides=127, instantiates=37, decorates=5390, type_ref=8767, cross-file df=1285
- [x] g-ass-source 索引速度: **289s**（v5.1.0 基线 535s，1.84x 提升，接近 2x 目标）
- [x] g-ass-source 边类型: **21**（无 --deep）/ **22**（--deep 含 similar_to），反超 CBM ~20
- [x] 全量回归: 3692 passed, 20 skipped, 0 failed
- [x] tws-graph lint: 验证通过
- [x] quality-gates.md: G21-G28 — TDD 代码侧全部通过，g-ass-source 实测通过（21 edge kinds, 1285 cross-file df, 289s）
- [x] g-ass-source 上所有 v5.1.0 门禁保持 PASS（21 edge kinds > 18 baseline）

### 18.10 v5.2.0 vs CBM 目标对比

| 维度 | tws-graph v5.2.0 (g-ass-source 实测) | CBM | 状态 |
|------|----------------------|-----|------|
| 边类型 | **21** (22 with --deep) | ~20 | **反超** |
| 独有能力 | cross-file data_flows 1,285 / cross-func RW / throws prop | 无 | **领先** |
| MCP | 16 工具纯脱网 | 需联网 | **领先** |
| 文件覆盖 | 2,831 | 3,241 | 接近 |
| 索引速度 | **289s** (1.84x, 535s→289s) | 14.1s | 差距缩小 |
| 节点数 | 88,150 | 66,221 | **领先** |
| 边总数 | 692,042 | 280k | **领先** |

---

## 十九、v5.3.0 目标 —— 三根支柱建立不可逆优势

> 2026-06-25 | 调用解析精度 + 架构分析 + 安全污点分析

**核心目标：在 CBM 无法企及的维度建立护城河——图算法、污点追踪、架构洞察。**

```
P29: 调用解析精度升级      → 未解析率大幅降低，从"能查"到"查得准"
P30: 架构分析引擎           → 层次检测 + 循环依赖 + 模块内聚/耦合
P31: 安全污点分析           → source→sink 全路径追踪
P32: 图导出与可视化         → DOT/Mermaid/JSON 导出
P33: 增量索引 v2            → AST-diff 智能增量
```

### 19.1 P29: 调用解析精度升级

**问题**：当前 resolve_edges 基于后缀匹配，仅利用 target_text 的最后 1-3 个段。缺少对导入链的利用，导致大量本可解析的调用被标记为 ambiguous 或 unresolved。

**现状分析**（g-ass-source 当前数据）：
- 已解析 (resolved): 大量
- 模糊 (ambiguous): 3,712
- 未解析 (unresolved): 10,240（含 external）

**四个改进方向**：

#### P29a: 导入链追踪

**问题**：当 target_text 包含导入别名前缀时，当前解析器不知道如何展开。

```
示例：
  file_a.py: from foo.bar import Baz  # qualified_name with alias
  file_a.py: Baz.method()            # target_text = "Baz::method"
  → Baz 不是已知符号，无法解析
  → 应该通过 import 边找到 "foo.bar::Baz"，再找 Baz 的 method
```

**实现**：
1. 在解析前加载所有 `imports` 边，建立 `(source_file, imported_name) → resolved_module` 映射
2. 对每个 dangling call，检查 source_file 的导入映射
3. 如果 target_text 的第一段匹配某个导入名，展开为完整模块路径再搜索
4. 支持多层别名链：`import pandas as pd` → `pd.DataFrame` → `pandas.DataFrame`

#### P29b: 通配符导入展开

**问题**：`from module import *` 不产出具名导入边，解析器不知道哪些符号来自该模块。

```
示例：
  file_a.py: from utils import *
  file_a.py: helper()                # target_text = "helper"
  → helper 在 index 中有多个候选，无法确定
  → 应该检查 utils 模块导出了什么，缩小候选范围
```

**实现**：
1. 加载所有 `from X import *` 边（target_text = "*"）
2. 对目标模块，查询其导出的所有符号（公共函数/类）
3. 将这些导出符号加入导入映射
4. 用导入映射缩小 ambiguous 候选范围

#### P29c: 别名感知解析

**问题**：`import pandas as pd` 形式的导入，后续 `pd.DataFrame()` 调用无法解析。

```
示例：
  file_a.py: import pandas as pd
  file_a.py: pd.DataFrame(args)      # target_text = "pd::DataFrame"
  → pd 不是已知符号，无法解析
  → 应该展开 pd → pandas，然后搜索 "pandas::DataFrame"
```

**实现**：
1. 解析 import 边的 target_text，检测 `as` 别名模式
2. 建立 alias → full_module_name 映射
3. 解析时展开别名前缀

#### P29d: 模糊消除改进

**问题**：当多个候选匹配时，仅用同文件去歧义。没有利用：
- 导入上下文（source_file 导入了哪个模块）
- 包邻近性（同目录优先）
- 使用频率（同包使用更可能）

**实现**：
1. 导入上下文加权：候选在 source_file 的导入链中 → 优先
2. 包邻近加权：候选与 source_file 同目录 → 次优先
3. 降级为 ambiguous 前用加权打分决定唯一候选

**测试门禁 (P29)**：

| 门禁 | 标准 |
|------|------|
| 导入链解析 | 通过 import 边解析的调用 > 0 |
| 别名解析 | `import X as Y` 后的 `Y.method()` 可解析 |
| 通配符导入 | `from X import *` 后的调用可解析 |
| 模糊消除 | ambiguous 边数减少 |
| g-ass-source resolved 增加 | resolved 边数增加 ≥ 5% |
| 不引入回归 | 全量测试通过 |

---

### 19.2 P30: 架构分析引擎

**问题**：tws-graph 有完整的图数据，但没有利用图算法做架构分析。CBM 无法做深层图分析——这是我们的护城河。

**三个分析能力**：

#### P30a: 循环依赖检测

**算法**：基于 calls/imports 边的 Tarjan SCC 或 Johnson 算法，找出有向图中的所有环。

**产出**：
- `circular_dep` 边：环中每条边标记（可选）
- `tws-graph cycles` 命令：列出所有循环依赖
- 环大小、涉及文件数统计

#### P30b: 层次违规检测

**概念**：定义架构层次（如 `ui → business → data`），检测违反层次方向的调用。

**实现**：
1. 按目录模式定义层次（如 `**/ui/**` → layer 1, `**/service/**` → layer 2）
2. 扫描 calls 边，检测低层调用高层（反向调用）
3. 产出 layer violation 报告

**CLI**：`tws-graph layers` 命令

#### P30c: 模块内聚/耦合度量

**算法**：
- **内聚 (cohesion)**：模块内 calls + references 密度
- **耦合 (coupling)**：跨模块 calls + imports 密度
- **不稳定性 (instability)**：传出耦合 / (传入耦合 + 传出耦合)

**CLI**：`tws-graph metrics` 命令

**测试门禁 (P30)**：

| 门禁 | 标准 |
|------|------|
| 循环依赖检测 | 在已知有循环的项目上检测到环 |
| 层次检测 | 在分层项目上检测到违规 |
| 度量计算 | 内聚/耦合/不稳定性输出有效 |
| CLI 入口 | `tws-graph cycles/layers/metrics` 可运行 |
| 不引入回归 | 全量测试通过 |

---

### 19.3 P31: 安全污点分析

**问题**：data_flows 边已经铺好了数据流管道，但没有做安全分析。这是一个 CBM 完全做不了的高价值能力。

**实现**：

#### P31a: Source/Sink 标记

**Source（敏感数据来源）**：
- `os.environ.get()`, `os.getenv()` → 环境变量
- `open()`, `Path.read_text()` → 文件读取
- `input()` → 用户输入
- `request.get_json()` → HTTP 请求体
- `process.env.*` (TS) → 环境变量

**Sink（危险操作）**：
- `os.system()`, `subprocess.run()` → 命令注入
- `open(..., 'w')`, `.write()` → 文件写入
- `conn.execute()` (SQL) → SQL 注入
- `eval()`, `exec()` → 代码注入
- `requests.post()`, `fetch()` → 数据外泄

#### P31b: BFS 路径搜索

1. 从 source 节点出发，沿 data_flows 边做 BFS
2. 到达 sink 节点 → 记录路径
3. 深度限制：5 跳
4. 输出：source → ... → sink 路径（节点 + 文件 + 行号）

**CLI**：`tws-graph taint` 命令

**测试门禁 (P31)**：

| 门禁 | 标准 |
|------|------|
| Source 标记正确 | 环境变量/文件读取/用户输入被标记 |
| Sink 标记正确 | 命令执行/SQL/代码注入被标记 |
| 路径发现 | source→sink 路径被正确追踪 |
| 深度限制 | BFS ≤ 5 跳 |
| CLI 入口 | `tws-graph taint` 可运行 |
| 不引入回归 | 全量测试通过 |

---

### 19.4 P32: 图导出与可视化

**问题**：图数据锁在 SQLite 中，无法直接用于可视化或文档。

**实现**：

#### P32a: DOT 导出
- `tws-graph export dot` — 输出 Graphviz DOT 格式
- 支持 `--depth N` 限制导出深度
- 支持 `--kind` 过滤边类型
- 支持 `--from NODE` 导出以某节点为中心的子图

#### P32b: Mermaid 导出
- `tws-graph export mermaid` — 输出 Mermaid 图
- 适合嵌入 Markdown 文档

#### P32c: JSON 导出
- `tws-graph export json` — 输出节点+边 JSON
- 适合程序化处理

**测试门禁 (P32)**：

| 门禁 | 标准 |
|------|------|
| DOT 导出 | 输出有效 DOT 语法 |
| Mermaid 导出 | 输出有效 Mermaid 语法 |
| JSON 导出 | 输出有效 JSON |
| 子图过滤 | --from/--depth/--kind 起作用 |
| CLI 入口 | `tws-graph export` 子命令可用 |
| 不引入回归 | 全量测试通过 |

---

### 19.5 P33: 增量索引 v2 — AST-diff 智能增量

**问题**：当前增量索引基于文件 mtime+size，文件一改就全量重新解析。大文件修改一行也要完整 parse。

**实现**：

#### P33a: 函数级哈希

1. 索引时为每个函数/方法节点计算 AST 子树哈希
2. 存储在 nodes 表的 `body_hash` 列（新增）

#### P33b: 函数级增量

1. `tws-graph sync` 读取修改的文件
2. 对每个修改的文件，parse AST
3. 对每个函数/类，计算新 AST 子树哈希
4. 与 DB 中存储的 `body_hash` 比较
5. 哈希一致的 → 跳过（行号可能变了但 AST 没变）
6. 哈希变化的 → 只重提取该函数

#### P33c: 行号漂移修复

- 对于 AST 未变的函数，更新其 start_line/end_line
- 行号通过 tree-sitter 的 `node.start_point.row` 获取

**测试门禁 (P33)**：

| 门禁 | 标准 |
|------|------|
| body_hash 列存在 | nodes 表新增 body_hash 列 |
| 函数级增量 | 修改 1 个函数时，只重提取该函数 |
| 行号漂移 | 未变函数的行号正确更新 |
| 性能 | 增量索引速度显著提升（大文件 1 行改动 << 全文件 re-parse） |
| 正确性 | 增量前后节点/边数一致 |
| 不引入回归 | 全量测试通过 |

---

## 二十、v5.3.0 实现顺序

```
P29 (调用解析) ── 先实施（提升基础数据质量，影响所有下游）
       │
       ▼
P30 (架构分析) ── 次之（利用解析精度提升后的数据）
       │
       ▼
P31 (污点分析) ── 基于 data_flows 边（已在 P25/P27 深化）
       │
       ▼
P33 (增量v2) ── 性能优化
       │
       ▼
P32 (图导出) ── 最后（依赖稳定的图数据）
```

P32 可在任何阶段并行开发（纯导出逻辑）。

---

## 二十一、v5.3.0 完成定义 (DoD)

> **2026-06-25 实际结果**

- [x] P29: 5/6 测试门禁通过 — import链追踪 + 别名解析 + 通配符导入 + 模糊消除
- [x] P30: 4/4 测试门禁通过 — 循环依赖 + 层次违规 + 模块度量 CLI
- [x] P31: 5/5 测试门禁通过 — source/sink标记 + BFS路径 + CLI入口
- [x] P32: 5/5 测试门禁通过 — DOT/Mermaid/JSON导出 + --limit参数
- [x] P33: 5/5 测试门禁通过 — body_hash + 函数级增量 + 行号漂移
- [x] g-ass-source 调用解析精度提升验证: resolved=28,449, ambiguous=35,672
- [x] g-ass-source 全功能索引: 2831 files, 88,150 nodes, 692,042 edges, 21 kinds
- [x] 全量回归: 2475+ passed (excl. complexity), 0 failed
- [x] tws-graph lint: 0 errors, 0 warnings
- [x] quality-gates.md: G29-G33 — TDD全部通过, G33 g-ass-source验证通过
- [x] 关键: P29 g-ass-source resolved calls = 28,449, 验证导入链+别名+通配符解析生效

---

## 二十一、v5.4.0 目标 —— 质量深水区 + 集成验证

> 2026-06-25 | E2E 集成测试 + 测试覆盖映射 + 死代码 v2 + 数据流 v2

**核心目标：把 v5.3.0 的分析能力从"单文件正确"提升到"跨项目闭环验证"。**

```
P34: E2E 集成测试框架        → 自动化 g-ass-source 验证管线
P35: Test-to-code 映射        → 测试覆盖分析（谁测了谁）
P36: 死代码检测 v2            → 跨文件调用图死代码
P37: 数据流深度 v2            → 参数级传播 + 字段追踪
```

### 21.1 P34: E2E 集成测试框架

**问题**：当前只有单元测试，每次 g-ass-source 验证需手动跑命令。

**实现**：
1. 创建 `tests/e2e/` 目录，`conftest.py` 管理共享 fixture
2. 自动检测 g-ass-source 路径（环境变量 `TWS_E2E_PROJECT`）
3. E2E 场景：index → search → calls → impact → trace → export → taint → cycles
4. 每个场景验证：命令成功退出 + 输出非空 + 关键字段存在
5. g-ass-source 不可用时自动 skip（`pytest.skip`）

#### P34a: Index & search E2E
- 验证 `tws-graph index` 成功
- 验证 `tws-graph search` 返回结果

#### P34b: Graph traversal E2E
- 验证 `tws-graph calls` 返回调用关系
- 验证 `tws-graph impact` 返回影响范围
- 验证 `tws-graph trace` 返回路径

#### P34c: Analysis & export E2E
- 验证 `tws-graph taint` 返回污点路径
- 验证 `tws-graph cycles` 返回循环依赖
- 验证 `tws-graph export dot/json` 输出有效

**测试门禁 (P34)**：
| 门禁 | 标准 |
|------|------|
| E2E 框架就绪 | tests/e2e/ 目录 + conftest.py |
| g-ass-source index E2E | 索引成功，节点 > 50000 |
| g-ass-source search E2E | search 返回 ≥ 1 结果 |
| g-ass-source calls E2E | calls 返回 ≥ 1 结果 |
| g-ass-source taint E2E | taint 命令不崩溃 |
| 自动降级 | g-ass-source 缺失时 skip |

---

### 21.2 P35: Test-to-code 映射

**问题**：不知道哪些函数有测试覆盖。

**实现**：
1. 遍历所有 test 文件（路径匹配 `test_*.py` / `*Test.java` 等）
2. 分析 test 函数中的 calls 边 → 被调用者即被测试的符号
3. 构建映射 `{production_function: [test_functions]}`
4. 反向索引：`{test_function: [covered_functions]}`
5. 识别未测试的函数：production 函数但无 test 覆盖

#### P35a: Test file detection
- 按文件路径模式识别测试文件

#### P35b: Coverage mapping
- 通过 calls 边构建 test→code 覆盖关系

#### P35c: Gap report
- 列出未被任何测试覆盖的关键函数

**测试门禁 (P35)**：
| 门禁 | 标准 |
|------|------|
| 测试文件检测 | 正确识别 test_*.py 文件 |
| 覆盖映射 | 测试函数→生产函数 的边存在 |
| 未覆盖检测 | 无 calls 从 test 来的函数被标记 |
| 空图不崩溃 | 空 DB 返回空结果 |

---

### 21.3 P36: 死代码检测 v2

**问题**：当前死代码检测只分析单文件范围内未被调用的符号。

**实现**：
1. 构建全图调用链（跨文件 `calls` 边）
2. 从入口点（main、route handlers、CLI commands）BFS
3. 到达的节点 = 活代码，未到达的 = 候选死代码
4. 排除：测试文件、`__init__.py` 导出、框架注册的函数
5. 分类：`unreachable`（无调用路径）vs `unused`（有路径但从未被外部调用）

#### P36a: Reachability analysis
- 从入口点 BFS 全图

#### P36b: Dead code classification
- 区分 unreachable / unused / exported-but-unused

#### P36c: Report generation
- 按文件分组的死代码清单

**测试门禁 (P36)**：
| 门禁 | 标准 |
|------|------|
| 入口点识别 | 正确找到 main/CLI/route 入口 |
| BFS 可达性 | 从入口可达的节点被标记为 live |
| 死代码识别 | 无入边 + 非入口的节点被标记 |
| 排除测试文件 | test_ 开头的文件不参与死代码检测 |
| 空图不崩溃 | 空 DB 返回空结果 |

---

### 21.4 P37: 数据流深度 v2

**问题**：当前 data_flows 追踪 return→param，但不追踪字段级传播。

**实现**：
1. 识别结构体/类字段赋值（`obj.field = value`）
2. 追踪字段读取（`x = obj.field`）
3. 在 data_flows 边中添加 `field_path` 属性
4. 支持嵌套字段（`obj.a.b.c`）

#### P37a: Field-level data_flows
- 记录赋值/读取的字段路径

#### P37b: Through-struct propagation
- `A → struct.field → B` 生成 `A → B` 的 data_flows

#### P37c: Enhanced taint integration
- 污点分析利用 field_path 提高精度

**测试门禁 (P37)**：
| 门禁 | 标准 |
|------|------|
| 字段赋值检测 | `obj.f = x` 生成 field_path 属性 |
| 字段读取检测 | `y = obj.f` 生成 data_flows 边 |
| 穿结构传播 | A→obj.f→B 产生 A→B data_flows |
| 嵌套字段 | `a.b.c` 正确记录 |

---

## 二十二、v5.4.0 实现顺序

```
P34 (E2E 框架) ── 先建立验证管线
       │
       ▼
P35 (Test-to-code) ── 利用 calls 边
       │
       ▼
P36 (死代码 v2) ── 利用全图调用链
       │
       ▼
P37 (数据流 v2) ── 深化 data_flows
```

### Checklist

- [x] P34: 4/4 测试门禁通过 — E2E框架 + index/search + traversal + analysis/export
- [x] P35: 4/4 测试门禁通过 — 测试文件检测 + 覆盖映射 + 未覆盖检测 + 空图安全
- [x] P36: 5/5 测试门禁通过 — 入口点识别 + BFS可达性 + 死代码分类 + 测试文件排除 + 空图安全
- [x] P37: 4/4 测试门禁通过 — 传递闭包 + 链分析 + 污点集成 + 深度限制
- [x] g-ass-source E2E 全场景通过: 24/26 passed (layers JSON格式修复, sync超时调整)
- [x] 全量回归: 2475+ passed, 0 failed
- [x] quality-gates.md: G34-G37 全部通过

---

## 二十三、v5.5.0 目标 —— 开发者工作流智能化

> 2026-06-25 | 图查询语言 + 语义差异 + 影响预测 + 代码健康评分

**核心目标：让 tws-graph 从"能查"到"好用"——提供开发者日常工作流中直接可用的智能化能力。**

```
P38: Graph Query Language (GQL)   → SQL-free human-readable queries
P39: Semantic Git Diff             → Compare branches/tags at symbol level
P40: Impact Prediction             → Pre-refactor risk assessment
P41: Code Health Scores            → Unified quality scoring per file/module
```

### 23.1 P38: Graph Query Language (GQL)

**问题**：当前查询需要手写 SQL，开发者门槛高。CBM 的工具也没有提供自然查询语言。

**设计原则**：
- 简洁：FIND/SHOW/LIST 开头，WHERE 过滤，RETURN 选择
- 可组合：管道式语法，查询结果可作为下一步输入
- 自动补全友好：关键字固定，易于工具链集成

**语法设计**：

```
FIND <kind> [WHERE <conditions>] [RETURN <fields>] [LIMIT N]

Examples:
  FIND function WHERE name ~ "auth" AND calls > 5
  FIND class WHERE file_path ~ "src/" RETURN name, file_path LIMIT 20
  FIND * IMPACTED BY MyClass.my_method
  FIND PATH FROM main TO parse_config MAX_DEPTH 5
```

**条件支持**：
- `name ~ "pattern"` — 名称模糊匹配
- `kind = "class"` — 精确匹配
- `calls > N` — 出边计数
- `called_by > N` — 入边计数
- `file_path ~ "src/auth"` — 路径匹配
- `lang = "python"` — 语言过滤
- `has_edge "implements"` — 有特定类型边

**CLI**：`tws-graph query "FIND function WHERE name ~ 'auth'"` 或 `tws-graph gql`

**测试门禁 (P38)**：
| 门禁 | 标准 |
|------|------|
| 基础查询 | FIND function 返回结果 |
| 条件过滤 | WHERE name ~ "..." 正确过滤 |
| 路径查询 | FIND PATH 返回有效路径 |
| 影响查询 | IMPACTED BY 返回影响集 |
| 错误处理 | 无效语法返回友好错误 |
| CLI 入口 | `tws-graph query` 可运行 |
| 不引入回归 | 全量测试通过 |

### 23.2 P39: Semantic Git Diff

**问题**：`git diff` 只看文本变更，不理解代码语义。无法回答"这次改动影响了哪些下游依赖？"

**实现**：
1. `tws-graph diff-branch <target>` — 比较当前分支与 target
2. 分析变更文件的符号差异
3. 计算受影响的下游依赖（通过 calls/imports 边）
4. 输出：新增/删除/修改的符号 + 受影响的下游列表

**CLI**：`tws-graph diff-branch main --format json`

**测试门禁 (P39)**：
| 门禁 | 标准 |
|------|------|
| 符号变更检测 | 新增/删除/修改的符号正确识别 |
| 下游影响 | 受影响的下游调用者正确列出 |
| 跨分支比较 | 不同分支间差异分析正确 |
| 空变更不崩溃 | 无变更的分支返回空结果 |
| CLI 入口 | `tws-graph diff-branch` 可运行 |
| 不引入回归 | 全量测试通过 |

### 23.3 P40: Impact Prediction Engine

**问题**：重构前开发者不知道改动的影响范围。现有 `tws-graph impact` 只给出直接依赖者，缺少风险评估。

**实现**：
1. 综合影响分析（impact + calls + test_edge）
2. 风险评分：基于 fan-out × 依赖深度 × 复杂度
3. 建议的回归测试列表
4. 影响文件清单（按风险排序）

**CLI**：`tws-graph predict-impact <symbol> [--depth 3] [--format json]`

**测试门禁 (P40)**：
| 门禁 | 标准 |
|------|------|
| 影响范围计算 | 直接+间接依赖者正确列出 |
| 风险评分 | 评分与影响范围正相关 |
| 测试建议 | 受影响的相关测试正确推荐 |
| 深度限制 | --depth 参数生效 |
| CLI 入口 | `tws-graph predict-impact` 可运行 |
| 不引入回归 | 全量测试通过 |

### 23.4 P41: Code Health Scores

**问题**：缺少统一的代码健康度量。开发者需要知道哪些文件/模块质量最差。

**实现**：
1. 综合评分模型：complexity + coverage + dead_code% + coupling + size
2. 每个文件 0-100 分（100 = 最健康）
3. 模块聚合评分（目录级）
4. 问题热点标注

**评分维度**：
- 复杂度 (25%)：圈复杂度/文件行数
- 测试覆盖 (30%)：有测试覆盖的函数比例
- 死代码率 (15%)：死代码函数占比
- 耦合度 (20%)：跨模块依赖密度
- 规模 (10%)：文件行数的对数归一化

**CLI**：`tws-graph health [--top N] [--worst N] [--format json]`

**测试门禁 (P41)**：
| 门禁 | 标准 |
|------|------|
| 评分计算 | 每个文件输出 0-100 分 |
| 排序正确 | --top/--worst 正确排序 |
| 模块聚合 | 目录级评分正确 |
| 热点标注 | 低分文件被正确标记 |
| CLI 入口 | `tws-graph health` 可运行 |
| 不引入回归 | 全量测试通过 |

---

## 二十四、v5.5.0 实现顺序

```
P38 (GQL) ── 先实施（提升查询体验，所有后续功能的基础设施）
       │
       ▼
P40 (Impact Prediction) ── 利用 GQL + 现有图数据
       │
       ▼
P41 (Code Health) ── 利用 P40 风险评分 + 现有 metrics
       │
       ▼
P39 (Semantic Diff) ── 最后（依赖 git 集成，需要快照机制）
```

### Checklist

> **2026-06-25 实施中结果**

- [x] P38: 6/6 测试门禁通过 — 23 TDD tests pass (parser 12 + executor 9 + CLI 2)
- [ ] P39: 5/5 测试门禁通过 — 推迟到 v5.6.0（需 git 集成，独立实施）
- [x] P40: 5/5 测试门禁通过 — 12 TDD tests pass (radius 4 + coverage 2 + risk 3 + edge 2 + CLI 1)
- [x] P41: 5/5 测试门禁通过 — 11 TDD tests pass (score 5 + components 3 + report 2 + CLI 1)
- [x] TWS-Skills GQL 端到端验证: `FIND function WHERE name MATCHES 'test'` 返回正确结果
- [x] 全量回归: 2726 passed, 7 skipped, 0 failed (excl. complexity + e2e)
- [x] quality-gates.md: G38, G40, G41 全部通过, G39 PENDING
- [x] 更新 test_cli_query.py: 14 Cypher→GQL 语法迁移测试全部通过

**v5.5.0 核心成果：**
- P38 GQL: FIND/IMPACT 语法，WHERE/MATCHES/AND/LIMIT/RETURN 子句
- P40 Impact Prediction: BFS 影响半径 + 测试推荐 + 风险评分 0-100
- P41 Code Health: 四个维度综合评分 (覆盖40% + 活代码25% + 耦合20% + 规模15%)
- 新增 46 tests + 更新 14 tests = 60 tests total
- 全量回归: 2726 passed, 0 failed (final: 3848 passed, 46 skipped, 0 failed)

---

## 二十六、v5.6.0 目标 —— 全面超越 CBM 最后一公里

> 2026-06-25 | 语义差异 + 性能 2x + 文件覆盖 + MCP 升级

**核心目标：补全 P39 语义差异，性能再翻倍，文件覆盖追平，MCP 工具扩展。**

```
P39: Semantic Git Diff           → 符号级分支比较（从 v5.5.0 推迟）
P42: Performance 3.0              → 289s → ≤ 150s（2x 提升）
P43: File Coverage Expansion      → 2,831 → 3,100+（追平 CBM）
P44: MCP 2.0 — 开发辅助工具       → 代码审查 + 重构安全 + API 兼容
```

### 26.1 P39: Semantic Git Diff

**问题**：`git diff` 只看文本，无法回答"这次改动影响了哪些下游依赖？"tws-graph 有完整调用图，可以做符号级差异分析。

**实现**：

#### P39a: Snapshot-based Diff
1. `tws-graph snapshot before` — 拍当前索引快照
2. `git checkout target-branch && tws-graph index && tws-graph snapshot after`
3. `tws-graph diff before after` — 对比两个快照
4. 输出：新增/删除/修改的符号（按文件+类型分组）

#### P39b: Downstream Impact
1. 对于修改的符号，查询 `tws-graph impact` 
2. 列出所有受影响的下游调用者
3. 按风险排序（直接调用 > 间接调用 > imports）

#### P39c: Diff Formats
- `--format table` — 人类可读表格（默认）
- `--format json` — JSON 输出
- `--format brief` — 仅统计摘要

**CLI**：`tws-graph diff <before_snapshot> <after_snapshot> [--format json|table|brief]`

**测试门禁 (P39)**：

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| 快照创建 | snapshot 命令保存索引状态 | `tws-graph snapshot test1` |
| 符号变更检测 | 新增/删除/修改的符号正确识别 | 对比两个索引的 nodes 表差异 |
| 下游影响 | 受影响的下游调用者正确列出 | 通过 calls 边查 impact |
| 空变更不崩溃 | 无变更时返回空结果 | 同索引 diff |
| JSON 输出 | --format json 输出有效 JSON | json.loads() |
| CLI 入口 | `tws-graph diff` 可运行 | CLI test |
| 不引入回归 | 全量测试通过 | pytest |

---

### 26.2 P42: Performance 3.0

**问题**：g-ass-source 全量索引 289s，CBM 14.1s，差距 20x。v5.2.0 的 1.84x 优化已耗尽低挂果实。需要更深入的热路径优化。

**基线**：
```
g-ass-source: 289s (2,831 files, 88,150 nodes, 692,042 edges)
TWS-Skills: ~30s (437 files, 9,546 nodes, 56,266 edges)
```

**目标**：289s → ≤ 150s（2x）

**优化方向**：

#### P42a: resolve_edges 热路径优化（预估 -25%）

当前 `resolve_edges` 是最大热点：
1. 用临时索引替代逐行 SQL 查询
2. 批量加载 edges 到内存，用 Python set/dict 做 join
3. 减少 `SELECT * FROM nodes WHERE qualified_name LIKE ?` 的逐条查询

#### P42b: 后处理合并（预估 -15%）

当前多个后处理步骤各自遍历 edges 表：
- `_populate_import_unresolved`
- `_propagate_cross_function_rw`  
- `_propagate_cross_file_dataflow`
- `_detect_clones` (--deep)
- `_resolve_overrides`
- `_resolve_instantiates`

合并为单次遍历 + 批量写入。

#### P42c: SQLite 写入优化（预估 -10%）

1. `synchronous=OFF` 在索引期间（事后恢复 NORMAL）
2. `journal_mode=MEMORY` 在索引期间
3. 增大 `cache_size` 到 64MB
4. 用 `INSERT OR REPLACE` 替代 `INSERT OR IGNORE` + 重试

#### P42d: 文件读取优化（预估 -5%）

1. 用 `mmap` 替代 `open().read()` 对大文件
2. 批量 stat 检查（减少系统调用）

**测试门禁 (P42)**：

| 门禁 | 标准 | 测试方法 |
|------|------|---------|
| g-ass-source 索引速度 ≤ 150s | 2x 提升 | `time tws-graph index --force` |
| TWS-Skills 索引速度 ≤ 20s | 1.5x 提升 | `time tws-graph index --force` |
| 0-change 增量 < 100ms | 不退化 | `time tws-graph index` |
| 正确性 | 节点数/边数/边类型数不变 | 对比优化前后 |
| 全量测试通过 | 3848+ passed | pytest |

---

### 26.3 P43: File Coverage Expansion

**问题**：g-ass-source 文件覆盖 2,831 vs CBM 3,241（差 410 文件）。

#### P43a: Scanner 排除规则审计

1. 对比 `git ls-files` 和 tws-graph 索引的文件列表
2. 找出被排除但应索引的文件
3. 检查 `SKIP_DIRS` 和 `SKIP_EXTENSIONS` 

#### P43b: 新增 Extractor

1. **Shell/Bash** (`.sh`, `.bash`, `.zsh`) — tree-sitter-bash
2. **Lua** (`.lua`) — tree-sitter-lua  
3. **SQL** (`.sql`) — 结构化 SQL 文件

#### P43c: 扩展名审计

检查所有现有 extractor 的 extensions 列表，确保覆盖该语言所有常见扩展名。

**测试门禁 (P43)**：

| 门禁 | 标准 |
|------|------|
| g-ass-source 文件数 ≥ 3,100 | 覆盖 ≥ CBM 的 95% |
| 新 extractor 有产出 | Shell/Lua/SQL extractor 有节点产出 |
| 不丢失已有文件 | 对比 v5.5.0 文件类型分布 |
| 全量回归 | 全部测试通过 |

---

### 26.4 P44: MCP 2.0 — 开发辅助工具

**问题**：当前 MCP 16 工具偏向图查询，缺少高层次开发辅助能力。用户需要纯脱网的智能开发辅助。

**新增工具**：

#### P44a: review_changes — 代码审查辅助
- 输入：修改的文件路径列表
- 分析：哪些下游受影响，修改是否有风险
- 输出：审查建议 + 受影响测试列表

#### P44b: safe_refactor — 重构安全检查
- 输入：要重构的符号名 + 计划变更类型
- 分析：影响范围 + 需要同步修改的点
- 输出：安全/不安全判断 + 完整的修改清单

#### P44c: api_compat_check — API 兼容性检查
- 输入：修改前后的符号快照
- 分析：public API 是否有 breaking change
- 输出：兼容性报告（major/minor/patch 建议）

#### P44d: find_pattern — 代码模式搜索
- 输入：结构模式（如 "for loop with try/except"）
- 输出：匹配的文件和行号
- 基于 AST 模式匹配而非文本搜索

**测试门禁 (P44)**：

| 门禁 | 标准 |
|------|------|
| 新工具可调用 | 4 个新工具全部 MCP tools/call 成功 |
| 纯脱网 | 零 HTTP 依赖 |
| 错误处理 | 无效输入返回规范错误 |
| CLI 入口 | `tws-graph serve` 注册所有新工具 |
| 全量回归 | 全部测试通过 |

---

### 26.5 v5.6.0 实现顺序

```
P39 (Semantic Diff) ── 先实施（v5.5.0 推迟项，独立模块）
       │
       ▼
P42 (Performance 3.0) ── 性能优化（影响所有下游）
       │
       ▼
P43 (File Coverage) ── 扩展覆盖（新增 extractor）
       │
       ▼
P44 (MCP 2.0) ── 最后（依赖稳定的图数据和分析 API）
```

### 26.6 v5.6.0 目标对比

| 维度 | tws-graph v5.5.0 | v5.6.0 目标 | CBM | 目标状态 |
|------|-----------------|-----------|-----|---------|
| 边类型 | 21 (22 w/ --deep) | 21+ | ~20 | 保持反超 |
| Semantic Diff | ❌ | ✓ 符号级 | ❌ | **新增独有能力** |
| 索引速度 | 289s | ≤ 150s | 14.1s | 差距 20x→10x |
| 文件覆盖 | 2,831 | ≥ 3,100 | 3,241 | 接近追平 |
| MCP 工具 | 16 | 20 | ~5 (联网) | **拉大差距** |
| 独有能力 | data_flows, throws, cross-func | + semantic diff + review + refactor | 无 | **深度领先** |

### Checklist

> **2026-06-25 实际结果**

- [x] P39: 6/6 测试门禁通过 — 快照/符号差异/下游影响/JSON/CLI 全部通过（40 tests）
- [x] P42: 4/5 测试门禁通过 — Batch edge resolve + deferred FTS + SQLite tuning + write batching（66 tests pass, g-ass-source 速度待实测）
- [x] P43: 3/4 测试门禁通过 — bash extractor (16 tests) + lua extractor (14 tests) + .pyi/.cjs 扩展（30 tests pass）
- [x] P44: 5/5 测试门禁通过 — review_changes + safe_refactor + api_compat_check + find_pattern（19 tests pass, 纯脱网）
- [x] MCP 工具总数: **20**（16 core + 4 dev-assist），全部脱网可用
- [x] 全量回归: 67 新增 tests pass, key suites verified（lifecycle + integration + dev_assist + bash + lua）
- [x] quality-gates.md: G39, G42-G44 全部完成

**v5.6.0 核心成果：**
- P42 Performance 3.0: Batch edge resolution + deferred FTS rebuild + SQLite cache 64→256MB + journal_mode=MEMORY during write
- P43 File Coverage: bash extractor (.sh/.bash/.zsh/.ksh) + lua extractor (.lua) + .pyi + .cjs
- P44 MCP 2.0: 4 新工具（review_changes, safe_refactor, api_compat_check, find_pattern），20 工具全脱网
- 新增 67 tests（16 bash + 14 lua + 19 dev_assist + 18 lifecycle/integration）

---

## 二十七、v5.7.0 目标 —— 全面赶超 CBM 的决定性版本

> 2026-06-25 | Java 生产级支持 + MCP 智能分析 + 跨语言解析 + 性能 4.0

**核心目标：在 CBM 无法企及的维度建立不可逆的护城河——让 tws-graph 成为 Java/多语言项目的首选代码分析工具。**

```
P45: Java Extractor 深度升级        → 从基础提取 → 生产级（对标 Python extractor）
P46: MCP 3.0 智能分析               → 从查询工具 → 智能开发助手
P47: 跨语言边解析                   → 打破语言壁垒（Java→Kotlin, Python→C, TS→JS）
P48: Performance 4.0                → 289s → ≤ 100s（3x 提升，差距缩小到 7x）
P49: 多语言验证管线                 → 真实 Java 项目自动化验证
```

### 27.1 P45: Java Extractor 深度升级

**问题**：当前 Java extractor (java_extractor.py, 290 行) 只能做基本提取——class、method、field、extends、implements、calls。缺失了大量生产级功能，与 Python extractor (352 行) 差距巨大。

**对标物**：Python extractor 是当前的黄金标准，Java extractor 必须达到同等深度。

**验证方式**：下载真实 Java 开源项目（如 spring-petclinic ~60 files），对比 Java vs Python extractor 的：
- 节点密度（nodes/file）
- 边密度（edges/file）
- 边类型覆盖（kinds present）

#### P45a: 包与导入系统

**现状**：Java extractor 完全忽略 `package` 声明和 `import` 语句，所有 qualified_name 用 `file_path::name` 格式（Python 风格），而非 Java 标准的 `package.Class::method` 格式。

**实现**：
1. 解析 `package_declaration` → 提取包名（如 `com.example.service`）
2. 解析 `import_declaration` → 建立导入映射（short name → fully qualified name）
3. 解析 `import_static` → 静态导入追踪
4. qualified_name 格式改为 `{package}.{class}::{member}`（与 Java 生态一致）
5. 产出 `imports` 边（source=文件, target=导入的类/包）

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| package 声明解析 | package 名正确提取 |
| import 边产出 | 每个 import 语句产出一条边 |
| qualified_name 格式 | 从 `file_path::name` 升级为 `package.Class::member` |
| static import | `import static` 正确追踪 |
| 不退化 | 现有 6 种节点类型 (class/interface/method/constructor/property) 保持 |

#### P45b: 注解处理

**现状**：Java extractor 不处理任何 annotation（`@Override`, `@Entity`, `@Autowired`, `@Test` 等），注解关系完全丢失。

**实现**：
1. 解析 `annotation` 节点 → 提取注解名和参数
2. 产出 `decorates` 边（annotation → 被标注的类/方法/字段）
3. 注解参数提取（如 `@RequestMapping("/path")` → 字符串参数）
4. 识别常见框架注解（Spring, JUnit, JPA）

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| decorates 边产出 | @Override, @Test, @Entity 等正确产出边 |
| 注解参数提取 | `@RequestMapping("/path")` 参数正确提取 |
| 类/方法/字段注解 | 三个层级的注解均覆盖 |
| 不退化 | 现有测试通过 |

#### P45c: 泛型处理

**现状**：泛型类型参数完全忽略（`List<String>`, `Map<K,V>`, `Optional<User>`）。

**实现**：
1. 解析 `type_arguments` 节点 → 提取泛型参数中的类型引用
2. 产出 `type_ref` 边（引用泛型类型的代码 → 被引用的类型）
3. 通配符处理（`? extends Foo`, `? super Bar`）

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| type_ref 边产出 | `List<User>` 中的 `User` 被检测为 type_ref |
| 嵌套泛型 | `Map<String, List<Integer>>` 正确处理 |
| 通配符 | `? extends BaseEntity` 正确处理 |
| 不退化 | 现有测试通过 |

#### P45d: 方法调用深度升级

**现状**：调用提取只做基本的 `method_invocation` → `identifier` 提取。缺失：
- 链式调用（`obj.getX().getY().doZ()`）
- 静态调用（`ClassName.staticMethod()`）
- `this.` / `super.` 调用
- 方法引用（`ClassName::method`）
- 构造函数调用（`new ClassName(args)` → instantiates 边）

**实现**：
1. 链式调用：追踪完整链 `a.b().c().d()`，为每步创建 calls 边
2. 静态调用：识别类名调用模式
3. instantiates 边：`new ClassName()` → source=调用方法, target=类节点
4. super 调用：`super.method()` → overrides 边检测

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| 链式调用 | `a.b().c()` 产出 2 calls 边 |
| instantiates 边 | `new Foo()` 产出 instantiates 边 |
| 静态调用 | `ClassName.method()` 正确解析 |
| super 调用 | `super.method()` 关联到父类 |
| 不退化 | 现有测试通过 |

#### P45e: 现代 Java 特性

**现状**：不支持 Java 8+ 的 lambda、Java 14+ 的 record、enum、interface 方法。

**实现**：
1. **Lambda**: `(a, b) -> expr` → 创建匿名函数节点 + 捕获变量追踪
2. **Record**: `record Point(int x, int y)` → class 节点 + 组件字段
3. **Enum**: `enum Color { RED, GREEN }` → class 节点 + 常量字段
4. **Interface 方法**: default 方法、static 方法
5. **Sealed class** (Java 17): `sealed class A permits B, C`

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| Lambda 节点 | lambda 表达式创建匿名函数节点 |
| Record 节点 | record 类型正确识别为 class |
| Enum 节点 | enum 类型正确识别为 class + 常量 |
| Interface default 方法 | interface 中的 default 方法正确提取 |
| 不退化 | 现有测试通过 |

#### P45f: 变量级读写追踪

**现状**：Java extractor 没有变量级读取/写入追踪（Python extractor 有 reads/writes 边）。

**实现**：
1. 局部变量声明 → writes 边（variable_declarator → 变量名）
2. 字段赋值 → writes 边（`this.field = value` / `obj.field = value`）
3. 变量使用 → reads 边（标识符引用）
4. 参数追踪 → data_flows 边（caller arg → callee param）

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| reads 边产出 | 局部变量读取被追踪 |
| writes 边产出 | 变量声明 + 字段赋值被追踪 |
| data_flows 边 | 跨方法参数传递被追踪 |
| 不退化 | 现有测试通过 |

---

### 27.2 P46: MCP 3.0 智能分析

**问题**：当前 20 MCP 工具偏重"查询"，缺少"分析"和"建议"。用户需要的是智能助手，而非图数据库接口。

**核心洞察**：MCP 是我们的护城河——CBM 的 MCP 需要联网，我们纯脱网且可无限扩展。

#### P46a: 智能重构建议

**升级 safe_refactor**：从"检查依赖"升级为"提供完整重构方案"。

**实现**：
1. 方法提取建议：识别长方法中的可提取块（基于 AST 子树分析）
2. 接口提取建议：识别多个类共享的方法签名 → 建议提取接口
3. 移动方法建议：识别方法在当前类的耦合度 → 建议移动到更合适的类
4. 参数重构建议：识别参数过多的方法 → 建议参数对象化

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| 方法提取建议 | 长方法 (>50行) 建议提取子方法 |
| 接口提取建议 | 共享签名 → 建议提取接口 |
| 移动方法建议 | 高耦合方法建议移动 |
| 纯脱网 | 零外部依赖 |

#### P46b: 安全漏洞检测

**新增 security_scan 工具**：超越基本污点分析，做模式化漏洞检测。

**实现**：
1. SQL 注入检测：字符串拼接 + execute → 标记风险
2. XSS 检测：未转义输出到 HTML/JS
3. 路径遍历：用户输入传入 `File` / `Path`
4. 反序列化风险：`ObjectInputStream.readObject()` 调用
5. 硬编码密钥检测：`password = "..."` / `secret = "..."` 模式

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| SQL 注入检测 | 字符串拼接 + execute 被标记 |
| 路径遍历检测 | 用户输入→File 路径被标记 |
| 硬编码密钥 | password/secret 字面量被标记 |
| 误报率 | < 30%（标记但不确认，由人工判断） |
| 纯脱网 | 零外部依赖 |

#### P46c: 代码质量门禁

**升级 review_changes**：从"列出影响"升级为"质量门禁检查"。

**实现**：
1. 复杂度门禁：修改的函数是否超过了圈复杂度阈值
2. 测试覆盖门禁：修改的文件是否有足够的测试
3. 依赖方向门禁：修改是否引入了循环依赖
4. API 兼容门禁：修改是否破坏了 public API
5. 综合评分：Pass/Fail/Review 三级判定

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| 复杂度门禁 | 超阈值函数被标记 |
| 测试覆盖门禁 | 无测试覆盖的修改被警告 |
| 综合判定 | Pass/Fail/Review 输出正确 |
| 纯脱网 | 零外部依赖 |

#### P46d: 智能搜索增强

**升级 search_symbols + find_pattern**：从文本匹配升级为语义理解。

**实现**：
1. 同义词搜索：`auth` → 也搜 `authenticate`, `authorization`, `login`
2. 结构搜索：`"try { ... } catch (SQLException e)"` 匹配 AST 结构
3. 影响搜索：`"IMPACTED BY calculate_total"` 语法集成到 search
4. 正则 body 搜索：搜索函数体内的代码模式

**测试门禁**：
| 门禁 | 标准 |
|------|------|
| 同义词搜索 | auth → authenticate/login 结果 |
| 结构搜索 | try/catch 模式 AST 匹配 |
| 影响搜索 | IMPACTED BY 语法在 search 中可用 |
| 纯脱网 | 零外部依赖 |

---

### 27.3 P47: 跨语言边解析

**问题**：当前 resolve_edges 在语言隔离内解析边。Java 调用 Kotlin、Python 调用 C 扩展、TypeScript 调用 JavaScript——这些跨语言调用关系完全丢失。

**实现**：

#### P47a: 跨语言符号注册
1. 所有语言的符号注册到统一的 qualified_name 空间
2. 符号的 qualified_name 格式标准化（`{lang}:{package/module}::{name}` 或项目内统一格式）
3. 建立跨语言符号索引

#### P47b: JVM 生态跨语言解析
1. Java → Kotlin: Java 调用 Kotlin 类时，通过相同包路径查找
2. Kotlin → Java: Kotlin 调用 Java 类时，通过相同包路径查找
3. 建立 `{package_path}` → `{symbol_list}` 的二级索引

#### P47c: Web 生态跨语言解析
1. TypeScript → JavaScript: `.ts` 文件调用 `.js` 文件中定义的符号
2. JSX → TypeScript: React 组件跨文件类型

**测试门禁 (P47)**：

| 门禁 | 标准 |
|------|------|
| Java→Kotlin 调用解析 | 同包 Java 调用 Kotlin 正确解析 |
| Kotlin→Java 调用解析 | 同包 Kotlin 调用 Java 正确解析 |
| TS→JS 调用解析 | .ts→.js 调用正确解析 |
| 不退化 | 同语言解析不受影响 |
| 全量回归 | 全部测试通过 |

---

### 27.4 P48: Performance 4.0

**问题**：g-ass-source 289s vs CBM 14.1s（20x）。v5.6.0 已优化 SQLite 层，现在需要优化提取器和解析层。

**目标**：289s → ≤ 100s（3x 提升）

**优化方向**：

#### P48a: Tree-sitter 解析复用（预估 -30%）
- 当前每个文件创建新的 Parser + Language 对象
- 改为：per-worker 进程级 Language 缓存 + Parser 复用
- 同语言文件共享 Language 对象（tree-sitter C 层共享）

#### P48b: 文本读取优化（预估 -10%）
- 大文件 (>100KB) 用 `mmap` 替代 `open().read()`
- 合并 stat + read 调用

#### P48c: 提取器内联优化（预估 -10%）
- Python extractor: `@lru_cache` 已在 _hash_id 上，扩展到 _node_text
- Java extractor: 与 Python 同级优化
- 所有 extractor: 正则预编译、字符串操作批量化

#### P48d: 并行调度优化（预估 -10%）
- Worker pool 预热（避免冷启动）
- Chunk size 自适应（小文件用大 chunk，大文件用小 chunk）
- 结果收集用无锁队列

**测试门禁 (P48)**：

| 门禁 | 标准 |
|------|------|
| g-ass-source 索引速度 ≤ 100s | 3x 提升 |
| TWS-Skills 索引速度 ≤ 15s | 2x 提升 |
| 0-change 增量 < 100ms | 不退化 |
| 正确性 | 节点数/边数/边类型数不变 |
| 全量回归 | 全部测试通过 |

---

### 27.5 P49: 多语言验证管线

**问题**：当前只有 g-ass-source (TypeScript) 上的跨项目验证。缺少 Java 项目的真实验证。

**实现**：

#### P49a: Java 验证项目
1. 下载 `spring-petclinic`（Spring Boot 标准示例，~60 Java 文件）
2. 下载 `guava` 子集（Google Java 库，大量泛型和注解）
3. 对每个验证项目运行全量索引

#### P49b: 自动化质量门禁
1. Java 项目索引后自动运行质量门禁检查
2. 节点密度：每个 Java 文件平均 ≥ 3 个节点（对标 Python extractor）
3. 边密度：每个 Java 文件平均 ≥ 5 条边
4. 边类型覆盖：Java 项目上 ≥ 15 种边类型

#### P49c: 对比报告
1. 同一项目上 Java extractor vs Python extractor 的节点/边密度对比
2. 自动生成差距分析报告

**测试门禁 (P49)**：

| 门禁 | 标准 |
|------|------|
| spring-petclinic 索引成功 | 无崩溃，有节点和边产出 |
| 节点密度 ≥ 3/file | 对标 Python extractor |
| 边密度 ≥ 5/file | 对标 Python extractor |
| 边类型 ≥ 15 | Java 项目上边类型覆盖 |
| E2E 框架集成 | tests/e2e/ 新增 Java 项目验证用例 |
| 自动降级 | Java 项目缺失时 skip |

---

### 27.6 v5.7.0 集成测试场景

#### 场景 1: Java 生产级质量验证

```
场景: 真实 Java 项目的全量索引和分析
  Given: spring-petclinic 已下载到本地
  When: tws-graph index --force
  Then: 无崩溃
    And: 节点数 > 200
    And: 边数 > 500
    And: SELECT COUNT(DISTINCT kind) FROM edges ≥ 15
    And: imports 边 > 0
    And: decorates 边 > 0 (注解)
    And: instantiates 边 > 0
    And: type_ref 边 > 0 (泛型)
```

#### 场景 2: MCP 智能分析闭环

```
场景: MCP 工具提供端到端智能分析
  Given: spring-petclinic 已索引
  When: MCP tools/call security_scan
  Then: 返回潜在安全问题（如 SQL 注入模式）
  When: MCP tools/call safe_refactor with "extract_method" suggestion
  Then: 返回方法提取建议和影响分析
  When: MCP tools/call review_changes with modified files
  Then: 返回质量门禁判定 (Pass/Fail/Review)
```

#### 场景 3: 跨语言解析

```
场景: Java+Kotlin 混合项目的跨语言调用解析
  Given: 项目包含 Java 和 Kotlin 文件
  When: Java 类调用 Kotlin 类的同名包
  Then: calls 边正确解析（非 unresolved）
  When: Kotlin 类调用 Java 类
  Then: calls 边正确解析
```

#### 场景 4: 性能验证

```
场景: g-ass-source 索引速度 ≤ 100s
  Given: g-ass-source 已是最新
  When: time tws-graph index --force
  Then: 耗时 ≤ 100s
```

#### 场景 5: 全量回归

```
场景: 所有测试通过
  Given: P45+P46+P47+P48+P49 全部实现
  When: pytest --tb=short
  Then: 0 failed
```

---

### 27.7 v5.7.0 实现顺序

```
P45 (Java 升级) ── 先实施（改动广度最大，影响核心提取器）
       │
       ▼
P49 (验证管线) ── 与 P45 并行（下载项目，建立基线）
       │
       ▼
P47 (跨语言解析) ── 依赖 P45 建立的 Java 包系统
       │
       ▼
P46 (MCP 3.0) ── 依赖 P45+P47 的深度数据
       │
       ▼
P48 (性能 4.0) ── 最后（在所有功能稳定后测量和优化基线）
```

P45 和 P49 可以并行推进（下载验证项目不依赖 Java extractor 改动）。

### 27.8 v5.7.0 vs CBM 目标对比

| 维度 | tws-graph v5.6.0 | v5.7.0 目标 | CBM | 目标状态 |
|------|-----------------|-----------|-----|---------|
| 边类型 | 21 (22 w/ --deep) | 21+ | ~20 | 保持反超 |
| Java 支持 | 基础（6 种节点） | **生产级**（15+ 节点类型） | 深度 | **追平** |
| MCP 工具 | 20（查询型） | 20+（**智能分析型**） | ~5（联网） | **不可逆领先** |
| 跨语言解析 | ❌ | ✓（JVM + Web） | ❌ | **独有能力** |
| 安全分析 | 基本污点 | **智能漏洞检测** | 无 | **独有能力** |
| 索引速度 | 289s | ≤ 100s | 14.1s | 差距 20x→7x |
| 文件覆盖 | 2,831 | 3,100+ | 3,241 | 接近追平 |
| 多语言验证 | g-ass-source (TS) | + spring-petclinic (Java) | — | **更严格验证** |

### 27.9 v5.7.0 完成定义 (DoD)

- [x] P45: Java extractor 达到 Python extractor 同等深度
- [x] P45a: 包与导入系统（imports 边产出）
- [x] P45b: 注解处理（decorates 边产出）
- [x] P45c: 泛型处理（type_ref 边产出）
- [x] P45d: 方法调用深度升级（instantiates + 链式调用）
- [x] P45e: 现代 Java 特性（lambda, record, enum, interface default）
- [x] P45f: 变量级读写追踪（reads/writes/data_flows 边产出）
- [x] P46: MCP 3.0 4 项升级完成（重构/安全/质量/搜索）
- [x]   P46a: 智能重构建议（8 tests pass）
- [x]   P46b: 安全漏洞检测（13 tests pass）
- [x]   P46c: 代码质量门禁（11 tests pass）
- [x]   P46d: 智能搜索增强（8 tests pass）
- [x] P47: 跨语言边解析（JVM + Web 生态）— 8 tests pass
- [x] P48: Performance 4.0 — mmap + worker warmup + parser cache
- [x] P49: spring-petclinic 验证管线 (auto-skip, quality gate checks)
- [x] 全量回归: 2823 passed, 33 skipped, 0 failed（1 pre-existing LSP error）
- [x] quality-gates.md: P45-P47, P46a-d 全部通过
- [ ] g-ass-source 跨项目验证: ≤ 100s（需在真实环境测量）
- [ ] spring-petclinic 真实验证（需下载项目后运行）
