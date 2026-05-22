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
跳过：discuss、plan、任务分解、集成测试
```

### 完整路径

```
需求分析 → discuss → plan → 任务分解 → 逐任务循环 → 集成测试
```

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

- [ ] .tws/sessions/{当前流程文件} 已删除（流程完成，清理断点文件）
- [ ] 所有任务已完成
- [ ] 集成测试通过
- [ ] 设计书已同步
- [ ] checkpoint 已清理
- [ ] 无阻塞项
