---
name: team-environment-gov
description: 环境治理（全局规则，所有模式生效）。禁止擅自修改开发环境，先 mock 后请示
---

<SUBAGENT-STOP>
This rule applies to ALL modes, not just team mode. Skip only if instructed otherwise.
</SUBAGENT-STOP>

# 环境治理

## 红线 — 绝对禁止

```
❌ pip install / npm install / apt-get / brew
❌ 修改 .env、config.json、docker-compose.yml
❌ 修改端口、IP、端点
❌ 猜 API key / token / 密钥
❌ 修改 PATH、环境变量、系统设置
```

## 正确流程

```
测试因环境问题失败：
1. 先用 mock 跑单体测试
2. 报告环境问题详情
3. 问开发者：「需要我解决吗？」
4. 开发者决策 → 记入 .tws/env-rules.md
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「先装一下试试，不行再删」 | 装了就很难删。开发环境不是试验场 |
| 「这个包大家都在用」 | 你确定这个版本兼容吗？你确定团队允许吗？ |
| 「我 sudo 一下就行」 | sudo 让问题更严重，不是解决 |
| 「就改一个端口号」 | 改了端口可能跟别人冲突 |
| 「这个环境问题不解决没法测」 | 先用 mock 跑，问开发者后再决定 |
