from __future__ import annotations

import csv
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ERRORS: list[str] = []


def fail(message: str) -> None:
    ERRORS.append(message)


def read_text(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8-sig")


def require_phrases(rel_path: str, phrases: list[str], label: str) -> None:
    text = read_text(rel_path)
    for phrase in phrases:
        if phrase not in text:
            fail(f"{rel_path} missing {label}: {phrase}")


PLATFORM_DOC_FILES = [
    "PLATFORM-SUPPORT.md",
    "README.md",
    "USAGE.md",
    "tws-init/SKILL.md",
]

PLATFORM_DISCOVERY_PHRASES = [
    "{sourceRoot}/*/SKILL.md",
    ".claude/skills",
    ".agents/skills",
    "skill source root",
    "CLAUDE.md",
    "AGENTS.md",
    ".tws/project-map.md",
    ".tws/platform-skills.md",
    "using-tws/SKILL.md",
    "整仓嵌入",
]

INIT_PLATFORM_TEMPLATE_PHRASES = [
    "所有映射路径基准",
    "Claude Code 原生可发现",
    "Codex 原生可发现",
    "完整 skill 映射表",
    "CLAUDE.md",
    "AGENTS.md",
    "新会话",
    "using-tws/SKILL.md",
]

SOURCE_REFRESH_PHRASES = [
    "skill source VERSION",
    "skill source root realpath",
    "skill source commit",
    "skill source fingerprint",
    "{skill source root}/VERSION",
]

USING_REFRESH_PHRASES = [
    ".tws/tws-version",
    "{skill source root}/VERSION",
    "fingerprint",
    "不得声称新版 TWS 已完整生效",
]

CANONICAL_SKILL_ID_PHRASES = [
    "canonical_id",
    "frontmatter `name` 只作展示/别名",
]

FLOW_HANDOFF_CHECKS = {
    "flow-fix-bug/SKILL.md": [
        "无法确认",
        "默认走完整路径",
    ],
    "flow-add-feature/SKILL.md": [
        "comp-subagent-dispatch",
        "comp-impact-assessment",
        "comp-task-breakdown",
        "主 agent 只整合和验收",
    ],
    "flow-hotfix/SKILL.md": [
        "deferred-issues.md",
        "删除 session 前",
    ],
    "checkpoint-reference.md": [
        "先检查标准完成依据，再删除对应 session 文件",
    ],
    "flow-investigate/SKILL.md": [
        "加载 `flow-fix-bug/SKILL.md`",
        "阶段交接",
        "不代表跳过 ③.5",
        "已吸收报告内容",
    ],
    "flow-documentation/SKILL.md": [
        "记录跳过理由",
        "checkpoint-reference.md",
        "检查公共完成依据",
    ],
}

CONTEXT_RECOVERY_CHECKS = {
    "using-tws/SKILL.md": [
        "上下文压缩恢复协议",
        "不得只凭摘要继续执行",
        ".tws/tws-version",
        "{skill source root}/VERSION",
        "checkpoint-reference.md",
        "comp-subagent-dispatch/SKILL.md",
    ],
    "checkpoint-reference.md": [
        "恢复所需文件",
        "上下文压缩恢复",
        "压缩摘要只能当作线索",
        "当前步骤所需 comp/found/team skill 必须重新加载",
    ],
    "PLATFORM-SUPPORT.md": [
        "上下文压缩",
        "摘要恢复",
        "context recovery protocol",
    ],
    "tws-init/SKILL.md": [
        "上下文压缩恢复协议",
        "上下文压缩 / 摘要恢复 / 新窗口续跑恢复规则",
    ],
    "comp-subagent-dispatch/SKILL.md": [
        "子 agent 发生上下文压缩",
        ".tws/project-map.md",
        ".tws/platform-skills.md",
        "不得只凭压缩摘要执行",
    ],
    "README.md": ["上下文压缩恢复"],
    "AGENTS.md": ["compacted-context resumptions"],
    "CLAUDE.md": ["compacted-context resumptions"],
}

FRONTEND_UI_GATE_SCRIPT_PHRASES = [
    '"ux": {"max_results": 3}',
    "UI_GATE_CHECKLIST",
    "loading/empty/error/normal states covered",
    "forms have labels, error copy, and recovery path",
    "charts/data views include labels, units, empty/error states",
]

BOOTSTRAP_SCRIPT = ROOT / "tws-init" / "scripts" / "tws_bootstrap.py"

BOOTSTRAP_OUTPUT_PHRASES = [
    "===== .tws/tws-version =====",
    "skill_source_fingerprint:",
    "===== .tws/platform-skills.md =====",
    "canonical_id",
    "using-tws/SKILL.md",
    "上下文压缩",
    "===== CLAUDE.md managed block =====",
    "===== AGENTS.md managed block =====",
    "context recovery protocol",
]


def check_skill_files() -> None:
    skill_files = sorted(ROOT.glob("*/SKILL.md"))
    if not skill_files:
        fail("No SKILL.md files found")
        return

    for path in skill_files:
        text = path.read_text(encoding="utf-8-sig")
        lines = text.splitlines()
        if len(lines) < 4 or lines[0] != "---":
            fail(f"{path.relative_to(ROOT)} missing YAML frontmatter")
            continue
        try:
            end = lines[1:].index("---") + 1
        except ValueError:
            fail(f"{path.relative_to(ROOT)} frontmatter is not closed")
            continue
        frontmatter = "\n".join(lines[1:end])
        if "name:" not in frontmatter:
            fail(f"{path.relative_to(ROOT)} missing name in frontmatter")
        if "description:" not in frontmatter:
            fail(f"{path.relative_to(ROOT)} missing description in frontmatter")


def check_stop_tags() -> None:
    required_names = {
        "using-tws",
        "tws-init",
        "comp-arch-decision",
        "comp-subagent-dispatch",
        "team-contract-aware",
        "team-environment-gov",
        "team-impact-report",
        "team-subagent-dispatch",
    }
    for path in sorted(ROOT.glob("*/SKILL.md")):
        rel = path.relative_to(ROOT)
        dirname = path.parent.name
        text = path.read_text(encoding="utf-8")
        needs_stop = dirname.startswith("flow-") or dirname in required_names
        if needs_stop and "<SUBAGENT-STOP>" not in text:
            fail(f"{rel} expected <SUBAGENT-STOP>")


def check_index_paths() -> None:
    index = ROOT / "SKILL-INDEX.md"
    if not index.exists():
        fail("SKILL-INDEX.md missing")
        return
    text = index.read_text(encoding="utf-8")
    for path in sorted(ROOT.glob("*/SKILL.md")):
        rel = path.relative_to(ROOT).as_posix()
        if rel not in text:
            fail(f"SKILL-INDEX.md does not mention {rel}")


def check_no_bytecode_artifacts() -> None:
    for path in sorted(ROOT.rglob("*")):
        if ".git" in path.parts:
            continue
        if path.suffix == ".pyc" or path.name == "__pycache__":
            fail(f"{path.relative_to(ROOT)} should not be committed")


def check_platform_docs() -> None:
    for rel_path in PLATFORM_DOC_FILES:
        path = ROOT / rel_path
        if not path.exists():
            fail(f"{rel_path} missing")
            continue
        text = path.read_text(encoding="utf-8-sig")
        if "Codex" not in text or "VSCode" not in text:
            fail(f"{path.relative_to(ROOT)} should mention Codex and VSCode platform support")

    require_phrases("PLATFORM-SUPPORT.md", PLATFORM_DISCOVERY_PHRASES, "platform discovery phrase")
    require_phrases("tws-init/SKILL.md", INIT_PLATFORM_TEMPLATE_PHRASES, "platform-skills template phrase")

    for rel in ["using-tws/SKILL.md", "comp-subagent-dispatch/SKILL.md"]:
        if "无原生 Skill loader" not in read_text(rel):
            fail(f"{rel} should define no-loader SKILL.md fallback")


def check_platform_activation_fingerprint() -> None:
    for rel in ["PLATFORM-SUPPORT.md", "tws-init/SKILL.md"]:
        require_phrases(rel, SOURCE_REFRESH_PHRASES, "source refresh phrase")

    require_phrases("using-tws/SKILL.md", USING_REFRESH_PHRASES, "activation refresh phrase")

    for rel in ["README.md", "AGENTS.md", "CLAUDE.md"]:
        require_phrases(rel, ["fingerprint", "tws-version"], "managed entry refresh phrase")


def check_skill_reference_resolution() -> None:
    skill_dirs = {path.parent.name for path in ROOT.glob("*/SKILL.md")}
    ignored = {"x", "xxx", "comp-xxx", "..."}
    pattern = re.compile(r"Skill\(skill:\s*[\"']([^\"']+)[\"']\)")
    for path in sorted(ROOT.glob("**/*.md")):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8-sig")
        for match in pattern.finditer(text):
            skill_id = match.group(1).strip()
            if not skill_id or skill_id in ignored or "{" in skill_id or "*" in skill_id:
                continue
            if skill_id not in skill_dirs:
                fail(f"{path.relative_to(ROOT)} references missing skill directory: {skill_id}")

    for rel in ["PLATFORM-SUPPORT.md", "tws-init/SKILL.md"]:
        require_phrases(rel, CANONICAL_SKILL_ID_PHRASES, "canonical skill id rule")


def check_flow_handoffs_and_closeout() -> None:
    for rel, phrases in FLOW_HANDOFF_CHECKS.items():
        require_phrases(rel, phrases, "flow handoff/closeout phrase")


def check_context_recovery_protocol() -> None:
    for rel, phrases in CONTEXT_RECOVERY_CHECKS.items():
        require_phrases(rel, phrases, "context recovery phrase")


def check_bootstrap_generator() -> None:
    if not BOOTSTRAP_SCRIPT.exists():
        fail("tws-init/scripts/tws_bootstrap.py missing")
        return

    for rel in ["tws-init/SKILL.md", "README.md", "PLATFORM-SUPPORT.md"]:
        require_phrases(rel, ["tws-init/scripts/tws_bootstrap.py"], "bootstrap generator reference")

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(BOOTSTRAP_SCRIPT),
            "--platform",
            "Codex",
            "--timestamp",
            "2000-01-01 00:00",
            "--emit",
            "all",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if result.returncode != 0:
        fail("tws_bootstrap.py smoke test failed: " + result.stderr.strip())
        return

    for phrase in BOOTSTRAP_OUTPUT_PHRASES:
        if phrase not in result.stdout:
            fail(f"tws_bootstrap.py output missing: {phrase}")


def check_rule_clarity() -> None:
    forbidden_phrases = [
        "小念",
        "妈妈",
        "G-Assistant",
        "跳过讨论、plan、review",
        "完整路径有 10 步",
        "不能合并成 5",
        "session-state.md",
        "skills/checkpoint-reference.md",
    ]
    for path in sorted(ROOT.glob("**/*.md")):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8-sig")
        for phrase in forbidden_phrases:
            if phrase in text:
                fail(f"{path.relative_to(ROOT)} contains ambiguous or project-specific phrase: {phrase}")

    required_boundaries = {
        ROOT / "comp-test" / "SKILL.md": [
            "不是 `comp-test` 的替代品",
            "先加载 comp-test",
        ],
        ROOT / "team-subagent-dispatch" / "SKILL.md": [
            "不是第二套调度规则",
            "通用规则以 comp-subagent-dispatch 为准",
        ],
        ROOT / "comp-code-review" / "SKILL.md": [
            "与 review-methodology 的边界",
            "具体查什么、怎么报告",
        ],
        ROOT / "using-tws" / "SKILL.md": [
            "唯一例外",
            "无子 agent 降级协议",
            "无子 agent 平台按上方降级协议执行",
        ],
        ROOT / "flow-hotfix" / "SKILL.md": [
            "事后补齐项",
            "不是永久省略",
        ],
        ROOT / "flow-fix-bug" / "SKILL.md": [
            "方案架构校验",
            "代码审查",
            "不省略根因、验证和同步",
        ],
        ROOT / "comp-subagent-dispatch" / "SKILL.md": [
            "无子 agent 平台按降级协议",
            "有可用子 agent 时",
        ],
    }
    for path, phrases in required_boundaries.items():
        text = path.read_text(encoding="utf-8-sig")
        for phrase in phrases:
            if phrase not in text:
                fail(f"{path.relative_to(ROOT)} missing clarity boundary: {phrase}")


def check_context_budget_structure() -> None:
    required_files = [
        ROOT / "SKILL-GRAPH.md",
        ROOT / "comp-frontend-ui-design" / "references" / "ux-guidelines.md",
        ROOT / "comp-frontend-ui-design" / "references" / "delivery-checklist.md",
        ROOT / "comp-frontend-ui-design" / "references" / "professional-ui-rules.md",
        ROOT / "comp-design-sync" / "references" / "architecture-escalation.md",
        ROOT / "comp-visual-prototype" / "references" / "examples.md",
    ]
    for path in required_files:
        if not path.exists():
            fail(f"{path.relative_to(ROOT)} missing context-budget reference")

    frontend_skill = ROOT / "comp-frontend-ui-design" / "SKILL.md"
    frontend_text = frontend_skill.read_text(encoding="utf-8-sig")
    frontend_lines = frontend_text.splitlines()
    if len(frontend_lines) > 190:
        fail("comp-frontend-ui-design/SKILL.md should keep long UI rules in references")
    for phrase in [
        "references/ux-guidelines.md",
        "references/delivery-checklist.md",
        "references/professional-ui-rules.md",
        "不可让步 UI Gate",
    ]:
        if phrase not in frontend_text:
            fail(f"comp-frontend-ui-design/SKILL.md missing context-budget phrase: {phrase}")

    using_text = (ROOT / "using-tws" / "SKILL.md").read_text(encoding="utf-8-sig")
    for phrase in [
        "PLATFORM-SUPPORT.md",
        "checkpoint-reference.md",
        "上下文预算",
    ]:
        if phrase not in using_text:
            fail(f"using-tws/SKILL.md missing compact reference: {phrase}")

    index_text = (ROOT / "SKILL-INDEX.md").read_text(encoding="utf-8-sig")
    if "SKILL-GRAPH.md" not in index_text:
        fail("SKILL-INDEX.md should link to SKILL-GRAPH.md instead of inlining the graph")


def check_entry_activation_chain() -> None:
    using_text = (ROOT / "using-tws" / "SKILL.md").read_text(encoding="utf-8-sig")
    for phrase in [
        "入口读取链",
        "不可停在入口",
        ".tws/project-map.md",
        ".tws/platform-skills.md",
        "flow-*/SKILL.md",
        "comp-subagent-dispatch/SKILL.md",
        "不得假装 TWS 已完整生效",
    ]:
        if phrase not in using_text:
            fail(f"using-tws/SKILL.md missing activation-chain phrase: {phrase}")

    init_text = (ROOT / "tws-init" / "SKILL.md").read_text(encoding="utf-8-sig")
    platform_text = (ROOT / "PLATFORM-SUPPORT.md").read_text(encoding="utf-8-sig")
    for rel, text in {
        "tws-init/SKILL.md": init_text,
        "PLATFORM-SUPPORT.md": platform_text,
    }.items():
        for phrase in [
            "TWS:BEGIN",
            "TWS:END",
            "tws-version",
            "TWS version",
            "skill source root",
            "updated_at",
        ]:
            if phrase not in text:
                fail(f"{rel} missing managed-entry/version phrase: {phrase}")

    for rel in ["README.md", "AGENTS.md", "CLAUDE.md"]:
        text = (ROOT / rel).read_text(encoding="utf-8-sig")
        for phrase in ["TWS:BEGIN", "TWS:END", "tws-version"]:
            if phrase not in text:
                fail(f"{rel} should document TWS managed block refresh: {phrase}")


def check_frontend_ui_data() -> None:
    skill_dir = ROOT / "comp-frontend-ui-design"
    script = skill_dir / "scripts" / "search.py"
    core = skill_dir / "scripts" / "core.py"
    design_system = skill_dir / "scripts" / "design_system.py"
    if not script.exists():
        fail("comp-frontend-ui-design/scripts/search.py missing")
        return
    if not core.exists():
        fail("comp-frontend-ui-design/scripts/core.py missing")
        return
    if not design_system.exists():
        fail("comp-frontend-ui-design/scripts/design_system.py missing")
        return

    require_phrases(
        "comp-frontend-ui-design/scripts/design_system.py",
        FRONTEND_UI_GATE_SCRIPT_PHRASES,
        "UI Gate phrase",
    )

    spec = importlib.util.spec_from_file_location("frontend_ui_core", core)
    if spec is None or spec.loader is None:
        fail("Unable to load comp-frontend-ui-design/scripts/core.py")
        return
    module = importlib.util.module_from_spec(spec)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode

    for csv_path in sorted((skill_dir / "data").glob("*.csv")):
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                fail(f"{csv_path.relative_to(ROOT)} is empty")
                continue
            if not header:
                fail(f"{csv_path.relative_to(ROOT)} has empty header")

    def check_columns(rel_file: str, columns: list[str]) -> None:
        csv_path = skill_dir / "data" / rel_file
        if not csv_path.exists():
            fail(f"{csv_path.relative_to(ROOT)} missing")
            return
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                fail(f"{csv_path.relative_to(ROOT)} is empty")
                return
        missing = [col for col in columns if col not in header]
        if missing:
            fail(f"{csv_path.relative_to(ROOT)} missing columns: {', '.join(missing)}")

    for config in module.CSV_CONFIG.values():
        check_columns(config["file"], config["search_cols"] + config["output_cols"])
    for config in module.STACK_CONFIG.values():
        check_columns(config["file"], module._STACK_COLS["search_cols"] + module._STACK_COLS["output_cols"])

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-B", str(script), "dashboard accessibility", "--domain", "ux", "-n", "1"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if result.returncode != 0:
        fail("frontend-ui-design search.py smoke test failed: " + result.stderr.strip())


def main() -> int:
    check_skill_files()
    check_stop_tags()
    check_index_paths()
    check_no_bytecode_artifacts()
    check_platform_docs()
    check_platform_activation_fingerprint()
    check_skill_reference_resolution()
    check_flow_handoffs_and_closeout()
    check_context_recovery_protocol()
    check_bootstrap_generator()
    check_rule_clarity()
    check_context_budget_structure()
    check_entry_activation_chain()
    check_frontend_ui_data()

    if ERRORS:
        print("Skill validation failed:")
        for error in ERRORS:
            print(f"- {error}")
        return 1

    print("Skill validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
