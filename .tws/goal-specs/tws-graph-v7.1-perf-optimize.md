# Goal: tws-graph v7.1.0 性能优化 — 缩小与 CBM 的差距

## 元信息
- 创建时间：2026-06-29
- 关联 flow：flow-refactor
- 关联 session：.tws/sessions/refactor-tws-graph-rust-v7.1.md
- 关联分支：refactor/tws-graph-rust-v7.1
- 目标状态：⏳ 待开始
- 前置版本：v7.0.0（重构完成，功能对齐）

## 总体目标

v7.0.0 已将 tws-graph 从 Python 成功重写到 Rust，功能完全对齐（1025 测试，2816 Python 测试，35 提取器，33 PyO3 函数）。当前性能：

| 指标 | CBM v5 | Python v6 | Rust v7.0 | Rust vs CBM 差距 |
|------|--------|-----------|-----------|------------------|
| TWS-Skills 索引 | 1.02s | 79.6s | 11.2s | **11x 慢** |
| ripgrep (142 files) | — | 12.4s | 2.8s | — |
| docker-py (149 files) | — | 26.8s | 2.5s | — |
| evolver (154 files) | — | 79.5s | 5.8s | — |

v7.1.0 目标：将 Rust vs CBM 的差距从 **11x 缩小到 <2x**，同时修复 v7.0.0 遗留的 bug。

## 约束条件
- 不改变 CLI 接口、PyO3 API 签名
- 不改变数据库 schema
- 不降低测试覆盖率（保持 1025+ Rust tests pass）
- 不引入 unsafe Rust
- 不改变提取器行为（节点/边输出不变）
- 版本号 → 7.1.0

## 热路径分析（v7.0.0 基准：428 files / 11.2s）

```
索引热路径开销 (每文件 26.2ms 均值)：
┌─────────────────────────────────────────────────────┐
│ tree-sitter parse + AST 递归遍历  ████████████ 38%  │ ← 提取器
│ SQLite BEGIN/COMMIT (每文件独立)  █████ 15%         │ ← DB
│ INSERT (未用 prepared stmt)       ████ 13%          │ ← DB
│ SHA256 hash (node/edge ID)        ███ 10%           │ ← CPU
│ Parser::new() + set_language()    ██ 8%             │ ← tree-sitter
│ String clone + Context 创建       █ 6%              │ ← 内存
│ File I/O                          █ 5%              │ ← IO
│ 其他 (语言检测, 路径处理)         █ 5%              │ ← 杂项
└─────────────────────────────────────────────────────┘
```

---

## 阶段 1: Bug 修复（前置条件）

### 门禁
- [ ] v7.0.0 已完成

### 测试
- [ ] markdown.rs 处理含韩文/中文/emoji 的 URL 不再 panic
- [ ] 全局审计所有 `&s[..N]` 字节切片，至少修复 markdown.rs 2 处

### 验收标准
- [ ] BUG-009: markdown.rs:173 `&url[..40]` → 字符边界安全切片
- [ ] BUG-009: markdown.rs:183 同上
- [ ] 全局 audit：`grep -rn '\[\.\.\d+\]' src/indexer/extractors/` 零风险项
- [ ] 1025 Rust tests 仍通过

### 状态：⏳ 待开始

---

## 阶段 2: 低风险快速优化 — DB 层（目标：11.2s → 6-7s，~40% 提升）

这三项改动范围小、风险低、收益确定。

### 门禁
- [ ] 阶段 1 完成

### 2a. 批量事务

**现状** (`lib.rs:116,147`): 每个文件包裹在独立的 `BEGIN TRANSACTION` + `COMMIT` 中。428 文件 = 428 次 WAL 同步。

**方案**: 每 100 个文件一组事务，或全部文件一个事务。

**改动**: `lib.rs` 的 `index()` 函数，约 30 行改动。

```rust
// Before: per-file transaction
for file_path in &files {
    if let Some((n, e)) = index_one_file(&conn, ...) { ... }
}

// After: batched transaction every N files
const BATCH_SIZE: usize = 100;
let _ = conn.execute_batch("BEGIN TRANSACTION");
for (i, file_path) in files.iter().enumerate() {
    if i > 0 && i % BATCH_SIZE == 0 {
        let _ = conn.execute_batch("COMMIT");
        let _ = conn.execute_batch("BEGIN TRANSACTION");
    }
    if let Some((n, e)) = index_one_file_no_txn(&conn, ...) { ... }
}
let _ = conn.execute_batch("COMMIT");
```

**注意**: `index_one_file()` 内部的事务包装需要移除或变为可选参数。

### 2b. Prepared Statements

**现状** (`lib.rs:118,137`): 每次 INSERT 用 `conn.execute(SQL, params)`，SQLite 每次重新解析 SQL。

**方案**: 在 `index()` 开始时 `prepare_cached()` 两个 INSERT 语句，循环中复用。

**改动**: `lib.rs` 的 `index()` 函数，约 20 行改动。

```rust
let mut insert_node = conn.prepare_cached(
    "INSERT OR REPLACE INTO nodes (...) VALUES (?1, ?2, ...)"
)?;
let mut insert_edge = conn.prepare_cached(
    "INSERT OR REPLACE INTO edges (...) VALUES (?1, ?2, ...)"
)?;

// 循环中:
insert_node.execute(rusqlite::params![...])?;
insert_edge.execute(rusqlite::params![...])?;
```

### 2c. Parser Pool（按语言复用）

**现状** (`lib.rs:100`): 每个文件执行 `Parser::new()` + `set_language()`，Parser 的 tree-sitter 内部状态重新分配。

**方案**: 在 `index()` 开始前，为每种出现过的语言创建一个 `Parser`，存入 `HashMap<&str, Parser>`。需要时取出、使用、放回。

**改动**: 新增 `src/indexer/parser_pool.rs`，`lib.rs` 中集成，约 60 行。

```rust
use std::collections::HashMap;

struct ParserPool {
    parsers: HashMap<String, tree_sitter::Parser>,
}

impl ParserPool {
    fn get_or_create(&mut self, lang: &str) -> Option<&mut tree_sitter::Parser> {
        if !self.parsers.contains_key(lang) {
            let ts_lang = lang_to_tree_sitter(lang)?;
            let mut parser = tree_sitter::Parser::new();
            parser.set_language(&ts_lang).ok()?;
            self.parsers.insert(lang.to_string(), parser);
        }
        self.parsers.get_mut(lang)
    }
}
```

**注意**: tree-sitter `Parser` 不是 `Send`，后续若并行化需要每个线程独立持有。

### 测试
- [ ] 批量事务后节点/边数不变（与 v7.0.0 快照对比）
- [ ] prepared stmt 后输出完全一致
- [ ] Parser pool 后 35 种语言各索引一个样本文件，结果与单 Parser 一致

### 验收标准
- [ ] 批量事务实现，TWS-Skills 索引耗时 < 9s
- [ ] Prepared statements 实现
- [ ] Parser pool 实现
- [ ] 1025 Rust tests 通过
- [ ] 节点/边输出与优化前 100% 一致

### 状态：⏳ 待开始

---

## 阶段 3: 中等风险优化 — 并行化（目标：6-7s → 2-3s，~60% 提升）

### 门禁
- [ ] 阶段 2 完成

### 3a. rayon 文件级并行提取

**现状**: 所有文件串行处理。

**方案**: 将文件列表分块，用 `rayon::par_iter()` 并行处理。每个线程拥有独立的 DB connection（SQLite 要求），独立 Parser pool，结果在主线程合并后写入。

**架构**:
```
主线程:
  1. scan_directory() → Vec<PathBuf>
  2. 按语言分组 (减少 parser 切换)
  3. par_chunks(50) → 各线程:
     a. 打开独立 DB conn
     b. 创建独立 ParserPool
     c. 处理本组文件
     d. 返回 Vec<(NodeRecord, EdgeRecord)>
  4. 主线程收集, 批量 INSERT

关键: SQLite WAL 模式支持多 reader，但 writer 需独占。
      因此各线程只提取不写库，最后由主线程统一写入。
```

**改动**: 
- `Cargo.toml`: 增加 `rayon = "1"`
- `lib.rs`: 重构 `index()` 主循环，约 80 行
- 新增 `src/indexer/parallel.rs`，约 100 行

**风险**: 
- tree-sitter `Parser` 不是 `Send + Sync`，每个线程需独立创建
- `ExtractionContext` 含 `String` 字段，需确保线程安全
- 内存峰值会上升（多文件同时在内存中）

### 测试
- [ ] 单线程 vs 多线程输出完全一致（nodes/edges 数量和内容）
- [ ] `TWS_USE_PARALLEL=0` 回退到串行
- [ ] 4 线程索引 TWS-Skills，验证加速比 > 2x

### 验收标准
- [ ] rayon 并行提取实现
- [ ] TWS-Skills 索引耗时 < 4s（4 线程）
- [ ] 输出与串行 100% 一致
- [ ] 1025 Rust tests 通过
- [ ] 内存峰值 < 1GB（当前 ~200MB）

### 状态：⏳ 待开始

---

## 阶段 4: 进一步优化 — 算法级（目标：2-3s → 1.5-2s，~30% 提升）

### 门禁
- [ ] 阶段 3 完成

### 4a. xxhash 替代 SHA256 做 ID

**现状** (`connection.rs:549`): `hash_id()` 用 SHA256 计算节点/边 ID，每次提取都做密码级哈希。

**方案**: 用 `xxhash-rust` (XXH3) 替代 SHA256 做符号 ID 生成。XXH3 比 SHA256 快 50-100x。保留 SHA256 仅用于内容寻址（body_hash）。

**改动**: 
- `Cargo.toml`: 增加 `xxhash-rust = { version = "0.8", features = ["xxh3"] }`
- `connection.rs`: 修改 `hash_id()`，约 5 行

```rust
pub fn hash_id(file_path: &str, qualified_name: &str) -> String {
    let raw = format!("{}:{}", file_path, qualified_name);
    let digest = xxhash_rust::xxh3::xxh3_64(raw.as_bytes());
    format!("{:016x}", digest)  // 16 hex chars
}
```

**风险**: 
- 哈希碰撞概率从 `1/2^128` (SHA256) 降到 `1/2^64` (XXH3)，在百万级节点下碰撞概率仍然极低（< 10^-12）
- 数据库中的旧 ID 与新 ID 不兼容 → 需要 `tws-graph index --force` 重建

### 4b. TreeCursor 优化关键提取器

**现状**: 提取器使用递归 `node.named_children()` 遍历 AST，每层都有函数调用栈开销和 Vec 分配。

**方案**: 对节点数最多的前 5 种语言（Python, TypeScript, Rust, Java, Go），用 `tree_sitter::TreeCursor` 做迭代式遍历。TreeCursor 是树游标，无递归、无分配。

**改动**: 每个目标提取器约 30-50 行重构，共 ~200 行。

```rust
// Before (recursive):
fn walk(&self, node: tree_sitter::Node, ...) {
    for child in node.named_children(&mut cursor) {
        match child.kind() { ... }
        self.walk(child, ...);  // recurse
    }
}

// After (cursor):
fn walk(&self, cursor: &mut tree_sitter::TreeCursor, ...) {
    loop {
        let node = cursor.node();
        match node.kind() { ... }
        if cursor.goto_first_child() {
            continue;  // depth-first
        }
        while !cursor.goto_next_sibling() {
            if !cursor.goto_parent() { return; }
        }
    }
}
```

### 测试
- [ ] xxhash ID 与 SHA256 ID 不冲突（百万节点压测）
- [ ] TreeCursor 版提取器输出与递归版 100% 一致
- [ ] 各语言独立测试通过

### 验收标准
- [ ] xxhash 实现
- [ ] 5 个关键提取器 TreeCursor 化
- [ ] TWS-Skills 索引耗时 < 2s
- [ ] 1025 Rust tests 通过

### 状态：⏳ 待开始

---

## 阶段 5: 收尾（目标：v7.1.0 发布就绪）

### 门禁
- [ ] 阶段 2-4 全部完成

### 验收标准
- [ ] 版本号 → 7.1.0 (pyproject.toml, Cargo.toml, __init__.py)
- [ ] `cargo test` 通过
- [ ] `pytest tws-graph/tests/` 通过
- [ ] `tws-graph lint` 通过
- [ ] 5 项目对照测试 (TWS-Skills, ripgrep, docker-py, evolver, get-shit-done)
- [ ] 性能报告更新
- [ ] 更新 CHANGELOG / CLAUDE.md

### 状态：⏳ 待开始

---

## 阻塞级 Bug

### BUG-009: markdown.rs 多字节字符 panic
- 发现阶段：v7.0.0 Phase 13 性能测试
- 复现：索引含韩文/中文/emoji 的 Markdown 文件（如 get-shit-done 项目的 README.ko-KR.md）
- 根因：`markdown.rs:173,183` 的 `&url[..40]` 按字节切片，在多字节 Unicode 字符边界处 panic
- 修复方式：`url.chars().take(40).collect::<String>()` 或用 `floor_char_boundary()`
- 状态：⏳ 待修复

---

## 性能目标汇总

```
CBM v5 (基线):    1.02s  █
                         
Rust v7.0 (当前):  11.2s  ███████████ (11x 慢)

Phase 2 后 (预估): 6-7s   ███████ (7x 慢)
Phase 3 后 (预估): 2-3s   ███ (3x 慢)
Phase 4 后 (预估): 1.5-2s ██ (2x 慢)
```

## 执行日志
- 2026-06-29 — 🎯 目标接收，spec 创建
