---
name: backend-test
description: 后端测试。单元测试 + 集成测试 + API 测试。每层独立测，不依赖下层真实实现
---

# 后端测试

## Step 0: 图中定位待测符号

写测试之前，先用代码图定位待测的 public 方法和它们所属的类/模块。

**必须操作：** 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），按其中指引完成前置检查和以下查询：

```
1. tws-graph --version              ← 检查可用性（不可用则 pip install -e tws-graph/）

2. tws-graph search kind:class <模块名>     ← 定位待测类，标注文件:行号

3. tws-graph calls <类名>                   ← 列出该类的所有 public 方法，确认测试覆盖范围

4. tws-graph calls <方法> --inbound         ← 确认哪些调用者依赖此方法（回归测试范围）
```

错误处理详见 `found-tws-graph-usage`。图返回空时标注 provenance=heuristic，退回到 Grep + Read 手动追踪。

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
