---
name: team-subagent-dispatch
description: 团队模式下的子 agent 调度入口。通用调度规则以 comp-subagent-dispatch 为准，本 skill 只补充团队协作关注点
---

<SUBAGENT-STOP>
This rule applies to agents that are considering splitting work into sub-agents. If you are already a sub-agent, do not recursively spawn further sub-agents without explicit approval.
</SUBAGENT-STOP>

# 团队子 Agent 调度入口

## 规则来源

通用子 agent 调度规则统一维护在 `comp-subagent-dispatch/SKILL.md`。本 skill 不是第二套调度规则，只是团队模式下的补充清单。

使用本 skill 时：

1. 先加载并遵循 `comp-subagent-dispatch/SKILL.md`
2. 再应用下面的团队补充规则
3. 如果两者冲突，以 `comp-subagent-dispatch/SKILL.md` 为准，并把冲突记录为待维护事项
4. 不在本 skill 中重复维护通用 prompt 模板、结果合并、平台降级等规则

通用规则以 comp-subagent-dispatch 为准；这样避免 Solo/Team 两套调度规则漂移。

## 团队补充规则

```
□ 涉及跨模块接口 → 子 agent prompt 中必须提供 CONTRACTS.md 相关条目
□ 涉及多人并行文件 → 明确每个子 agent 的文件/模块边界
□ 涉及共享环境 → 先读 team-environment-gov，禁止擅自改环境
□ 涉及接口、事件、公共函数 → 完成后触发 team-impact-report
```

## 团队并发上限

| 任务类型 | 最大并发 | 说明 |
|----------|----------|------|
| 代码修改 | 4 | 仅限边界清晰、文件不重叠 |
| 文档/调查 | 4 | 不涉及代码冲突 |
| 同一模块修改 | 1 | 串行处理，避免互相覆盖 |

超出上限时排队，不新增子 agent。

## 汇报要求

团队模式下，子 agent 汇报除通用字段外，还必须包含：

```markdown
### 团队影响
- 是否改动 CONTRACTS.md：是/否
- 影响消费者：{列表或无}
- 需要通知的模块/负责人：{列表或无}
- 共享环境变更：{无 / 说明}
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「通用调度已经够了」 | 团队模式还要处理契约、通知和共享环境 |
| 「两个子 agent 改同一模块更快」 | 同一模块并发通常会制造冲突 |
| 「接口影响等最后再看」 | 接口影响必须在任务边界里提前声明 |
