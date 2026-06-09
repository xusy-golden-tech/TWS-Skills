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
