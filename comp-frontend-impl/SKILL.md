---
name: frontend-impl
description: 前端编码（React/Vue 等）。按设计书实现组件逻辑、状态管理、API 对接、响应式样式
---

# 前端编码

## 核心原则

**先搭骨架（组件 + 数据），再装血肉（样式 + 交互）。**

## Step 0: 图中理解现有结构

在动笔之前，先用代码图理解要修改的组件及其依赖关系。

**必须操作：** 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），按其中指引完成前置检查和以下查询：

```
1. tws-graph --version              ← 检查可用性（不可用则 pip install -e tws-graph/）

2. tws-graph search <组件/模块名>    ← 定位要修改的组件符号，标注文件:行号

3. tws-graph impact <符号> --depth 2 ← 查影响范围，确认改动会影响哪些调用者

4. tws-graph calls <符号>            ← 查该组件的依赖，理解它调了谁
```

错误处理详见 `found-tws-graph-usage`。图返回空时标注 provenance=heuristic，退回到 Grep + Read 手动追踪。

## The Gate Function

```
FOR each component:
1. 组件骨架：props 类型 + 子组件结构
2. 状态管理：组件内 state + 全局 store（如有）
3. API 对接：请求 → 响应处理 → 错误/加载/空状态
4. 样式：布局 + 响应式 + 状态样式
5. 验证：渲染正确 + 交互可用
```

## 常见失败模式

| 表现 | 原因 | 对策 |
|------|------|------|
| 组件太胖 | 一个组件做了太多事 | 拆子组件 |
| 状态混乱 | state 和 props 分不清 | state 归组件管，数据归 props 传 |
| API 错误没处理 | 只写了 .then 没写 .catch | 每个请求都有 error 状态 |
| 样式外溢 | 全局 CSS 污染 | 用 CSS Modules 或 scoped styles |

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「这个 UI 很标准，直接写」 | 标准的 UI 也要确认空状态和错误态 |
| 「先写逻辑，样式后面再调」 | 不画结构就写逻辑，写完了发现结构不对 |
| 「CSS 随便写一下，能看就行」 | 随便写的 CSS 后面没人敢改 |
