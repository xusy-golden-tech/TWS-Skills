---
name: flow-add-feature
description: 添加新功能标准流程。复杂度门禁 → 简化或完整路径 → 逐任务(设计书→编码→单元测试→同步) → 集成测试
---

<SUBAGENT-STOP>
This is a top-level flow skill. Do not trigger it if you were dispatched as a subagent for a specific task.
</SUBAGENT-STOP>

# 添加功能

## 复杂度门禁（第 0 步）

```
检查条件：
□ 涉及文件数 ≤ 2？
□ 只改一个模块？
□ 不改接口？

全部满足 → 简化路径
任一不满足 → 完整路径
```

### 简化路径

```
设计书 → 编码 → 测试 → 同步
省略完整路径中的 discuss、plan、任务分解、集成测试；不省略设计书、测试和同步
```

### 完整路径

```
需求分析 → discuss → plan → 任务分解 → 逐任务循环 → 集成测试
```

> **逐任务循环中的架构校验：** 每个任务在设计书确认后、编码前，主 agent 需做轻量架构校验（检查清单见⑤-a 节）。这不是独立步骤，而是设计书确认的附加条件。

---

## 完整路径

### ① 需求分析 → ② Discuss → ③ Plan → ④ 任务分解

> **说明：** ② Discuss 和 ③ Plan 是流程步骤而非独立组件。它们在 flow skill 中内联描述，agent 按文本指引直接执行，无需额外加载组件 skill。

Plan 产出物：开发流程 + 需求列表 + 注意点 + 影响范围（调 `comp-impact-assessment`）。
任务分解调 `comp-task-breakdown`。

### ⑤ 逐任务循环

主 agent 不加载组件 skill。对每个任务派子 agent 执行：

```
子 agent 读取对应 skill，执行，汇报结果。主 agent 只做验收。
```

子 agent 执行内容：

```
子 agent 必须通过 Skill 工具加载对应的 comp skill，而非用 Read 读文件。
Skill 工具会正式激活 skill，使其成为指令而非参考文本。

a. 设计书（加载 `comp-design-doc`） → 确认
   确认时附加架构校验（独立子 agent，干净上下文）：
   ```
   □ 新功能与现有模块的边界清晰吗？有没有模糊地带？
   □ 新功能是否引入了对其他模块的不必要依赖？
   □ 新功能的抽象层次与项目一致吗？有没有该抽象却直接硬编码的？
   □ 接口设计是否考虑了未来可能的扩展？
   □ 如果删掉这个功能，能不能干净地移除而不留残留？

   结论：通过 / 有风险（列风险 + 建议） / 阻塞（必须调整设计）
   ```
b. 可选：前端 UI 设计（任务涉及前端页面/组件/交互时加载 `comp-frontend-ui-design`）→ 生成设计系统，确认风格/配色/字体
c. 可选：视觉原型（涉及 UI 变动时加载 `comp-visual-prototype`）→ 确认外观
d. 编码（加载 `comp-implementation`）
e. 测试（加载 `comp-test`，测试规约路径见 .tws/project-map.md，按其中配置的重试上限执行）
f. 可选：目标回溯验证（满足触发条件时加载 `comp-goal-verify`，由独立审查子 agent 执行）
   - 触发条件（满足任一）：涉及 3+ 文件 / 涉及接口变更 / 安全相关功能 / 设计书有接线验证项
   - 不满足触发条件 → 跳过
g. 可选：代码审查（高风险任务加载 `comp-code-review`）
h. 设计书同步（加载 `comp-design-sync`）
i. ✅ 标记完成 → 更新 checkpoint
```

### ⑥ 集成测试

全部任务完成后跑集成测试。

## 完成依据

标准完成依据见根目录 `checkpoint-reference.md`。本流程额外要求：

- [ ] 所有任务已完成
- [ ] 集成测试通过
- [ ] 设计书已同步

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「这是小功能，直接写就行」 | 小功能也会改接口、状态和测试边界 |
| 「设计书写完就不用再看」 | 编码和同步都要回到设计书核对 |
| 「集成测试最后有空再跑」 | 集成测试是完成条件，不是附加项 |
