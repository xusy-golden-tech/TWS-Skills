---
name: frontend-test
description: 前端测试。组件渲染、用户交互、状态变化、API mock、边界情况
---

# 前端测试

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
