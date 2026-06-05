---
name: impact-assessment
description: 动手前评估改动的影响范围——事前检查。涉及哪些模块、改什么接口、连锁影响。跟 found-impact-report（事后上报）是不同环节
---

# 影响范围判断

## 核心原则

**没想清楚影响范围之前，不要动手改。**

改代码是最容易的部分。知道改了之后会影响到什么，才是需要想清楚的。

## Step 0: 代码图预分析（客观事实层）

在进入 Gate Function 之前，先通过代码图获取客观调用关系——图告诉你「被谁调了、影响半径多大」，这些是事实，不需要 agent 推理。

**必须操作：** 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），按其中指引完成前置检查和以下查询：

```
1. tws-graph impact <要改的符号> --depth 2
   → 按模块分组的调用者列表，每个调用者的文件:行号、可见性
   → 涉及路由节点时标注 API 端点

2. tws-graph calls <要改的符号> --inbound
   → 直接调用者列表（精确到文件:行号）

3. tws-graph snapshot before
   → 保存改前快照（design-sync 需要 diff 到此快照）
```

错误处理详见 `found-tws-graph-usage`。空结果/符号未找到时的回退策略照旧（标注 provenance=heuristic，退回到 grep）。

→ 以上 Step 0 是「**客观事实层**」——图告诉你的，不需要推理
→ 以下 Gate Function 是「**主观判断层**」——agent 基于图输出做推理

## The Gate Function

```
BEFORE implementing any change, AFTER the design/root cause is clear:

1. 查图输出 → 涉及哪些模块？—— 基于 impact 结果列出
2. 改什么接口？—— 基于 tws-graph diff 签名变更
3. 消费者是谁？—— 图 calls --inbound 输出 = 客观消费者列表
4. 会不会有连锁影响？—— 图 impact --depth 2 展开传递依赖链
5. 要不要回测？—— 哪些现有测试可能受影响
6. 要不要更新文档？—— 设计书、README、API 文档
7. 输出影响范围报告
```

## 输出格式

```markdown
### 影响范围
- 涉及模块：{模块A, 模块B}
- 接口变更：{接口X（有消费者：模块C）, 接口Y（无消费者）}
- 连锁影响：{如果有}
- 需回测：{哪些测试}
- 需更新文档：{哪些文档}
- 风险等级：{低/中/高}
```

## 风险等级参考

| 等级 | 条件 |
|------|------|
| 低 | 只改一个模块的内部实现，不暴露接口 |
| 中 | 改了一个接口，但有向后兼容 |
| 高 | 改了接口且不向后兼容 / 改了核心模块 / 涉及数据迁移 |

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「就改一个字段，不影响别的」 | 字段可能被其他模块引用。查了再说 |
| 「这个接口没人用」 | 查 tws-graph impact。没查就是猜 |
| 「影响范围很小」 | 说出具体影响哪些模块，而不是用「很小」概括 |
| 「不用回测吧」 | 影响了 A 模块就必须跑 A 的测试 |
| 「tws-graph 还没装，用 grep 也一样」 | grep 找不到跨文件间接调用。装 tws-graph |
