from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path


BEGIN = "<!-- TWS:BEGIN managed by TWS Skills"
END = "<!-- TWS:END -->"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def read_version(source_root: Path) -> str:
    version_file = source_root / "VERSION"
    return read_text(version_file).strip() if version_file.exists() else "unknown"


def git_commit(source_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--short", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def fingerprint(source_root: Path, version: str, commit: str) -> str:
    digest = hashlib.sha256()
    for rel in [
        "VERSION",
        "PLATFORM-SUPPORT.md",
        "using-tws/SKILL.md",
        "tws-init/SKILL.md",
        "checkpoint-reference.md",
    ]:
        path = source_root / rel
        if path.exists():
            digest.update(rel.encode("utf-8"))
            digest.update(path.read_bytes())
    for skill_file in sorted(source_root.glob("*/SKILL.md")):
        rel = skill_file.relative_to(source_root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(skill_file.read_bytes())
    return f"{version}+{commit}+{digest.hexdigest()[:12]}"


def skill_rows(source_root: Path) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for skill_file in sorted(source_root.glob("*/SKILL.md")):
        canonical_id = skill_file.parent.name
        text = read_text(skill_file)
        match = re.search(r"^name:\s*(.+)$", text, flags=re.MULTILINE)
        display_name = match.group(1).strip() if match else canonical_id
        aliases = display_name if display_name != canonical_id else "-"
        path = f"{source_root.as_posix()}/{canonical_id}/SKILL.md"
        rows.append((canonical_id, path, display_name, aliases))
    return rows


def metadata(source_root: Path, timestamp: str | None) -> dict[str, str]:
    version = read_version(source_root)
    commit = git_commit(source_root)
    return {
        "version": version,
        "source_root": source_root.as_posix(),
        "source_root_realpath": source_root.resolve().as_posix(),
        "source_commit": commit,
        "source_fingerprint": fingerprint(source_root, version, commit),
        "updated_at": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def render_tws_version(meta: dict[str, str]) -> str:
    return "\n".join(
        [
            f"version: {meta['version']}",
            f"skill_source_root: {meta['source_root']}",
            f"skill_source_root_realpath: {meta['source_root_realpath']}",
            f"skill_source_version: {meta['version']}",
            f"skill_source_commit: {meta['source_commit']}",
            f"skill_source_fingerprint: {meta['source_fingerprint']}",
            f"updated_at: {meta['updated_at']}",
            "",
        ]
    )


def render_platform_skills(source_root: Path, platform: str, meta: dict[str, str]) -> str:
    rows = skill_rows(source_root)
    lines = [
        "## 平台",
        f"- 当前平台：{platform}",
        f"- TWS version：{meta['version']}",
        f"- skill 源目录：{meta['source_root']}",
        f"- skill source root realpath：{meta['source_root_realpath']}",
        f"- skill source commit：{meta['source_commit']}",
        f"- skill source fingerprint：{meta['source_fingerprint']}",
        f"- updated_at：{meta['updated_at']}",
        "- 所有映射路径基准：相对 skill 源目录解析",
        "- Claude Code 原生可发现：未知",
        "- Codex 原生可发现：未知",
        "- VSCode 兼容 loader：未知（扫描规则：{sourceRoot}/*/SKILL.md）",
        "- 是否支持子 agent：未知",
        "",
        "## 调用映射",
        "- `/tws-init` -> `tws-init/SKILL.md`",
        "- `/using-tws` -> `using-tws/SKILL.md`",
        '- `Skill(skill: "x")` -> 读取 `{skill 源目录}/x/SKILL.md`',
        "",
        "## 新会话恢复",
        "- 新会话先读取 `.tws/project-map.md` 和 `.tws/platform-skills.md`",
        "- 开发、修复、重构、排查、文档类多步骤任务默认进入 `using-tws/SKILL.md`",
        "- 若 `.tws/sessions/` 存在未完成流程，先询问是否续上",
        "- 上下文压缩、摘要恢复、新窗口续跑时，按 `using-tws/SKILL.md` 的恢复协议重新读取 `.tws/tws-version`、project-map、platform-skills、session、flow 和 dispatch",
        "- 若原生入口不可用，显式读取 `{skill 源目录}/using-tws/SKILL.md`",
        "",
        "## 完整 skill 映射",
        "canonical_id 必须等于目录名；`display_name`/`aliases` 只辅助展示或兼容旧称，不能改变调用 ID。",
        "",
        "| canonical_id | path | display_name | aliases |",
        "|--------------|------|--------------|---------|",
    ]
    lines.extend(f"| {canonical} | `{path}` | {display} | {aliases} |" for canonical, path, display, aliases in rows)
    lines.extend(
        [
            "",
            "## 降级规则",
            "- 无原生 skill loader：显式读取对应 `SKILL.md`",
            "- 无子 agent：标注“按子 agent 约束由当前 agent 执行”",
            "- 无隔离上下文：只提供必要文件和客观背景，避免复用执行者推理",
            "",
        ]
    )
    return "\n".join(lines)


def render_claude_block(meta: dict[str, str]) -> str:
    source = meta["source_root"]
    return f"""<!-- TWS:BEGIN managed by TWS Skills {meta['version']} -->
## TWS

本项目使用 TWS。

- TWS version: {meta['version']}
- skill source root: {source}
- skill source VERSION: {meta['version']}
- skill source root realpath: {meta['source_root_realpath']}
- skill source commit: {meta['source_commit']}
- skill source fingerprint: {meta['source_fingerprint']}
- 开发、修复、重构、排查、文档类多步骤工程任务，默认先调用 `/using-tws`；不得只按本区块直接开发。
- 如果 `.tws/project-map.md` 不存在，先调用 `/tws-init`。
- 如果 `/using-tws` 不可用，读取 `{source}/using-tws/SKILL.md` 并按其中流程执行。
- 新会话开始时先读取 `.tws/project-map.md` 和 `.tws/platform-skills.md`；若 `.tws/sessions/` 有未完成流程，先询问是否续上。
- 发生上下文压缩、摘要恢复或新窗口续跑时，按 `using-tws/SKILL.md` 的“上下文压缩恢复协议”重新读取 TWS 文件，不得只凭摘要继续。
- 如果 `.tws/tws-version` 与本区块或 `{source}/VERSION` 的 version/root/realpath/commit/fingerprint 任一不一致，先运行 `/tws-init` 或读取当前 `tws-init/SKILL.md` 刷新入口映射。
- 用户明确要求跳过 TWS 时才跳过。
<!-- TWS:END -->
"""


def render_agents_block(meta: dict[str, str]) -> str:
    source = meta["source_root"]
    return f"""<!-- TWS:BEGIN managed by TWS Skills {meta['version']} -->
## TWS

This project uses TWS.

- TWS version: {meta['version']}
- skill source root: {source}
- skill source VERSION: {meta['version']}
- skill source root realpath: {meta['source_root_realpath']}
- skill source commit: {meta['source_commit']}
- skill source fingerprint: {meta['source_fingerprint']}
- For development, fix, refactor, investigation, documentation, or other multi-step engineering tasks, start from `using-tws/SKILL.md`; do not implement directly from this block.
- If `.tws/project-map.md` is missing, run `tws-init/SKILL.md` first.
- If native skills are unavailable, read `{source}/using-tws/SKILL.md` directly and follow it.
- At the beginning of a new session, read `.tws/project-map.md` and `.tws/platform-skills.md`; if `.tws/sessions/` contains unfinished flows, ask whether to resume.
- After context compaction, summary restore, or a new window continuation, follow the context recovery protocol in `using-tws/SKILL.md`; do not continue from summary alone.
- If `.tws/tws-version` does not match this block or `{source}/VERSION` on version/root/realpath/commit/fingerprint, run `tws-init/SKILL.md` to refresh TWS entry mappings before development work.
- Skip TWS only when the user explicitly asks to skip it.
<!-- TWS:END -->
"""


def replace_managed_block(existing: str, block: str) -> str:
    pattern = re.compile(r"<!-- TWS:BEGIN managed by TWS Skills.*?<!-- TWS:END -->\s*", re.DOTALL)
    if pattern.search(existing):
        return pattern.sub(block.rstrip() + "\n", existing)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return existing + "\n" + block


def emit_all(source_root: Path, platform: str, meta: dict[str, str]) -> str:
    return "\n".join(
        [
            "===== .tws/tws-version =====",
            render_tws_version(meta),
            "===== .tws/platform-skills.md =====",
            render_platform_skills(source_root, platform, meta),
            "===== CLAUDE.md managed block =====",
            render_claude_block(meta),
            "===== AGENTS.md managed block =====",
            render_agents_block(meta),
        ]
    )


def write_outputs(project_root: Path, source_root: Path, platform: str, meta: dict[str, str]) -> None:
    tws_dir = project_root / ".tws"
    tws_dir.mkdir(parents=True, exist_ok=True)
    (tws_dir / "tws-version").write_text(render_tws_version(meta), encoding="utf-8")
    (tws_dir / "platform-skills.md").write_text(render_platform_skills(source_root, platform, meta), encoding="utf-8")

    if platform == "Claude Code":
        path = project_root / "CLAUDE.md"
        existing = read_text(path) if path.exists() else ""
        path.write_text(replace_managed_block(existing, render_claude_block(meta)), encoding="utf-8")
    elif platform == "Codex":
        path = project_root / "AGENTS.md"
        existing = read_text(path) if path.exists() else ""
        path.write_text(replace_managed_block(existing, render_agents_block(meta)), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render or write TWS bootstrap files for a target project.")
    parser.add_argument("--skill-source-root", type=Path, default=repo_root_from_script())
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--platform", choices=["Claude Code", "Codex", "VSCode", "Other"], default="Other")
    parser.add_argument(
        "--emit",
        choices=["all", "tws-version", "platform-skills", "claude-block", "agents-block"],
        default="all",
    )
    parser.add_argument("--timestamp", help="Override updated_at for deterministic validation.")
    parser.add_argument("--write", action="store_true", help="Write .tws files and the matching managed entry block.")
    args = parser.parse_args()

    source_root = args.skill_source_root.resolve()
    project_root = args.project_root.resolve()
    meta = metadata(source_root, args.timestamp)

    if args.write:
        write_outputs(project_root, source_root, args.platform, meta)
        return 0

    if args.emit == "tws-version":
        print(render_tws_version(meta), end="")
    elif args.emit == "platform-skills":
        print(render_platform_skills(source_root, args.platform, meta), end="")
    elif args.emit == "claude-block":
        print(render_claude_block(meta), end="")
    elif args.emit == "agents-block":
        print(render_agents_block(meta), end="")
    else:
        print(emit_all(source_root, args.platform, meta), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
