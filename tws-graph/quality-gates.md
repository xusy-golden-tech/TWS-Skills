# tws-graph 边质量门禁 spec

> 2026-06-24 | 实用性驱动的质量门禁
> 原则：数量反超不是终点，每条边必须经得起真实场景验证

---

## 门禁 1: self/cls 写入误报

**问题**：方法签名的 `self`/`cls` 参数被 VariableUsageExtractor 误判为变量写入

**门禁**：
- [ ] `self` 不在 writes 边的 target_text 中（Python）
- [ ] `cls` 不在 writes 边的 target_text 中（Python classmethod）
- [ ] 抽样 20 条 writes 边，0 条是方法接收者参数
- [ ] 现有 usage.py 和 dataflow.py 测试全通过

**测试方法**：
```sql
SELECT COUNT(*) FROM edges WHERE kind='writes' AND target_text IN ('self', 'cls');
-- 期望: 0
```

---

## 门禁 2: extends 边悬空率

**问题**：65% extends 边指向不存在的节点

**门禁**：
- [ ] extends 边总数中，指向不存在节点的比例 < 10%
- [ ] 抽样 10 条 extends 边，target 节点均存在于 nodes 表
- [ ] 无父类的类不产生 extends 边

**测试方法**：
```sql
SELECT COUNT(*) FROM edges e LEFT JOIN nodes n ON e.target = n.id
WHERE e.kind='extends' AND n.id IS NULL;
-- 期望: < 22 (原有 219 的 10%)
```

---

## 门禁 3: unresolved 分类准确率

**问题**：Python 内置类型方法（如 `str::lower`）被错误标记为 `[internal]`

**门禁**：
- [ ] `str::`, `list::`, `dict::`, `int::`, `float::`, `bool::`, `tuple::`, `set::` 开头的方法调用归为 `[external]`
- [ ] 含有 `::` 但前缀不在项目路径中的引用归为 `[external]`
- [ ] 抽样 20 个 unresolved refs，分类准确率 ≥ 90%

**测试方法**：
```bash
tws-graph unresolved 2>&1 | grep -c "\[internal\].*::" 
# 期望: 0（含 :: 且非项目内路径的不应被标记为 internal）
```

---

## 门禁 4: test_edge 持久性

**问题**：test_edge 边在增量索引后消失

**门禁**：
- [ ] 全量索引后 test_edge 计数 > 0
- [ ] 增量索引（0 变更）后 test_edge 计数不变
- [ ] 增量索引（1 变更）后 test_edge 计数合理变化（只更新受影响的测试关系）

**测试方法**：
```bash
# 全量
tws-graph index --force
BEFORE=$(sqlite3 .tws/codegraph/index.db "SELECT COUNT(*) FROM edges WHERE kind='test_edge';")
# 0 变更增量
tws-graph index
AFTER=$(sqlite3 .tws/codegraph/index.db "SELECT COUNT(*) FROM edges WHERE kind='test_edge';")
[ "$BEFORE" -eq "$AFTER" ] && echo "PASS" || echo "FAIL: $BEFORE → $AFTER"
```

---

## 门禁 5: 跨项目可移植性

**问题**：质量修复是否在另一个项目上同样有效

**门禁**：
- [ ] `D:\g-ass-source` 项目索引成功（无崩溃）
- [ ] 所有 11 种 edge kind 在该项目上均有产出（或合理说明为何为 0）
- [ ] self/cls 误报率 < 5%（跨项目验证修复一致性）
- [ ] extends 边悬空率 < 10%（跨项目验证修复一致性）

**测试方法**：
```bash
cd D:/g-ass-source
tws-graph index --force
# 检查 edge kind 分布
```

---

## 门禁 6: CBM 对比基准

**问题**：修复后与 CBM 在 g-ass-source 上的实用性对比

**门禁**：
- [ ] tws-graph 和 CBM 均成功索引 g-ass-source
- [ ] 共享边类型（calls/imports）密度不低于 CBM 的 80%
- [ ] tws-graph 独有的边类型（reads/writes/data_flows/test_edge）在该项目上均有产出
- [ ] tws-graph 能回答的实用查询 ≥ CBM 能回答的

---

## 门禁 7: 全量回归

**问题**：任何修复不能引入回归

**门禁**：
- [ ] `cd tws-graph && pytest --tb=short` — `3613+ passed, 0 failed`
- [ ] `tws-graph lint` — `0 errors`
- [ ] 增量 0 变更速度 < 100ms（不能因为修复而退化）
