---
name: backend-impl-python
description: Python 后端编码（FastAPI/Django/Flask）。数据模型 → 业务逻辑 → API 路由。类型注解完整
---

# Python 后端编码

## 核心原则

**分层清晰，每层可独立测试。**

```
M → S → C
Model（数据） → Service（业务） → Controller（路由）
向上不向下依赖
```

## The Gate Function

```
FOR each task:
1. 数据模型层（Pydantic/ORM Model）：字段 + 校验
2. 业务逻辑层（Service）：核心逻辑 + 异常定义
3. API 路由层（Controller）：路由 + 参数 + 响应
4. 测试：三层分别测
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「Python 不用类型注解」 | 类型注解 = 自文档。没有注解的函数要看了才知道传什么 |
| 「这层逻辑很少，一起写了」 | 再少也分层。今天少一层，明天这层就会消失 |
| 「FastAPI 自动校验了，不用写校验逻辑」 | 自动校验只验格式，不验业务规则 |
