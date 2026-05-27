# Architecture Decision Escalation

> `comp-design-sync` 按需读取。主 skill 只负责发现可升级决策并报告；真正写入 `.tws/architecture-decisions.md` 必须由人类项目经理确认后调用 `comp-arch-decision`。

## 报告格式

```markdown
📌 检测到可升级为项目级的架构决策：

1. {决策标题}
   背景：{为什么需要做这个决策}
   决定：{选了什么}
   排除：{排除了什么}
   影响：{影响哪些模块}
   来源：设计书 {功能名}
```

## 闭环机制

- 主 agent 汇总所有任务的决策报告后，统一输出给用户。
- 如果用户不立即处理，决策报告追加到 `.tws/deferred-issues.md`，标记为「待审核架构决策」。
- 下次 `using-tws` 启动时，检查 `deferred-issues.md` 中的待审核决策，提醒用户处理。
- 设计书的决策记录保留不动，它是“这个功能为什么这么做”的原始记录。
