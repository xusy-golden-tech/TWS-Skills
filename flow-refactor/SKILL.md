---
name: flow-refactor
description: 重构流程。现状分析 → 目标设计 → 迁移计划 → 增量重构(先确认测试基线再改代码) → 对等验证 → 同步
---

<SUBAGENT-STOP>
If you were dispatched as a subagent for a specific task, skip this skill.
</SUBAGENT-STOP>

# 重构

## 流程

```
① 现状分析 → ② 方案构思 → ③ 目标设计 → ④ 迁移计划 → ⑤ 增量重构 → ⑥ 对等验证 → ⑦ 同步
```

## ① 现状分析

```
1. 当前代码的问题是什么？（耦合、重复、性能、可读性…）
2. 涉及哪些模块？
3. 现有测试覆盖度如何？
4. 影响评估（子 agent 通过 Skill 工具加载 `comp-impact-assessment`）
```

## ② 方案构思

```
1. 多视角探索：重构策略可以有不同的切法——按模块逐步拆？先抽接口再改实现？先补测试再动代码？
2. 假设映射：每种策略依赖什么假设（"现有测试能覆盖核心路径"等）
3. 子 agent 通过 Skill 工具加载 `comp-proposal-ideation`
4. 小范围重构（≤ 3 文件、不改接口）可跳过此步
```

## ③ 目标设计

```
1. 目标结构是什么样的？
2. 接口会怎么变？
3. 确认目标后再推进
```

## ④ 迁移计划

子 agent 通过 Skill 工具加载 `comp-migration-plan`

```
按模块拆分为小步，每步可以独立验证
避免大爆炸式重构
```

## ⑤ 增量重构

```
对每个模块，依次：
1. 跑现有测试确认基线（子 agent 通过 Skill 工具加载 `comp-test`）——必须 0 failures 才能开始重构
2. 可选：涉及 UI 重构时（子 agent 通过 Skill 工具加载 `comp-frontend-ui-design`，确认设计系统）
3. 重构代码（子 agent 通过 Skill 工具加载 `comp-implementation`）
4. 跑测试（子 agent 通过 Skill 工具加载 `comp-test`，确保行为一致，没改坏）
5. 进入下一个模块
```

## ⑥ 对等验证

```
验证重构前后行为一致：
1. 同一输入 → 同一输出
2. 跑所有相关测试（子 agent 通过 Skill 工具加载 `comp-test`）
3. 重点检查边界条件
```

## ⑦ 同步

子 agent 通过 Skill 工具加载 `comp-design-sync`

```
更新所有受影响的模块的设计书
```

## 完成依据

- [ ] .tws/sessions/{当前流程文件} 已删除（流程完成，清理断点文件）
