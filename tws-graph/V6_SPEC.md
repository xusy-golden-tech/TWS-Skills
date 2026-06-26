# tws-graph v6.0.0 SPEC

> TDD 驱动文件。每个工作流包含：门禁 → 测试设计 → 编码 → 验收标准。
> 状态图例：⬜ 未开始 | 🔄 进行中 | ✅ 通过 | ❌ 阻塞 | ⚠️ 有风险
> 调研基线：V6_INVESTIGATION.md | CBM 基准：纯 C、Linux kernel 28M LOC / 3min、14 MCP 工具
> 执行顺序：P51 → P52 → P55 → P50 → P53 → P54（详见 Phases）

---

## Phase 1: P51 Extractor 深度升级 (Go/Rust/C++/C#/PHP) ⬜

**根源问题**：Go (237行/5边) 和 Rust (260行/4边) 仅产出 calls + contains，远不及 Python extractor (605行/18边产出)。C++/C#/PHP 中等深度但未达到 Python 级别。

**解决方案标准**：以 Python extractor 为深度参照，以 CBM 为竞争基准。每种新边类型必须有实际代码语义支撑，不做无意义的边类型堆砌。

### 门禁
- [ ] GATE-P51-1: 每个 extractor 当前能力 vs Python extractor 差距清单已完成（见 V6_INVESTIGATION.md §2）
- [ ] GATE-P51-2: 每个 extractor 有 ≥ 2 个 GitHub 真实项目作为测试素材（已 clone 到本地）
- [ ] GATE-P51-3: 每个 extractor 升级目标已明确定义（具体到边类型 + 语言特性）
- [ ] GATE-P51-4: CBM 对同语言的产出已测量（节点数/边数/边类型），作为验收基线

### TDD 循环
每 extractor 独立循环：
```
1. 选取 GitHub 真实项目 → 用 CBM 跑 → 记录 baseline 节点/边
2. 写测试（TEST-P51-{Lang}-*）：测试用例用真实项目代码片段
3. 跑测试 → 必须失败（当前 extractor 产出不足）→ 确认测试有效
4. 升级 extractor → 跑测试 → 通过
5. 在完整真实项目上跑 → 对比 CBM baseline → 达标
```

### 测试
- [x] TEST-P51-Go-imports: import 路径解析 + imports 边 ✅
- [x] TEST-P51-Go-interfaces: 接口实现检测 + implements 边 ✅
- [x] TEST-P51-Go-struct-methods: 接收者类型绑定 + 方法归属 ✅
- [x] TEST-P51-Go-struct-tags: struct tag 解析 + decorates 等价边 ✅
- [x] TEST-P51-Go-var-rw: 变量读写追踪 ✅
- [x] TEST-P51-Go-regression: 现有 Go 测试全部通过 ✅

- [x] TEST-P51-Rust-modules: mod 系统 + imports 边 ✅
- [x] TEST-P51-Rust-traits: trait 实现 + implements 边 ✅
- [x] TEST-P51-Rust-macros: 宏调用 + calls 边 ✅
- [x] TEST-P51-Rust-generics: 生命周期/泛型 + type_ref 边 ✅
- [x] TEST-P51-Rust-derive: #[derive] + decorates 等价边 ✅
- [x] TEST-P51-Rust-regression: 现有 Rust 测试全部通过 ✅

- [x] TEST-P51-C++-include: include 追踪 + imports 边 ✅
- [x] TEST-P51-C++-virtual: 虚函数覆写 + overrides 边 ✅
- [x] TEST-P51-C++-template: 模板 + type_ref 边 ✅
- [x] TEST-P51-C++-namespace: 命名空间 + qualified_name ✅
- [x] TEST-P51-C++-constructor: 构造函数 + instantiates 边 ✅
- [x] TEST-P51-C++-regression: 现有 C++ 测试全部通过 ✅

- [ ] TEST-P51-C#-using: using 指令 + imports 边
- [ ] TEST-P51-C#-attribute: [JsonProperty] 等属性 + decorates 边
- [ ] TEST-P51-C#-generic: 泛型 + type_ref 边
- [ ] TEST-P51-C#-LINQ: 链式调用
- [ ] TEST-P51-C#-property: 属性访问器 + reads/writes 边
- [ ] TEST-P51-C#-regression: 现有 C# 测试全部通过

- [ ] TEST-P51-PHP-use: use 导入 + imports 边
- [ ] TEST-P51-PHP-trait: trait + implements 等价边
- [ ] TEST-P51-PHP-annotation: 注解 + decorates 边
- [ ] TEST-P51-PHP-type-hint: 类型提示 + type_ref 边
- [ ] TEST-P51-PHP-namespace: 命名空间 + qualified_name
- [ ] TEST-P51-PHP-regression: 现有 PHP 测试全部通过

### 验收
- [ ] ACCEPT-P51: 5 个 extractor 在 ≥ 2 个 GitHub 真实项目上产出 ≥ CBM 的 1.0x（节点+边总数）
- [ ] ACCEPT-P51-EDGE: 每种新边类型有 ≥ 1 个真实项目验证用例
- [ ] ACCEPT-P51-ALL-TESTS: 全部 P51 测试通过 + 全部已有测试通过

---

## Phase 2: P52 新语言 Extractors (Swift/Dart/Groovy/CMake/Nix/Zig) ⬜

**根源问题**：6 个有生态价值的语言尚未支持。tree-sitter 语法可用性需先验证。

**注意**：此 phase 在 P51 完成后执行，因为 P51 建立的测试模式和深度标准可直接复用。

### 门禁
- [x] GATE-P52-1: 6 个语言的 tree-sitter 语法在 tree-sitter-language-pack 中全部可用 ✅
- [ ] GATE-P52-2: 不可用的语法 → 降级方案（从 GitHub 安装？跳过？标记为 blocked）
- [ ] GATE-P52-3: 每个语言有 ≥ 1 个 GitHub 真实项目作为测试素材
- [ ] GATE-P52-4: CBM 对同语言的支持情况已调查

### 测试
- [ ] TEST-P52-Swift: 节点+边产出测试
- [ ] TEST-P52-Dart: 节点+边产出测试
- [ ] TEST-P52-Groovy: 节点+边产出测试
- [ ] TEST-P52-CMake: 节点+边产出测试
- [ ] TEST-P52-Nix: 节点+边产出测试
- [ ] TEST-P52-Zig: 节点+边产出测试
- [ ] TEST-P52-INTEGRATION: 新 extractor 不干扰现有测试

### 验收
- [ ] ACCEPT-P52: 每个新 extractor 产出 ≥ 3 种节点类型 + ≥ 1 种边类型
- [ ] ACCEPT-P52-CBM: 每个新 extractor 在真实项目上产出 ≥ CBM 的 1.0x

---

## Phase 3: P55 工具分离 + 人类手册 ⬜

**根源问题**：found-tws-graph-usage 包含全部 21 个工具的文档，agent 加载时产生 token 膨胀。Agent 实际只需要核心查询工具（search/calls/impact/trace），人类才需要全套分析工具。

**解决方案**：将工具分为 Agent 用（≤12 个）和 Human 用，found-tws-graph-usage 只列 Agent 工具，人类手册覆盖全部。

### 门禁
- [ ] GATE-P55-1: 21 个 MCP 工具按 agent/human 分类完成
- [ ] GATE-P55-2: 分类标准已定义：agent 需要的是「读」工具（查图、追溯、影响分析），human 需要「写/管理」工具
- [ ] GATE-P55-3: 17 个 CLI 命令按 agent/human 分类完成
- [ ] GATE-P55-4: 现有 MCP 工具无功能删除（只能重组）

### Agent 工具候选（≤12 个）
```
核心查询 (4): search_symbols, get_code, get_dependencies, trace_path
影响分析 (2): get_impact, get_git_diff_impact
代码质量 (1): find_dead_code
架构分析 (1): get_entry_points
安全 (1): security_scan
审查辅助 (2): review_changes, safe_refactor
模式搜索 (1): find_pattern

共 12 个（从 21 个中选出）
```

### 测试
- [ ] TEST-P55-1: found-tws-graph-usage 只包含 agent 工具（≤12 个），每个工具有正确用法
- [ ] TEST-P55-2: docs/tws-graph-manual.md 覆盖全部 17 个 CLI 命令 + 21 个 MCP 工具
- [ ] TEST-P55-3: tws-graph serve 所有 21 个工具仍可正常注册和调用
- [ ] TEST-P55-4: MCP 工具注册代码无硬编码的分类逻辑

### 验收
- [ ] ACCEPT-P55-AGENT: found-tws-graph-usage 精简至 ≤ 150 行
- [ ] ACCEPT-P55-HUMAN: docs/tws-graph-manual.md 完成，覆盖全工具 + 使用场景 + 示例
- [ ] ACCEPT-P55-NO-LOSS: 零功能损失（所有工具仍可通过 MCP/CLI 调用）

---

## Phase 4: P50 Python 性能天花板验证 ⬜

**根源问题**：v5.2.0-v5.7.0 四轮优化已耗尽 Python 层低挂果实。Python vs C 存在 10-100x 语言级差距。不应该在 Python 层继续投入「渐进优化」，而应该做一次性的天花板验证——能提多少提多少，然后输出决策报告。

**重要**：此 phase 不追求「达到某个速度目标」，而是验证「Python 的天花板在哪里」。如果 2x 提升都达不到，Python 优化就此终止。

### 门禁
- [x] GATE-P50-1: 性能基准已测定 — tws-graph >300s vs CBM 1.021s（TWS-Skills 项目），差距 300x+
- [ ] GATE-P50-2: cProfile/py-spy 热点已定位到具体函数
- [ ] GATE-P50-3: 优化方案不损失任何功能（全部 4043 测试通过）
- [ ] GATE-P50-4: CBM 同项目性能已测量（作为参考，不作为验收标准）

### 测试
- [ ] TEST-P50-1: 基准项目性能测试（对 TWS-Skills 项目 index，3 次取中位数）
- [ ] TEST-P50-2: 大文件边缘场景（单文件 10k+ 行）
- [ ] TEST-P50-3: 全部 4043 现有测试通过

### 验收
- [ ] ACCEPT-P50: 若提升 ≥ 2x → 性能优化成功
- [x] ACCEPT-P50-FALLBACK: 已预判 — 300s→150s（2x）仍远超 CBM 1s。Python 性能天花板明确。建议 v7.0.0 用 Rust 重写核心引擎，保留 Python CLI 层作为前端

---

## Phase 5: P53 watch 命令加固 ⬜

**根源问题**：watch 命令已实现但测试覆盖率 0%。P53a-P53d 大部分已有基础代码。

### 门禁
- [ ] GATE-P53-1: 当前 watch 实现已审计（watcher 模块、防抖、忽略规则）
- [ ] GATE-P53-2: 测试空白清单已列

### 测试
- [ ] TEST-P53-1: 文件变更 → 自动增量索引
- [ ] TEST-P53-2: 防抖合并（500ms 内多次写入只触发 1 次）
- [ ] TEST-P53-3: 忽略规则（.git/node_modules/.tws）
- [ ] TEST-P53-4: 持续运行 10 分钟不崩溃

### 验收
- [ ] ACCEPT-P53: watch 测试覆盖率 ≥ 60%（从 0% 起）

---

## Phase 6: P54 多仓库联邦 ⬜

**根源问题**：多仓库场景（微服务）需要跨仓库调用图。CBM 已有 CROSS_* edges。

**注意**：此 phase 在 P51 完成后执行，依赖 extractor 深度升级的 imports 边。

### 门禁
- [ ] GATE-P54-1: 多仓库场景已定义（微服务 / monorepo / 依赖库）
- [ ] GATE-P54-2: 跨仓库符号解析策略已设计

### 测试
- [ ] TEST-P54-1: ≥ 3 个仓库同时索引
- [ ] TEST-P54-2: 跨仓库 import 边解析
- [ ] TEST-P54-3: 跨仓库 calls 关系
- [ ] TEST-P54-4: 跨仓库影响分析

### 验收
- [ ] ACCEPT-P54: 跨仓库 trace 能找到跨越仓库边界的调用路径

---

## 全局验收

- [ ] ACCEPT-GLOBAL-1: 全部 4043 现有测试通过
- [ ] ACCEPT-GLOBAL-2: tws-graph lint 0 错误 0 警告
- [ ] ACCEPT-GLOBAL-3: 对 TWS-Skills 项目索引成功，search/calls/impact/trace 可用
- [ ] ACCEPT-GLOBAL-4: 所有 extractor 测试使用 GitHub 真实项目代码片段（非手写 sample）
- [ ] ACCEPT-GLOBAL-5: 版本号升至 6.0.0
