---
name: flow-refactor
description: 重构流程。现状分析 → 目标设计 → 迁移计划 → 增量重构 → 对等验证 → 同步
---

<SUBAGENT-STOP>
If you were dispatched as a subagent for a specific task, skip this skill.
</SUBAGENT-STOP>

# 重构

## 流程

```
① 现状分析 → ② 目标设计 → ③ 迁移计划 → ④ 增量重构 → ⑤ 对等验证 → ⑥ 同步
```

## ① 现状分析

```
1. 当前代码的问题是什么？（耦合、重复、性能、可读性…）
2. 涉及哪些模块？
3. 现有测试覆盖度如何？
4. 影响评估（子 agent 通过 Skill 工具加载 `comp-impact-assessment`）
```

## ② 目标设计

```
1. 目标结构是什么样的？
2. 接口会怎么变？
3. 确认目标后再推进
```

## ③ 迁移计划

子 agent 通过 Skill 工具加载 `comp-migration-plan`

```
按模块拆分为小步，每步可以独立验证
避免大爆炸式重构
```

## ④ 增量重构

```
对每个模块，依次：
1. 可选：涉及 UI 重构时（子 agent 通过 Skill 工具加载 `comp-frontend-ui-design`，确认设计系统）
2. 重构代码（子 agent 通过 Skill 工具加载 `comp-implementation`）
3. 跑测试（子 agent 通过 Skill 工具加载 `comp-test`，确保没改坏）
4. 进入下一个模块
```

## ⑤ 对等验证

```
验证重构前后行为一致：
1. 同一输入 → 同一输出
2. 跑所有相关测试（子 agent 通过 Skill 工具加载 `comp-test`）
3. 重点检查边界条件
```

## ⑥ 同步

子 agent 通过 Skill 工具加载 `comp-design-sync`

```
更新所有受影响的模块的设计书
```

## 完成依据

- [ ] .tws/sessions/{当前流程文件} 已删除（流程完成，清理断点文件）
