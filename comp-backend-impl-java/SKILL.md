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
