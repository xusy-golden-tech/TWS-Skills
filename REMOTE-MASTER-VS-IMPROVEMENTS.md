# 远端 Master 与改进分支比较评价

生成时间：2026-05-27

## 比较对象

| 对象 | 远端引用 | Commit | 说明 |
|------|----------|--------|------|
| 远端 master 版 | `origin/master` | `47e88b4` | 当前远端默认分支 |
| 远端改进版 | `origin/chore/skill-review-improvements` | `2f41647` | 已推送的本地改进分支 |

远端仓库：`http://106.15.120.127:3000/xusy/TWS-Skills`

## 差异概览

| 指标 | master 版 | 改进版 |
|------|----------:|-------:|
| 文件总数 | 83 | 95 |
| Skill 数量 | 41 | 41 |
| 相对 master 提交数 | - | 3 |
| 差异规模 | - | 44 files, +2243 / -915 |
| 跟踪的 `.pyc` / `__pycache__` | 2 个 `.pyc` | 0 |
| 本地校验脚本 | 无 | `validate-skills.py` |
| Bootstrap 生成器 | 无 | `tws-init/scripts/tws_bootstrap.py` |

## 评分

| 维度 | master 版 | 改进版 |
|------|----------:|-------:|
| 默认识别与新会话生效 | 5.0 | 9.0 |
| Claude Code / Codex / VSCode 兼容 | 4.5 | 9.2 |
| Skill / TWS 规则不丢失能力 | 5.0 | 9.2 |
| 上下文占用控制 | 5.5 | 9.0 |
| 规则清晰度与一致性 | 6.0 | 9.1 |
| 可验证性 | 3.0 | 9.4 |
| 维护复杂度 | 8.0 | 9.0 |
| 综合评分 | 5.6 | 9.1 |

## Master 版优点

- 结构更轻，新增机制少，首次阅读负担较低。
- 规则直接写在各 skill 中，局部修改比较简单。
- 作为早期基线较稳定，适合快速了解 TWS 的基本分层和流程。

## Master 版缺点

- 对“整仓放入其他项目后是否能被 Claude Code / Codex / VSCode 自动识别”的说明不足。
- 缺少项目级持久入口、`.tws/platform-skills.md` 映射、source fingerprint 等刷新机制。
- 上下文压缩或新窗口续跑后，主要依赖摘要和记忆，无法稳定保证重新加载 flow / comp skill。
- 缺少全局校验脚本，规则漂移、入口遗漏、重复/无效缓存文件不容易自动发现。
- master 中仍跟踪 `.pyc` 文件，仓库清洁度和可移植性较弱。

## 改进版优点

- 明确了 Claude Code、Codex、VSCode / 编辑器插件的等价加载方式。
- 新增项目级持久入口规则，要求新会话先读取 `.tws/project-map.md`、`.tws/platform-skills.md` 和 active session。
- 增加 `VERSION`、source root、realpath、commit、fingerprint 比对，降低覆盖升级后继续读取旧规则的风险。
- 增加上下文压缩恢复协议，要求压缩、摘要恢复、新窗口续跑时从磁盘重新加载入口链、session、flow、dispatch 和当前步骤 skill。
- 明确 canonical skill id 使用目录名，frontmatter `name` 只作展示/别名，降低 VSCode 或兼容 loader 的映射歧义。
- 将部分长 UI 规则移入 references，降低默认上下文占用，同时保留 UI Gate 和按需读取路径。
- 新增 `validate-skills.py`，覆盖入口链、platform 支持、source fingerprint、context recovery、flow closeout、UI Gate、Skill 引用解析、pycache 防回归。
- 新增 `tws-init/scripts/tws_bootstrap.py`，可确定性生成 `.tws/tws-version`、`.tws/platform-skills.md` 和 Claude/Codex 托管区块，减少手写模板漂移。

## 改进版缺点

- 规则体系更完整，也更依赖初始化和刷新动作正确执行。
- 如果目标平台没有项目级入口或 workspace instruction 机制，仍不能声称默认主动生效，只能走显式读取降级方案。
- `validate-skills.py` 能验证结构和关键语义，但不能替代真实 Claude Code / Codex / VSCode 端到端运行测试。
- `tws_bootstrap.py` 已降低模板漂移风险，但目标项目的技术栈规约内容仍需要 `tws-init` 按项目实际情况采样或问答生成。

## 结论

改进版更适合作为可发布、可移植、可维护的 TWS Skills 版本。它解决了 master 版最主要的风险：跨工具加载不明确、新会话不稳定、上下文压缩后规则可能丢失、升级覆盖后旧入口延续、缺少自动校验。

master 版适合作为轻量历史基线；若目标是让 Claude Code、Codex、VSCode 在不同项目中尽量稳定识别并遵循 TWS，建议以 `origin/chore/skill-review-improvements` 作为合并候选。
