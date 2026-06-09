---
name: backend-impl-java
description: Java 后端编码（Spring Boot）。Controller → Service → Repository 分层。Interface 先于 Impl
---

# Java 后端编码

## 核心原则

**接口先行，实现随后。分层隔离，解耦优先。**

```
C → S → R
Controller（路由+参数）→ Service（接口+业务）→ Repository（数据）
```

## Step 0: 图中理解现有结构

在动笔之前，先用代码图理解要修改的模块及其依赖关系。

**必须操作：** 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），按其中指引完成前置检查和以下查询：

```
1. tws-graph --version              ← 检查可用性（不可用则 pip install -e tws-graph/）

2. tws-graph search <模块/类名>      ← 定位要修改的符号，标注文件:行号

3. tws-graph impact <符号> --depth 2 ← 查影响范围，确认改动会影响哪些调用者

4. tws-graph calls <符号>            ← 查该符号的依赖，理解它调了谁
```

错误处理详见 `found-tws-graph-usage`。图返回空时标注 provenance=heuristic，退回到 Grep + Read 手动追踪。

## The Gate Function

```
FOR each task:
1. 定接口（Interface）：方法签名 + 异常声明
2. 写实现（Impl）：业务逻辑
3. Controller：路由 + DTO + 调用 Service
4. 测试：Mock 下层，逐层验证
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「逻辑简单，接口和实现写一起」 | Interface 存在的意义不是为了复杂，是为了替换和测试 |
| 「Spring Data JPA 自动生成的不用测」 | 自动生成的不测 = 出错了不知道 |
| 「DTO 跟 Entity 一样，直接用 Entity」 | 用 Entity 作响应 = 暴露了不想暴露的字段 |
