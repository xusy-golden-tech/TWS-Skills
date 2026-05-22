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
① 需求分析 → ② 架构设计 → ③ 模块划分 → ④ 逐模块开发 → ⑤ 集成测试 → ⑥ 部署
```

## ① 需求分析

```
1. 理解：做什么？给谁用？核心功能？
2. 输出一句话项目定位 → 确认
```

## ② 架构设计

```
1. 技术选型（语言、框架、数据库）
2. 数据结构设计（核心实体 + 关系）
3. 模块划分（几个模块，各模块职责）
4. 数据流（数据怎么走）
5. 涉及 UI 时：前端 UI 设计（子 agent 通过 Skill 工具加载 `comp-frontend-ui-design`，生成设计系统）
6. 涉及 UI 时：视觉原型（子 agent 通过 Skill 工具加载 `comp-visual-prototype`）
7. 确认
```

## ③ 模块划分

```
把项目拆成可独立开发的模块。
每个模块输出：模块名 + 职责 + 对外接口 + 依赖模块。
明确模块间的依赖关系（A 依赖 B → 先开发 B）。
```

## ④ 逐模块开发

按依赖顺序对每个模块执行：

```
1. 设计书（子 agent 通过 Skill 工具加载 `comp-design-doc`）
2. 可选：前端 UI 设计（涉及 UI 时加载 `comp-frontend-ui-design`，生成设计系统）
3. 编码（子 agent 通过 Skill 工具加载 `comp-implementation`，由 implementation 按技术栈路由到语言组件）
4. 测试（子 agent 通过 Skill 工具加载 `comp-test`）
5. 设计书同步（子 agent 通过 Skill 工具加载 `comp-design-sync`）
```
每模块完成后进入下一个。

## ⑤ 集成测试

联调各模块 → 跑端到端测试 → 接口对齐确认。

## ⑥ 部署

子 agent 通过 Skill 工具加载 `comp-deploy`。

## 完成依据

- [ ] .tws/sessions/{当前流程文件} 已删除（流程完成，清理断点文件）
- [ ] 所有模块开发完成
- [ ] 集成测试通过
- [ ] 各模块设计书已同步
- [ ] 没遗留的 TODO 或未处理的技术决策
