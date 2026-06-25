---
name: flow-new-project
description: 从零搭建项目。需求 → 架构 → 模块划分 → 逐模块开发 → 集成 → 部署
---

<SUBAGENT-STOP>
If you were dispatched as a subagent for a specific task, skip this skill.
</SUBAGENT-STOP>

# 从零搭项目

## 流程

```
① 需求分析 → ② 方案构思 → ③ 架构设计 → ④ 模块划分 → ⑤ 逐模块开发 → ⑥ 集成测试 → ⑦ 部署
```

## ① 需求分析

```
1. 理解：做什么？给谁用？核心功能？
2. 输出一句话项目定位 → 确认
```

## ①.5 初始化代码图

在架构设计开始前，初始化项目的代码关系图。后续所有技能（impact-assessment、design-sync、root-cause-analysis）
都可以从图中查询，不必再 grep + read 手工遍历。

调用 `Skill(skill: "tws-graph-init")` 执行完整的代码图初始化流程：
安装 tws-graph → 构建项目符号关系图 → 创建基线快照。

如果 tws-graph-init 执行失败，后续技能将自动降级到手工 grep/read。

代码图就绪后，后续影响分析/设计同步/根因分析需通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")）查询。

## ② 方案构思

```
1. 多视角探索：至少从成本优先/风险优先/灵活优先 3 个角度探索解法
2. 假设映射：每种解法依赖的关键假设
3. 比较收敛：淘汰不合约束的思路，选出推荐方案
4. 子 agent 通过 Skill 工具加载 `comp-proposal-ideation`
```

## ③ 架构设计

```
1. 技术选型（语言、框架、数据库）
2. 数据结构设计（核心实体 + 关系）
3. 模块划分（几个模块，各模块职责）
4. 数据流（数据怎么走）
5. 涉及 UI 时：前端 UI 设计（子 agent 通过 Skill 工具加载 `comp-frontend-ui-design`，生成设计系统）
6. 涉及 UI 时：视觉原型（子 agent 通过 Skill 工具加载 `comp-visual-prototype`）
7. 确认
```

## ④ 模块划分

```
把项目拆成可独立开发的模块。
每个模块输出：模块名 + 职责 + 对外接口 + 依赖模块。
明确模块间的依赖关系（A 依赖 B → 先开发 B）。
```

## ⑤ 逐模块开发

按依赖顺序对每个模块执行：

```
1. 设计书（子 agent 通过 Skill 工具加载 `comp-design-doc`）
2. 可选：前端 UI 设计（涉及 UI 时加载 `comp-frontend-ui-design`，生成设计系统）
3. 测试设计（子 agent 通过 Skill 工具加载 `comp-test`，设计阶段：基于设计书推导测试用例，编写测试脚本）
4. 编码（子 agent 通过 Skill 工具加载 `comp-implementation`，以通过步骤 3 编写的测试为目标）
5. 测试执行（子 agent 通过 Skill 工具加载 `comp-test`，执行阶段：运行测试、检查边界、确认通过）
   如测试失败：子 agent 按 comp-test 中的「先分类再行动」判定方向，不得直接改测试代码
6. 设计书同步（子 agent 通过 Skill 工具加载 `comp-design-sync`）
```
每模块完成后进入下一个。

## ⑥ 集成测试

联调各模块 → 跑端到端测试 → 接口对齐确认。

## ⑦ 部署

子 agent 通过 Skill 工具加载 `comp-deploy`。

## 完成依据

- [ ] .tws/sessions/{当前流程文件} 已删除（流程完成，清理断点文件）
- [ ] 所有模块开发完成
- [ ] 集成测试通过
- [ ] 各模块设计书已同步
- [ ] 没遗留的 TODO 或未处理的技术决策
