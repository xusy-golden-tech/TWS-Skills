---
name: tws-graph-init
description: 代码图初始化——安装 tws-graph CLI、编译 Rust 核心、构建项目符号关系图、创建基线快照
---

# TWS 代码图初始化

## 核心原则

**没有代码图，agent 就只能 grep + read 手工追代码——慢，而且容易漏。**

tws-graph-init 把代码图工具链的安装和初始化封装成一个标准流程，在任何 TWS 项目里运行一次即可。

## 架构说明（v7.0.0+）

tws-graph 从 v7.0.0 起已从纯 Python 重写为 Rust 核心 + Python CLI 包装：

- **Python CLI** (`tws-graph/`): `pip install -e tws-graph/` — typer 命令行界面
- **Rust 核心** (`tws-graph/rust_core/`): PyO3 cdylib — 索引器、查询引擎、图算法、GQL、lint 等全部核心逻辑
- **桥接**: Python 通过 `_core._core` 原生库调用 Rust 核心

**初始化必须完成两部分**：Python 包安装 + Rust 核心编译部署。缺一不可。

## 流程

**执行前必须：** 通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")），获取准确的命令语法和错误处理策略。以下各步骤的命令仅为流程描述，实际执行以 found-tws-graph-usage 为准。

```
① 检测环境 → ② 安装 Python 包 → ③ 编译 Rust 核心 → ④ 部署原生库 → ⑤ 构建索引 → ⑥ 解析跨文件引用 → ⑦ 创建基线快照 → ⑧ 安装 git hooks
```

## ① 检测环境

```
# 1. 读源码期望版本
Bash: grep 'version\s*=' tws-graph/pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/'
# 或 Read tws-graph/pyproject.toml 读 project.version

# 2. 查已安装版本
Bash: tws-graph --version 2>&1

→ 未安装（command not found）→ 继续步骤 ②
→ 已安装但版本号 < 源码版本 → 进入步骤 ②「升级」
→ 已安装且版本一致 → 跳到步骤 ⑤

# 3. 检测 Rust 工具链（v7.0.0+ 必需）
Bash: rustc --version 2>&1 && cargo --version 2>&1

→ 未安装 → 提示安装 Rust 工具链后重试：
  「tws-graph v7.0.0+ 需要 Rust 工具链编译核心引擎。请安装：
    Linux/macOS: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
    Windows: https://rustup.rs/
    安装后重启终端，重新运行此流程。」
→ 已安装 → 继续步骤 ②
```

> **版本号判断**：运行 `tws-graph --version` 获取已安装版本，与 `tws-graph/pyproject.toml` 中的 `version` 比较。小于则重装。

## ② 安装 Python 包

tws-graph 是随 TWS-Skills 仓库分发的 Python 包，位于项目根目录的 `tws-graph/` 下。

**安装（首次）：**
```
Bash: pip install -e tws-graph/ 2>&1
```

**升级（版本不匹配）：**
```
Bash: pip install -e tws-graph/ --upgrade 2>&1
```

```
→ 成功 → 验证: tws-graph --version（此时会报 ImportError，因为 Rust 核心尚未部署，正常现象）
  继续步骤 ③
→ 失败（找不到 tws-graph/ 目录或 pip 报错）→ 输出以下提示并结束：
  「tws-graph Python 包安装失败。请检查：
    1. tws-graph/ 目录是否存在
    2. Python >= 3.10 是否可用
   在问题解决之前，影响分析/设计书同步/根因分析将降级为手工 grep。」
```

## ③ 编译 Rust 核心

Rust 核心源码位于 `tws-graph/rust_core/`，使用 Cargo 构建。

```
Bash: cd tws-graph/rust_core && cargo build --release 2>&1

→ 首次构建需下载依赖（约 200+ crates），耗时 5-15 分钟（取决于机器和网络）
→ 构建产物：target/release/_core.dll（Windows）或 target/release/lib_core.so（Linux/macOS）
→ 增量编译缓存目录 target/ 约 4-5G，纯构建缓存，不影响最终产物
```

```
→ 成功 → 继续步骤 ④
→ 失败 → 输出以下提示：
  「Rust 核心编译失败。常见原因：
    1. 缺少 C++ 编译工具链（tree-sitter 解析器需要 C 编译器）
       Windows: 安装 Visual Studio Build Tools 或运行 'rustup default stable-msvc'
       Linux: sudo apt install build-essential
       macOS: xcode-select --install
    2. 网络问题导致 crate 下载失败
    3. 内存不足（rustc 链接阶段需要较多内存，建议 4G+ 可用内存）
   在问题解决之前，tws-graph 无法使用。」
```

## ④ 部署原生库

编译产物需要部署到 Python 可导入的位置。

```
# 确定 Python 扩展模块后缀
Bash: python -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))"
# 示例输出: .cp312-win_amd64.pyd (Windows) 或 .cpython-312-x86_64-linux-gnu.so (Linux)

# 确定 site-packages 路径
Bash: python -c "import site; print(site.getsitepackages()[0])"

# 创建 _core 包目录并部署
Bash: PKG_DIR=$(python -c "import site; print(site.getsitepackages()[0])")/_core && \
       mkdir -p "$PKG_DIR" && \
       echo 'from ._core import *' > "$PKG_DIR/__init__.py" && \
       echo '__doc__ = _core.__doc__' >> "$PKG_DIR/__init__.py" && \
       echo 'if hasattr(_core, "__all__"):' >> "$PKG_DIR/__init__.py" && \
       echo '    __all__ = _core.__all__' >> "$PKG_DIR/__init__.py" && \
       cp tws-graph/rust_core/target/release/_core.dll "$PKG_DIR/_core$(python -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")" 2>&1
# Windows 上 dll 替换为 .dll，Linux/macOS 上替换为 lib_core.so / lib_core.dylib
```

> **注意**：Windows 上产物名为 `_core.dll`，Linux 上为 `lib_core.so`，macOS 上为 `lib_core.dylib`。部署时统一按 Python 的 EXT_SUFFIX 命名（如 `_core.cp312-win_amd64.pyd`）。

```
# 验证部署
Bash: python -c "from _core._core import ping; print(ping())" 2>&1
→ 输出 "pong" → 继续步骤 ⑤
→ ImportError → 检查文件是否复制到正确位置、文件名是否匹配 EXT_SUFFIX
```

## ⑤ 构建索引

```
Bash: cd <项目根目录> && tws-graph index 2>&1

→ 输出索引统计（N 个文件, N 个符号, N 条关系）→ 继续步骤 ⑥
  tws-graph 通过 28+ 个 tree-sitter 提取器覆盖 30+ 种语言和文件格式，
      含编程语言、标记样式、配置格式、容器/数据库、技能文档等。
      详细清单见 found-tws-graph-usage。所有提取器均产出节点（可搜索符号）+ 边（关系）。
→ 如果部分文件解析失败 → 继续，但标注「部分文件索引失败，涉及这些文件的功能可能无法查询」
→ 如果全部失败 → 标注「索引构建失败」，后续技能退回 grep
```

> **.twsignore（v7.2.0）**：首次索引前，建议在项目根目录创建 `.twsignore` 文件，排除测试用例、构建产物、设计书等不需要索引的内容。格式与 `.gitignore` 一致（支持 `#` 注释、`!` 反选）。索引时自动读取。详见 `found-tws-graph-usage` 的「.twsignore 忽略文件」节。

## ⑥ 解析跨文件引用

索引构建完成后，各文件的符号和关系已经入库，但**跨文件的 import/call/type 引用尚未连接**。`tws-graph index` 只做单文件符号提取，`tws-graph resolve` 扫描所有跨文件边，通过语言特定的模块解析器（覆盖 21 种语言）解析引用目标，更新边的指向。

```
Bash: tws-graph resolve 2>&1

→ 输出解析统计（已解析 N 条引用，未解析 M 条）
→ 解析后，calls/impact/trace 等跨文件查询才能正常工作
→ 未解析的引用记录在 unresolved_refs 表中，可通过 tws-graph unresolved 查看
```

```
→ 成功 → 继续步骤 ⑦
→ 如果全部未解析 → 检查项目语言是否在 21 种支持语言之列、import 路径是否规范
→ 失败不阻塞，标注「跨文件引用未解析，calls/impact/trace 跨文件查询可能不完整」
```

> **为什么需要 resolve？** `tws-graph index` 提取了 `import X from Y` 和 `X.method()` 调用，但不知道 `X` 对应哪个文件的哪个符号。`resolve` 通过语言的模块解析规则，将 `from .utils import helper` 连接到 `utils.py` 中的 `helper` 函数，将 `Class.method()` 调用连接到 `Class.method` 定义。没有 resolve，跨文件的 calls/impact/trace 结果将不完整。

## ⑦ 创建基线快照

```
Bash: tws-graph snapshot initial 2>&1

→ 保存为 .tws/codegraph/index-initial.db
→ 后续 design-sync 可以 diff 到这个基线
```

## ⑧ 安装 git hooks

```
Bash: tws-graph hooks install 2>&1

→ 安装 post-commit / post-merge / post-checkout hooks
→ 之后每次 commit/merge/checkout 自动增量同步索引
→ 失败不阻塞，提示手动安装
```

## ⑨ 安装 Grep Hook（推荐）

Grep Hook 是 Claude Code PreToolUse hook，拦截 agent 的 Grep 调用，自动缩小搜索范围到 tws-graph 已索引的文件。减少 grep 噪音输出，节省 token。

```
Bash: cd <TWS-Skills 项目根目录> && bash grep-hook/install-grep-hook.sh 2>&1

→ 复制 grep-hook.sh 到 ~/.claude/hooks/
→ 在 ~/.claude/settings.json 注册 PreToolUse hook
→ 安装后，agent 使用 Grep 搜索已知符号名时，hook 自动查询 tws-graph 并缩小 Grep 范围
→ 仅交互式会话生效（--print 模式不支持 hooks，这是 Claude Code 平台限制）
→ 失败不阻塞：hook 是非阻塞模式，安装失败不影响 tws-graph 正常使用
```

> **原理**：hook 用启发式判断 Grep pattern 是否为符号名（无正则特殊字符）。若是，运行 `tws-graph search` 提取相关文件路径，将 Grep 的 `path` 参数缩小到这些文件所在目录。若不是符号名（正则/glob），透传原 Grep 调用不做修改。

> **维护**：升级 TWS-Skills 后，重新运行安装脚本即可更新 hook 脚本。

## 部署到其他服务器

tws-graph v7.0.0+ 在新服务器上需要从头初始化整个工具链。以下为完整流程：

```
1. 安装 Rust 工具链: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
2. 克隆仓库: git clone <repo-url>
3. pip install -e tws-graph/
4. cd tws-graph/rust_core && cargo build --release
5. 部署原生库（步骤 ④）
6. tws-graph index
7. tws-graph resolve
8. tws-graph snapshot initial
9. tws-graph hooks install
```

> **关于 target/ 目录**：`tws-graph/rust_core/target/` 是 Cargo 构建输出目录，包含 debug 和 release 构建产物、增量编译缓存、依赖库等，通常 4-5G。该目录已在 `.gitignore` 中排除，**发布和部署不需要此目录**。在源服务器上完成构建和部署后，可以安全删除：
> ```bash
> rm -rf tws-graph/rust_core/target/
> ```

## 完成标准

- [ ] rustc 和 cargo 可用
- [ ] tws-graph --version 正常输出版本号
- [ ] 版本号与 `tws-graph/pyproject.toml` 中一致
- [ ] `python -c "from _core._core import ping; print(ping())"` 输出 "pong"
- [ ] tws-graph index 成功运行
- [ ] .tws/codegraph/index.db 文件存在且 > 0
- [ ] tws-graph resolve 成功运行
- [ ] tws-graph snapshot initial 已创建基线
- [ ] tws-graph hooks install 已安装
- [ ] （推荐）grep-hook 已安装（`~/.claude/hooks/grep-hook.sh` 存在且 settings.json 已配置）

## 在 TWS 流程中的位置

- `flow-new-project` 的 `①.5 初始化代码图` 会调用本技能
- `tws-init` 完成规约生成后，调用本技能初始化代码图
- 如果已安装且版本匹配 → 跳过安装和编译步骤，直接进入索引

## 错误处理

| 问题 | 处理 |
|------|------|
| rustc/cargo 不可用 | 提示安装 Rust 工具链，结束流程 |
| tws-graph --version 失败 | 安装 Python 包 |
| 已安装版本 < 源码版本 | 升级 Python 包（pip install -e --upgrade），如 Rust 核心 API 有变更则重新编译 |
| pip install 失败 | 提示用户，后续退回 grep |
| cargo build --release 失败 | 检查 C++ 编译工具链、网络、内存，提示用户 |
| 原生库部署失败 | 检查 EXT_SUFFIX 和文件路径，手动复制 |
| tws-graph index 部分失败 | 继续，标注不完整 |
| tws-graph index 全部失败 | 标注失败，退回 grep |
| tws-graph resolve 失败 | 不阻塞，标注跨文件引用未解析 |
| tws-graph snapshot 失败 | 不阻塞，下次 design-sync 用最新 DB 比 |

## 后续使用

代码图初始化完成后，日常查询需通过 Skill 工具加载 `found-tws-graph-usage`（Skill(skill: "found-tws-graph-usage")）。
任何需要查图的子 agent 必须通过 Skill 工具加载它，获取准确的命令语法。

## 常规维护

项目代码有大量变更后，重新索引。具体命令语法以 `found-tws-graph-usage` 为准：

```
tws-graph index    ← 增量索引（只处理修改过的文件）
```

如果要强制全量重建（如索引版本升级、提取器扩展后想获得新增符号类型）：

```
rm .tws/codegraph/index.db
tws-graph index
tws-graph resolve
tws-graph snapshot initial
```

> **索引版本升级**：tws-graph 提取器持续扩展。如果项目索引是较早版本构建的，增量 index 不会重建已有文件。全量重建后可获得新增的符号类型（如结构式语言的节点、新编程语言的符号）。不影响已有查询，只是让 `search` 覆盖面更广。

## Rust 核心升级

当 `tws-graph/rust_core/` 源码有更新时（如新功能、性能优化、Bug 修复），需要重新编译和部署：

```
1. cd tws-graph/rust_core
2. git pull 获取最新代码
3. cargo build --release
4. 重新部署原生库（步骤 ④）
5. 验证: python -c "from _core._core import ping; print(ping())"
6. tws-graph index   # 全量重建索引以利用新提取器/符号类型
7. tws-graph resolve
```

## Rationalization Prevention

| 想说的话 | 真相 |
|---------|------|
| 「tws-graph 太重了，grep 够用」 | grep 找不到跨文件间接调用链 |
| 「先装 Python 包再编 Rust 吧」 | 安装后立即编译部署，不然 tws-graph 不可用 |
| 「这个项目很小，不用代码图」 | 项目会长大。现在建好基线，后面受益 |
| 「Rust 编译太慢，跳过吧」 | 一次编译，长期收益。target/ 目录可事后删除 |
| 「pip install 就够了，Rust 核心是可选依赖」 | Rust 核心是 v7.0.0+ 的必需组件，不是可选。没有它 tws-graph 无法运行任何命令 |
| 「装不上就算了」 | 装不上时至少确认是缺 Python、缺 Rust、还是缺 C++ 编译器 |
