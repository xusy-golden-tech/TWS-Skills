---
name: reproduce
description: 复现 Bug。找到稳定的复现步骤，确认问题真实存在
---

# 复现

## 核心原则

**复现不了 = 不能确认 Bug 存在。**

## Step 0: 图中定位相关代码

在手动搜索之前，先用代码图定位相关符号和调用链。

**必须操作：** 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），按其中指引完成前置检查和以下查询：

```
1. tws-graph --version              ← 检查可用性（不可用则 pip install -e tws-graph/）

2. tws-graph search <关键词>         ← 定位用户报告涉及的功能/模块符号
   → 找到相关类名、方法名、函数名，标注文件:行号

3. tws-graph calls <入口符号>        ← 查调用链，理解从触发点到出错点的完整路径
   tws-graph calls <符号> --inbound  ← 查调用者，理解哪些入口会触发此路径

4. tws-graph trace <入口> <出错点>   ← 如果已知入口和报错点，直接查完整路径
```

错误处理详见 `found-tws-graph-usage`。图返回空时标注 provenance=heuristic，退回到 Grep + Read 手动追踪。

## The Gate Function

```
1. 理解用户描述的现象
2. 确定触发条件（输入、环境、操作顺序）
3. 运行并验证：确实重现了
4. 记录：现象 + 复现步骤 + 环境信息

如果复现不了：
→ 追问用户更多信息（环境、版本、操作细节）
→ 不要假设问题不存在
→ 不要假设「用户操作错了」
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「复现不了，可能是偶发的」 | 不确定就别关闭。先标记为「待复现」 |
| 「我按操作做了，没出现」 | 环境不同、数据不同。问用户更多细节 |
| 「这个版本已经修复了」 | 确认当前版本是否包含修复 |
