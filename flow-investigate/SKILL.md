---
name: flow-investigate
description: 排查问题流程（只查不修）。复现 → 收敛 → 定位 → 报告。查清了如果要修，转 fix-bug 流程
---

<SUBAGENT-STOP>
If you were dispatched as a subagent for a specific task, skip this skill.
</SUBAGENT-STOP>

# 排查问题

## 流程

```
① 复现 → ② 范围收敛 → ③ 定位根因 → ④ 报告
```

## ① 复现

子 agent 通过 Skill 工具加载 `comp-reproduce`

```
1. 确认问题现象
2. 稳定复现
3. 记录触发条件
```

复现不了 → 追问用户更多细节，不关闭。

## ② 范围收敛

```
优先级：先看日志 → 再看配置 → 再看代码
按二分法缩小范围（哪个模块？哪个功能？哪个操作？）
每缩小一次记录中间结论
```

## ③ 定位根因

```
子 agent 通过 Skill 工具加载 `comp-root-cause-analysis`
最多 3 次假设循环，超限上报
```

## ④ 报告

```
### 排查报告
- 问题描述：{现象}
- 复现条件：{步骤}
- 根因：{具体位置 + 原因}
- 影响范围：{哪些功能}
- 建议修复方案：{方案描述}

只查不修。如果要修 → 转 fix-bug 流程。
```

**转接时的中间产物传递：**

investigate 的排查报告应保存为 `.tws/investigate-report.md`：

```
## 排查报告
- 问题描述：{现象}
- 复现条件：{步骤}
- 根因：{具体位置 + 原因}
- 影响范围：{哪些功能}
- 建议修复方案：{方案描述}
- 生成时间：YYYY-MM-DD HH:MM
```

转 fix-bug 时：

```
1. 主 agent 读取 .tws/investigate-report.md
2. 从步骤③修复方案开始，将报告内容作为 fix-bug 的输入
3. 子 agent 收到压缩后的排查结论（不读原始对话历史）
4. 更新 session-state.md：流程名改为 fix-bug，步骤从③开始
5. 修复完成后清理 investigate-report.md
```

**转接方式：**

````
investigate 的报告可作为 fix-bug 的 ① 复现 + ② 根因 输入。
转 fix-bug 时从第③步修复方案开始，不需要重新排查。
更新 session-state.md：流程名从 investigate 改为 fix-bug，①②直接标为已完成，当前步骤从③开始。
````

## 完成依据

- [ ] .tws/sessions/{当前流程文件} 已删除（流程完成，清理断点文件）

