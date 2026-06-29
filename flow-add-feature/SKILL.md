---
name: flow-add-feature
description: 添加新功能标准流程。复杂度门禁 → 简化或完整路径 → 逐任务(设计书→测试设计→编码→测试执行→同步) → 集成测试
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
设计书 → 测试设计 → 编码 → 测试执行 → 同步
跳过：方案构思、discuss、plan、任务分解、集成测试
```

### 完整路径

```
需求分析 → 方案构思 → discuss → plan → 任务分解 → 逐任务循环 → 集成测试
```

> **逐任务循环中的架构校验：** 每个任务在设计书确认后、编码前，主 agent 需做轻量架构校验（检查清单见⑤-a 节）。这不是独立步骤，而是设计书确认的附加条件。

---

## 完整路径

### ① 需求分析 → ② 方案构思 → ③ Discuss → ④ Plan → ⑤ 任务分解

> **说明：** 方案构思在需求明确但实现方向可选时介入。只有一种技术可行的做法可跳过。跳过判断：是否只有一种架构合理的方式实现？是 → 跳过构思直接进入 Discuss。
>
> Discuss 和 Plan 是流程步骤而非独立组件。它们在 flow skill 中内联描述，agent 按文本指引直接执行，无需额外加载组件 skill。
>
> 方案构思调 `comp-proposal-ideation`（子 agent 通过 Skill 工具加载）。
> Plan 产出物：开发流程 + 需求列表 + 注意点 + 影响范围（调 `comp-impact-assessment`）。
> 任务分解调 `comp-task-breakdown`。

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
d. 测试设计（加载 `comp-test`，设计阶段：基于设计书推导测试用例，编写测试脚本）
   此阶段测试预期失败——代码尚未实现，测试先于代码存在，验证测试能正确捕获需求
e. 编码（加载 `comp-implementation`，以通过 d 中编写的测试为目标）
f. 测试执行（加载 `comp-test`，执行阶段：运行测试、检查边界、确认 0 failures）
   如测试失败：子 agent 按 comp-test 中的「先分类再行动」判定方向，不得直接改测试代码
g. 可选：目标回溯验证（满足触发条件时加载 `comp-goal-verify`，由独立审查子 agent 执行）
   - 触发条件（满足任一）：涉及 3+ 文件 / 涉及接口变更 / 安全相关功能 / 设计书有接线验证项
   - 不满足触发条件 → 跳过
h. 可选：代码审查（高风险任务时，主 agent 按 comp-code-review 编排层执行三阶段并行审查）
i. 设计书同步（加载 `comp-design-sync`）
j. ✅ 标记完成 → 更新 checkpoint
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
