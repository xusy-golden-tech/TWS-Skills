---
name: backend-test
description: 后端测试。单元测试 + 集成测试 + API 测试。每层独立测，不依赖下层真实实现
---

# 后端测试

## 覆盖要求

```
□ Service 层：每个 public 方法至少一个正常路径 + 一个异常路径
□ API 层：每个路由至少测 200（成功）和对应错误码
□ 数据层：CRUD 操作 + 查询条件边界
□ 边界：空值、极限值、非法输入、特殊字符、重复提交
```

## Mock 策略

```
- 外部服务（数据库/Redis/三方API）全部 mock
- 单元测试不启动真实数据库
- 集成测试用测试容器（如 Testcontainers）或者内存数据库
```

## 工具选型

| 语言 | 测试框架 | Mock 库 |
|------|---------|---------|
| Python | pytest | unittest.mock / pytest-mock |
| Java | JUnit 5 | Mockito |
| TypeScript | Jest / Vitest | jest.mock / vi.mock |

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「后端逻辑很简单，不用测异常路径」 | 后端最容易在异常路径泄漏错误或状态 |
| 「集成测试能覆盖单元测试」 | 集成测试慢且定位差，不能替代层内验证 |
| 「真实服务更可靠」 | 单元测试依赖真实外部服务会变成环境测试 |
