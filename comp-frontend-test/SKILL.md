---
name: frontend-test
description: 前端测试。组件渲染、用户交互、状态变化、API mock、边界情况
---

# 前端测试

## Step 0: 图中定位待测组件

写测试之前，先用代码图定位待测组件及其依赖关系。

**必须操作：** 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），按其中指引完成前置检查和以下查询：

```
1. tws-graph --version              ← 检查可用性（不可用则 pip install -e tws-graph/）

2. tws-graph search <组件名>         ← 定位待测组件符号，标注文件:行号

3. tws-graph calls <组件>            ← 查该组件调了哪些 hooks/API/子组件

4. tws-graph calls <组件> --inbound  ← 确认哪些父组件依赖它（回归测试范围）
```

错误处理详见 `found-tws-graph-usage`。图返回空时标注 provenance=heuristic，退回到 Grep + Read 手动追踪。

## 覆盖要求

```
每个组件至少覆盖四种状态：
□ 正常渲染（有数据时显示正确）
□ 空状态（无数据时显示占位/提示）
□ 错误状态（API 失败时显示错误提示）
□ 加载状态（请求中显示 loading）

交互测试：
□ 用户操作（点击、输入、表单提交）→ 触发正确行为
□ 路由跳转（如有）→ 导航到正确页面

边界情况：
□ 超长文本
□ 特殊字符
□ 快速重复点击
```

## Mock 策略

```
- API 请求全部 mock（MSW / jest.mock / vi.mock）
- 不依赖真实后端环境
- 组件测试用 Testing Library（React Testing Library / Vue Testing Library）
```
