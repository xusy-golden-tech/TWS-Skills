#!/usr/bin/env python3
"""
Precise token comparison: claude --print mode with realistic bug investigation.
Measures input_tokens and output_tokens SEPARATELY via --output-format json.

Grep mode: restricted to Grep + Read only
tws-graph mode: full tools with --dangerously-skip-permissions for CLI execution
"""
import subprocess, json, time, sys, os

TASK = """你是一个 bug 调查 agent。

## 背景
g-ass-source (D:/g-ass-source/g_assistant_backend) 是一个 AI coding agent 后端服务。
用户报告：调用 cancel_coding_task 后，有时候任务状态未被正确更新为 'canceled'。

## 调查任务
1. 找到 cancel_coding_task 的 API 入口和核心逻辑（具体文件+行号）
2. 追踪从 API 入口到数据库状态更新的完整调用链（画出来）
3. 找出所有可能设置/修改任务状态的地方
4. 识别可能导致状态未正确更新的潜在问题（异常吞没、条件遗漏、异步未等待等）

## 要求
- 调查要彻底，给出文件路径+行号
- 列出所有状态转换点和可能的问题点
- 给出你的判断：bug 最可能在哪个环节"""

PROMPT_GREP = TASK + """
## 工具限制
你只能使用 Grep 和 Read 工具。禁止使用 tws-graph、Glob 或其他代码导航工具。"""

PROMPT_TWS = """通过 Skill 工具加载 found-tws-graph-usage: Skill(skill="found-tws-graph-usage")

""" + TASK + """
## 工具说明
优先使用 tws-graph (search/calls/impact/trace) 进行代码调查。Grep 仅作回退手段。"""

PROMPT_TWS_BRIEF = """通过 Skill 工具加载 found-tws-graph-usage: Skill(skill="found-tws-graph-usage")

""" + TASK + """
## 工具说明
优先使用 tws-graph (search/calls/impact/trace) 进行代码调查。Grep 仅作回退手段。

## 重要：tws-graph calls 使用 --brief
每次调用 tws-graph calls 命令时必须添加 --brief flag，减少不必要的信息输出。例如：
- tws-graph calls <node> --brief
- tws-graph calls <node> --inbound --brief"""


def run_bench(mode, prompt, extra_args=None):
    """Run claude --print and return usage stats."""
    tag = f"[{mode}]"
    print(f"\n{'='*50}")
    print(f"{tag} 开始调查...")
    t0 = time.time()

    cmd = ["claude", "--print", "--output-format", "json", "--verbose"]
    if extra_args:
        cmd.extend(extra_args)

    p = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        cwd="D:/g-ass-source",
    )

    stdout, stderr = p.communicate(input=prompt, timeout=600)
    elapsed = time.time() - t0

    if p.returncode != 0:
        print(f"{tag} FAILED (exit={p.returncode})")
        print(f"STDERR: {stderr[-500:]}")
        return None

    # Claude --print with --output-format json outputs a JSON array
    last_result = None
    try:
        data = json.loads(stdout.strip())
        if isinstance(data, list):
            for obj in reversed(data):
                if isinstance(obj, dict) and obj.get("type") == "result" and "usage" in obj:
                    last_result = obj
                    break
        elif isinstance(data, dict) and data.get("type") == "result" and "usage" in data:
            last_result = data
    except json.JSONDecodeError:
        pass

    if not last_result:
        print(f"{tag} No result with usage found")
        print(f"STDOUT (last 500): {stdout[-500:]}")
        os.makedirs("benchmark/results", exist_ok=True)
        with open(f"benchmark/results/{mode}_debug_stdout.json", "w", encoding="utf-8") as f:
            f.write(stdout)
        return None

    u = last_result["usage"]
    stats = {
        "mode": mode,
        "input_tokens": u["input_tokens"],
        "output_tokens": u["output_tokens"],
        "cache_read": u.get("cache_read_input_tokens", 0),
        "cache_creation": u.get("cache_creation_input_tokens", 0),
        "num_turns": last_result.get("num_turns", 0),
        "duration_s": elapsed,
        "cost_usd": last_result.get("total_cost_usd", 0),
        "stop_reason": last_result.get("stop_reason", ""),
    }

    print(f"{tag} 完成: {stats['input_tokens']:,} in / {stats['output_tokens']:,} out "
          f"/ {stats['num_turns']} turns / {elapsed:.0f}s / ${stats['cost_usd']:.4f}")
    return stats


def main():
    results = {}

    # Run tws-graph mode (rich) — allow all tools including Bash for tws-graph CLI
    r = run_bench("tws-graph", PROMPT_TWS, extra_args=["--dangerously-skip-permissions"])
    results["twsgraph"] = r

    time.sleep(3)

    # Run tws-graph mode (--brief) — same but calls uses --brief
    r = run_bench("tws-graph-brief", PROMPT_TWS_BRIEF, extra_args=["--dangerously-skip-permissions"])
    results["twsgraph_brief"] = r

    # Print comparison
    t = results.get("twsgraph")       # rich (default)
    b = results.get("twsgraph_brief") # --brief

    if not t or not b:
        print("\n无法完成对比")
        return

    print("\n" + "=" * 65)
    print("精确 Token 对比: tws-graph rich vs --brief (真实 bug 调查)")
    print("=" * 65)

    rows = [
        ("Input tokens", "input_tokens"),
        ("Output tokens", "output_tokens"),
        ("Cache read tokens", "cache_read"),
        ("Cache creation tokens", "cache_creation"),
    ]

    for label, key in rows:
        tv, bv = t[key], b[key]
        diff = tv - bv
        pct = diff / tv * 100 if tv else 0
        print(f"  {label:<22} {tv:>10,} → {bv:>10,}  |  {diff:>+10,}  ({pct:>+5.1f}%)")

    print(f"  {'-'*55}")
    print(f"  {'Turns':<22} {t['num_turns']:>10} → {b['num_turns']:>10}")
    print(f"  {'Duration (s)':<22} {t['duration_s']:>10.1f} → {b['duration_s']:>10.1f}")

    t_total = t["input_tokens"] + t["output_tokens"]
    b_total = b["input_tokens"] + b["output_tokens"]
    diff = t_total - b_total
    pct = diff / t_total * 100
    print(f"  {'='*55}")
    print(f"  {'TOTAL tokens':<22} {t_total:>10,} → {b_total:>10,}  |  {diff:>+10,}  ({pct:>+5.1f}%)")
    print(f"  {'TOTAL cost':<22} ${t['cost_usd']:.4f} → ${b['cost_usd']:.4f}")

    print(f"\n  --brief 节省了 {diff:,} total tokens ({pct:.1f}%)")
    if b['num_turns'] > t['num_turns']:
        print(f"  [!] --brief group +{b['num_turns'] - t['num_turns']} turns vs rich (less info -> more queries)")
    else:
        print(f"  [OK] --brief group turns: {b['num_turns']} (<= rich: {t['num_turns']})")

    # Save
    os.makedirs("benchmark/results", exist_ok=True)
    with open("benchmark/results/precise_comparison.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n数据已保存: benchmark/results/precise_comparison.json")


if __name__ == "__main__":
    main()
