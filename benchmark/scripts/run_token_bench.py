#!/usr/bin/env python3
"""
Clean comparison: Grep-based vs tws-graph-based token consumption.
Measures INPUT tokens only — the context fed to the model, not output.

Methodology:
  Same task ("find all callers of CodingTaskService"), two approaches:
  A) Grep mode: grep for the symbol, read representative file snippets
  B) tws-graph mode: single structured query result

We count the input tokens for each approach's final context window.
"""

import subprocess
import json
import os

PROJECT = "D:/g-ass-source"
DB = f"{PROJECT}/.tws/codegraph/index.db"
SYMBOL = "CodingTaskService"


def run(cmd, **kwargs):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


def count_tokens(text):
    """Approximate token count: ~1 token per 3.5 chars for English, ~1 per 1.5 for code."""
    return len(text) // 3


def get_twsgraph_context():
    """Get tws-graph approach context."""
    result = run(
        f'tws-graph calls {SYMBOL} --inbound --depth 2 --exclude "tests/" --db "{DB}"',
        cwd=PROJECT,
    )
    return f"""## tws-graph 查询结果

命令: tws-graph calls {SYMBOL} --inbound --depth 2 --exclude "tests/"

输出 ({len(result.stdout.splitlines())} 行):
{result.stdout}
"""


def get_grep_context():
    """Simulate grep approach context — what an agent would accumulate."""
    # Step 1: grep for the symbol name
    grep_result = run(
        f'grep -rn "{SYMBOL}" g_assistant_backend/ --include="*.py" -l 2>nul',
        cwd=PROJECT,
    )
    files_with_symbol = [f for f in grep_result.stdout.splitlines() if f.strip()]

    # Step 2: grep with line numbers (limited to first 80 matches)
    grep_lines = run(
        f'grep -rn "{SYMBOL}" g_assistant_backend/ --include="*.py" 2>nul | head -80',
        cwd=PROJECT,
    )

    # Step 3: simulate reading key files (first 40 lines of each relevant file to find imports/defs)
    snippets = []
    key_files = [
        "g_assistant_backend/engine/coding_task_service.py",
        "g_assistant_backend/api/coding_task_routes.py",
        "g_assistant_backend/engine/coding_task_service_resolver.py",
        "g_assistant_backend/engine/chat_coding_task_loop.py",
        "g_assistant_backend/message_processor.py",
    ]
    for f in key_files[:4]:  # limit to 4 files (common agent behavior)
        filepath = os.path.join(PROJECT, f)
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            # Take ~200 lines from around where CodingTaskService is used
            lines = content.splitlines()
            snippet = "\n".join(lines[:200])
            snippets.append(f"### {f} (前 200 行):\n{snippet[:8000]}")

    return f"""## Grep 调查过程

### Grep 查找 "{SYMBOL}" 文件列表:
发现 {len(files_with_symbol)} 个文件包含该符号:
{chr(10).join(f'- {f}' for f in files_with_symbol[:15])}

### Grep 匹配行 (前 80 条):
{grep_lines.stdout[:6000]}

### 关键文件内容 (Read):
{chr(10).join(snippets[:3])}
"""


def main():
    print("=" * 70)
    print("Token 消耗对比: Grep vs tws-graph")
    print(f"任务: 找出 {SYMBOL} 类的所有调用者")
    print("=" * 70)

    # Get contexts
    twsgraph_ctx = get_twsgraph_context()
    grep_ctx = get_grep_context()

    # Count tokens
    twsgraph_tokens = count_tokens(twsgraph_ctx)
    grep_tokens = count_tokens(grep_ctx)

    # Measure actual byte sizes
    twsgraph_bytes = len(twsgraph_ctx.encode("utf-8"))
    grep_bytes = len(grep_ctx.encode("utf-8"))

    print(f"\n{'':>25} {'tws-graph':>15} {'Grep':>15} {'节省率':>15}")
    print(f"{'':->70}")
    print(f"{'Context 字符数':>25} {len(twsgraph_ctx):>15,} {len(grep_ctx):>15,} {((len(grep_ctx)-len(twsgraph_ctx))/len(grep_ctx)*100):>14.1f}%")
    print(f"{'Context 字节数':>25} {twsgraph_bytes:>15,} {grep_bytes:>15,} {((grep_bytes-twsgraph_bytes)/grep_bytes*100):>14.1f}%")
    print(f"{'估算 Token 数':>25} {twsgraph_tokens:>15,} {grep_tokens:>15,} {((grep_tokens-twsgraph_tokens)/grep_tokens*100):>14.1f}%")
    print(f"{'tws-graph 输出行数':>25} {len(twsgraph_ctx.splitlines()):>15,}")
    print(f"{'Grep 涉及文件数':>25} {len(grep_ctx.splitlines()):>15,}")

    # Actual Anthropic API token count if API key is available
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)

            # Use the token counting endpoint
            resp_tws = client.messages.count_tokens(
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": twsgraph_ctx}],
            )
            resp_grep = client.messages.count_tokens(
                model="claude-sonnet-4-20250514",
                messages=[{"role": "user", "content": grep_ctx}],
            )

            tws_real = resp_tws.input_tokens
            grep_real = resp_grep.input_tokens

            print(f"\n{'':->70}")
            print(f"{'API 精确 Token 数':>25} {tws_real:>15,} {grep_real:>15,} {((grep_real-tws_real)/grep_real*100):>14.1f}%")
            print(f"\n结论: tws-graph 的 context 比 Grep 少 {grep_real - tws_real:,} 个 input tokens ({((grep_real-tws_real)/grep_real*100):.1f}%)")

        except Exception as e:
            print(f"\nAPI token 计数失败: {e}")
    else:
        print(f"\n(未设置 ANTHROPIC_API_KEY，使用估算)")

    # Save detailed context to files for inspection
    os.makedirs("benchmark/results", exist_ok=True)
    with open("benchmark/results/grep_context.txt", "w", encoding="utf-8") as f:
        f.write(grep_ctx)
    with open("benchmark/results/twsgraph_context.txt", "w", encoding="utf-8") as f:
        f.write(twsgraph_ctx)
    print(f"\n详细 context 已保存到 benchmark/results/{{grep,twsgraph}}_context.txt")


if __name__ == "__main__":
    main()
