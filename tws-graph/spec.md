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

## 八、完成定义 (DoD)

- [ ] P17: 5/5 测试门禁通过，4 种新边类型有产出
- [ ] P18: 5/5 测试门禁通过，边类型总数 ≥ 20
- [ ] P19: 5/5 测试门禁通过，g-ass-source 索引 ≤ 30s
- [ ] P20: 4/5 测试门禁通过，文件覆盖 ≥ 3,000
- [ ] g-ass-source 边类型 ≥ 20 种
- [ ] 全量回归: 0 failed
- [ ] `tws-graph lint`: 0 errors
- [ ] quality-gates.md 7 门禁全通过（不退化）
- [ ] 设计书同步
