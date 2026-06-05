---
name: impact-assessment
description: 动手前评估改动的影响范围——事前检查。涉及哪些模块、改什么接口、连锁影响。跟 team-impact-report（事后上报）是不同环节
---

# 影响范围判断

## 核心原则

**没想清楚影响范围之前，不要动手改。**

改代码是最容易的部分。知道改了之后会影响到什么，才是需要想清楚的。

## Step 0: 代码图预分析（客观事实层）

在进入 Gate Function 之前，先通过代码图获取客观调用关系——图告诉你「被谁调了、影响半径多大」，这些是事实，不需要 agent 推理。

**前置检查：**

```
0.0 tws-graph --version 2>&1
    → 成功 → 继续 0.1
    → 失败（"command not found" 等）→ Bash: pip install -e tws-graph/ 2>&1
    → 安装成功 → 继续 0.1
    → 安装失败（找不到 tws-graph/ 目录或 pip 报错）→ 「tws-graph 不可用，本次退回到手动 grep/read」

0.1 tws-graph index
    → 确保索引是根据最新代码构建的
    → 如果解析有警告：继续，但标注「索引可能不完整」
```

**客观事实查询：**

```
0.2 tws-graph impact <要改的符号> --depth 2
    → 输出：按模块分组的调用者列表，每个调用者的文件:行号、可见性
    → 如果涉及路由节点 → 标注 API 端点

0.3 tws-graph calls <要改的符号> --inbound
    → 输出：直接调用者列表（精确到文件:行号）

0.4 tws-graph snapshot before
    → 保存改前快照（design-sync 改后会 diff 到这个快照）
```

**错误处理：**

```
如果 tws-graph impact/calls 返回空：
    → 可能是动态调度/闭包/回调 → 标注 provenance=heuristic
    → agent 退回到手动 grep 补充

如果符号未找到（tws-graph 返回 "未找到符号"）：
    → 回退到 grep 确认符号名是否正确
    → 可能是局部变量、lambda、动态生成 → 标注并跳过
```

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
