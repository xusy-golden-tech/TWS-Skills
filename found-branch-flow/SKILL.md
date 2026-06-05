---
name: found-branch-flow
description: 分支规范。强制从 develop 创建新分支，禁止直接在 develop/master 上开发
---

# 分支规范

## 核心原则

**所有代码改动必须从 `develop` 创建新分支。不允许直接在 `develop`/`master` 上提交。没有例外。**

```
git checkout develop && git checkout -b {type}/{description}
```

develop 是开发集成分支，master 是发布分支。直接在上面改 = 堵住所有人的合并路径。

## 分支命名规则

```
{type}/{简短描述}

feat/     → 新功能             例：feat/user-avatar
fix/      → 修复              例：fix/login-timeout-401
refactor/ → 重构              例：refactor/auth-middleware
docs/     → 文档              例：docs/api-contract
test/     → 测试              例：test/auth-edge-cases
chore/    → 杂务              例：chore/update-pytest
```

## The Gate Function

```
BEFORE starting work:
1. git checkout develop && git pull (确保本地 develop 最新)
2. git checkout -b {type}/{description}
3. 确认分支名符合命名规则

DURING work:
1. 定期 rebase develop（不是 merge）
2. 保持分支聚焦——一个分支只做一个改动

BEFORE merge:
1. rebase develop 解决冲突
2. 确保提交历史干净
3. 合并回 develop
```

## 常见失败模式

| 表现 | 正确做法 |
|------|---------|
| 直接在 develop/master 上改 | 永远不要。切分支再改 |
| 分支名 fixbug（不规范） | 用 fix/ 前缀 |
| 一个分支改了三件事 | 拆成三个分支 |
| merge develop 到分支 | 用 rebase，不是 merge |
| 不改了，分支不删 | 合并后删掉远程分支 |

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「就改一行，不用起分支了」 | 一行也要分支。分支是为了隔离，不是为了一堆代码 |
| 「先直接在 develop 上改，等会再 rebase」 | 等会 = 忘了。直接起分支 |
| 「merge 也一样」 | merge 产生多余的合并提交。rebase 保持历史线性 |
| 「分支名随便起一下」 | 规范的分支名让 CI/CD 能自动识别改动类型 |

## 为什么重要

- 没有分支隔离 → 多人同时改 develop → 天天冲突
- 分支名不规范 → CI/CD 无法根据前缀做不同处理
- 不做 rebase → 合并时一堆冲突，谁都不敢动
