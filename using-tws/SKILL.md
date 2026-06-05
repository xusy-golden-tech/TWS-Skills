---
name: using-tws
description: TWS 入口 skill — 自动判断开发场景，路由到对应流程。用户有任何开发相关的任务时，优先触发本 skill
---

<SUBAGENT-STOP>
If you were dispatched as a subagent with a specific task, skip this skill.
</SUBAGENT-STOP>

# TWS — 开发场景路由

## 全局规则（贯穿所有流程）

不猜测。判断 → 建议 → 等待确认 → 执行。

1. 没有设计书就不写代码
2. 增量实施，每步可验证，禁止跳过
3. 测试通过是最低要求，还要检查边界和同步
4. 改了代码必须同步设计书，不改 = 没改完
5. **主 agent 不写代码**——所有编码、测试、同步由子 agent 执行（详见 `comp-subagent-dispatch`）
6. **主 agent 不加载 comp skill**——comp skill（comp-reproduce、comp-implementation 等）是给子 agent 的工作手册。主 agent 的职责是派子 agent、告诉它加载哪个 comp skill、验收产出。主 agent 只加载 flow skill（如 flow-fix-bug）用于流程控制
7. **所有改动必须从 develop 开新分支**——`git checkout develop && git checkout -b {type}/{description}`。禁止直接在 develop/master 上提交（详见 `found-branch-flow`）
8. **子 agent 完成后必须 git commit**——每完成一个子任务立即 `git add` + `git commit`（不 push）。防止后续子 agent 误操作回滚已验收的改动。详见 `comp-subagent-dispatch` 的「结果合并」节
9. **主 agent 关注上下文容量**——长流程中累积多个子 agent 汇报后，上下文会逐渐膨胀。自检信号：已派 5+ 子 agent / 汇报累积超 3 屏 / 下一个任务很复杂。偏重时减少汇报内联、考虑合并后续步骤；过载时 checkpoint 保存后建议用户开新会话续上。这不是精确计算，是纪律——防止在上下文紧张时做低质量编排决策

## 第一步：判断场景

### 硬边界：此步骤只做判断，不做调查

此处只判断流程类型（fix-bug / add-feature / ...）。

```
允许：
✅ 读取 .tws/ 下的状态文件（sessions/ 目录、project-map.md）
✅ 根据用户描述判断场景类型
✅ 追问 1-2 个问题明确场景

禁止：
❌ 读业务代码
❌ 搜索代码库
❌ 起子 agent 去调查
❌ 「我需要先了解一下才能判断」→ 不需要，判断靠用户描述就够了
```

调查、分析、理解代码是子 agent 在 flow skill 里做的事，不是 using-tws 的事。
你的工作是**判断场景 → 输出计划 → 等确认**，仅此而已。

先判断上下文——有没有正在进行的工作？

```
用 Glob 检查 .tws/sessions/*.md：
→ 没有文件 → 正常路由，开始新流程
→ 有 1 个文件 → 「检测到上次未完成的流程（{流程名}），进度到 {当前步骤}。要续上吗？」
   → 用户选「是」→ 读取该文件，加载对应 flow skill，从断点续上
   → 用户选「否」→ 开始新流程
→ 有多个文件 → 列出所有：「检测到 {N} 个未完成的流程，要续上哪个？还是要开始新流程？」
   → 用户选择 → 读取对应文件，续上
   → 用户选「新流程」→ 正常路由

检查项目根目录：
→ 有未完成的 plan 文件（.planning/）→ 「继续这个 plan 还是重新定方向？」
→ 有设计书但没对应的代码 → 「这是设计阶段还没编码？」
→ 有代码但没设计书 → 「这是遗留代码？要先补设计书还是直接改？」
```

然后根据**场景特征**判断——不靠关键词硬匹配，用特征描述 + 排除法：

| 特征关键词 | 流程 | 说明 |
|-----------|------|------|
| 「紧急」「hotfix」 | hotfix | 时间紧、生产环境 |
| 「坏了」「报错」「不 work」「行为不对」 | fix-bug | 现有功能出问题 |
| 「新建」「从零」「脚手架」 | new-project | 空项目 |
| 「重构」「重写」「优化」 | refactor | 不改功能只改结构 |
| 「查一下」「为什么」「排查」 | investigate | 只查不修 |
| 「文档」「README」 | documentation | 产出物是文档 |
| 其他 / 不明确 | add-feature | 兜底 |

无法判断时追问 1-2 个问题，实在判断不定走 add-feature。

## 第二步：环境检查 + 加载 flow skill + 门禁判断

此步骤做三件事，顺序执行：

### 2a. 项目索引是否就绪

```
检查 .tws/project-map.md：
→ 存在 → 读取技术栈和路径映射
  → 后续按需加载规约文件（不要预加载全量）
  → 编码任务 → 加载对应栈的 coding-conventions
  → 测试任务 → 加载对应栈的 testing-conventions
  → 写设计书 → 加载 design-conventions
  → 遇到环境问题 → 加载 env-conventions

→ 不存在 → 「项目规约尚未初始化。是否先运行 `init/SKILL.md` 初始化？」
  → 用户选「是」→ 调用 init
  → 用户选「跳过」→ 继续，但后续 skill 可能提醒

检查 tws-graph 可用性：
  通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），
  按其中「前置检查」指引确认 tws-graph 已安装且索引已构建。
  → 不可用时按 found-tws-graph-usage 中的降级策略处理。
```

### 2a-补充：MCP 工具审计

提醒用户（不强制）：

「TWS 提示：当前会话启用的 MCP 工具会在每轮对话中消耗 token。
 如果当前任务不涉及以下能力，建议在 Claude Code 设置中临时禁用：
 - 浏览器/Playwright → 当前有 UI 测试任务吗？
 - GUI 自动化 → 当前需要 GUI 操作吗？
 - 图片分析 → 当前需要看截图吗？
 禁用方式：Settings → MCP → 取消勾选不需要的服务器」

不要强制，只是提醒。用户决定。如果用户明确说不需要提醒，本步骤在后续会话中跳过。

### 2b. 是否有未完成的流程

已在第一步中通过 Glob 检查 `.tws/sessions/*.md` 处理。如果用户选择续上，此步骤跳过；如果用户选择新流程，继续 2c。

### 2c. 加载对应的 flow skill，运行门禁判断

直接用 Skill 工具按名称调用，不要搜索文件、不要用 Read 读文件：

```
Skill(skill: "flow-fix-bug")
Skill(skill: "flow-add-feature")
Skill(skill: "flow-hotfix")
Skill(skill: "flow-refactor")
Skill(skill: "flow-investigate")
Skill(skill: "flow-new-project")
Skill(skill: "flow-documentation")
```

加载后：
→ flow skill 被正式激活，成为当前上下文中的指令
→ 从中获取复杂度门禁条件
→ 根据用户描述判断走简化路径还是完整路径
→ 门禁结果决定后续计划的步骤内容
→ 加载 ≠ 立即执行所有步骤：此时只做门禁判断和计划输出，用户确认后才进入具体步骤

禁止：
❌ Search 搜索 skill 文件路径
❌ Read 直接读 SKILL.md 文件
❌ 只有用 Skill 工具调用才能正式激活 skill

> session-state 的格式和恢复流程详见 `skills/checkpoint-reference.md`（需要读/写 checkpoint 时加载，不预加载）。

## 第三步：输出计划等待确认

基于第二步的门禁结果，输出对应的计划（简化路径或完整路径）。不是凭直觉写，而是严格对应 flow skill 中的路径。

**步骤不允许合并**。flow skill 中有几个步骤就展开几个，每步对应一条。例如 flow-fix-bug 完整路径有 10 步（复现→根因→方案→修复→回归→审查→防复燃→影响评估→集成→同步），计划就必须有 10 条，不能合并成 5 条。合并 = 丢失检查点 = 跳过风险。

输出格式必须展开每一步的**产出物和执行方式**。这不是概要，而是承诺清单——用户确认后，跳过任何一步都是违反承诺。

主 agent 在此步骤中只做**项目管理**：输出计划、协调子 agent、验收产出。所有实际工作（设计、编码、测试、审查、同步）由子 agent 执行。

```
📋 识别为：【{场景名}】
📐 使用流程：【{流程名}】（{简化路径 / 完整路径}，门禁依据：{为什么}）

🔄 执行计划：

{根据 flow skill 的路径展开步骤，每步包含：}
步骤 N — {步骤名}
  产出：{产出物}
  执行：子 agent（通过 Skill 工具加载 {对应 comp skill}）
  主 agent 验收：{验收标准}

要开始吗？
```

用户确认后：
1. 以上步骤成为**已承诺的执行计划**，后续必须按此执行，不得跳过
2. 立即加载子 agent 调度规则：`Skill(skill: "comp-subagent-dispatch")`——这是主 agent 执行计划的前提，不加载就不知道怎么派子 agent
3. 用 Write 工具创建 session 文件 `.tws/sessions/{flow-type}-{short-desc}.md`（支持多流程并行）
   - 文件名示例：`fix-bug-plugin-stuck.md`、`add-feature-refresh-btn.md`、`refactor-auth.md`
   - 内容格式：

```markdown
## 当前流程
- 流程名：{场景名}
- flow skill：{flow-fix-bug 等}
- 开始时间：{YYYY-MM-DD HH:MM}
- 版本号：1

## 进度
- [ ] ① {步骤名}
- [ ] ② {步骤名}
（复制计划中的所有步骤）

## 当前任务
- 正在做：① {第一步名称}
- 状态：进行中
```

4. 进入 flow skill 执行阶段——flow skill 已在第二步加载，门禁已通过，直接执行

5. 计划中的步骤是承诺，flow skill 中的流程是约束，两者共同保证执行质量

## 全局纠错——方向偏离时怎么办

如果执行过程中发现理解错了需求或走错了方向，不要硬着头皮继续：

```
发现走偏了 → STOP
1. 记录：为什么走偏了？（理解错了？信息不全？）
2. 纠正：回到正确的方向上
3. 调整 checkpoint：更新当前流程的 .tws/sessions/{文件}.md
4. 继续

不分什么流程都通用。
走偏是正常的，不走出来才不正常。
```

## Skill 优先级

```
1. 用户明确指定的命令 — 最高优先级
2. using-tws 场景判断 — 次之
3. 其他组件 skill — 按需调用
```

## 注意事项

- 如果用户描述很模糊，可以追问 1-2 个问题来明确场景，但不要过度追问
- 不要替用户做决定——输出判断结果，让用户确认
- 确认后立即调用 flow skill，不要继续停留在入口

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「这个需求很明确，不用走流程了」 | 流程是为了防漏，不是怀疑你 |
| 「这个就是加个字段而已」 | 「而已」= 跳过分析的信号 |
| 「先跑起来再说」 | 「后面」= 永远不会 |
| 「其实更简单的做法是…」 | 检查是否在绕过核心问题 |
| 「这个很简单，几分钟搞定」 | 越简单越要走流程，出坑的往往是这种 |
| 「我得先看看代码才能判断场景」 | 场景判断靠用户描述，看代码是子 agent 的事 |
| 「我需要了解一下才能写计划」 | 计划写的是步骤承诺，不是技术方案。调查在 flow skill 里做 |
