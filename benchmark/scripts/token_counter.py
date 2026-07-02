#!/usr/bin/env python3
"""
Token counter for Claude Code session logs.

Extracts token statistics from Claude Code session output and computes
delta tokens relative to a baseline.

Usage:
    python token_counter.py < session_log.txt
    python token_counter.py session_log.txt
    python token_counter.py session_log.txt --baseline baseline_log.txt
"""

import json
import re
import sys
from pathlib import Path


def extract_tokens_from_text(text: str) -> dict:
    """Extract token counts from Claude Code session log text.

    Looks for patterns like:
        input_tokens: 1234
        output_tokens: 567
        cache_creation_input_tokens: 0
        cache_read_input_tokens: 890
        total_tokens: 2691
    """
    patterns = {
        "input_tokens": r"\binput_tokens:\s*(\d+)",
        "output_tokens": r"\boutput_tokens:\s*(\d+)",
        "cache_creation_input_tokens": r"\bcache_creation_input_tokens:\s*(\d+)",
        "cache_read_input_tokens": r"\bcache_read_input_tokens:\s*(\d+)",
    }

    result = {}
    for key, pattern in patterns.items():
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            # Sum all occurrences (in case of multiple rounds)
            result[key] = sum(int(m) for m in matches)
        else:
            result[key] = 0

    result["total_tokens"] = (
        result["input_tokens"] + result["output_tokens"]
    )
    return result


def extract_token_headers(text: str) -> dict:
    """Also try extracting from JSON-format token headers.

    Some session logs contain structured JSON blocks with token info.
    """
    patterns = {
        "total_tokens": r'"total_tokens"\s*:\s*(\d+)',
        "total_input_tokens": r'"total_input_tokens"\s*:\s*(\d+)',
        "total_output_tokens": r'"total_output_tokens"\s*:\s*(\d+)',
    }
    result = {}
    for key, pattern in patterns.items():
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            result[key] = sum(int(m) for m in matches)
    return result


def compute_delta(current: dict, baseline: dict) -> dict:
    """Compute delta between current and baseline token counts."""
    delta = {}
    for key, value in current.items():
        base_val = baseline.get(key, 0)
        delta[key] = value - base_val
        delta[f"{key}_baseline"] = base_val
        delta[f"{key}_current"] = value
    return delta


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract token statistics from Claude Code session logs."
    )
    parser.add_argument(
        "input", nargs="?", default=None,
        help="Session log file path (reads from stdin if not provided)"
    )
    parser.add_argument(
        "--baseline", "-b", default=None,
        help="Baseline session log file for delta computation"
    )
    parser.add_argument(
        "--json", "-j", action="store_true",
        help="Output in JSON format (default: human-readable)"
    )
    parser.add_argument(
        "--summary", "-s", action="store_true",
        help="Print summary statistics only"
    )
    args = parser.parse_args()

    # Read input
    if args.input:
        text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    else:
        text = sys.stdin.read()

    # Extract tokens
    tokens = extract_tokens_from_text(text)
    json_tokens = extract_token_headers(text)

    # Merge (prefer regex patterns over JSON)
    all_tokens = {**json_tokens, **tokens}

    # Read baseline if provided
    baseline_tokens = {}
    if args.baseline:
        baseline_text = Path(args.baseline).read_text(encoding="utf-8", errors="replace")
        baseline_tokens = extract_tokens_from_text(baseline_text)
        baseline_json = extract_token_headers(baseline_text)
        baseline_tokens = {**baseline_json, **baseline_tokens}

    # Compute delta
    delta = compute_delta(all_tokens, baseline_tokens) if args.baseline else {}

    # Output
    output = {
        "tokens": all_tokens,
        "delta": delta if delta else None,
    }

    if args.json:
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print("=" * 60)
        print("Token Statistics")
        print("=" * 60)
        for key, value in all_tokens.items():
            print(f"  {key:35s}: {value:>10,}")
        print("-" * 60)
        print(f"  {'total_tokens':35s}: {all_tokens.get('total_tokens', 0):>10,}")
        print("=" * 60)

        if delta:
            print()
            print("=" * 60)
            print("Delta vs Baseline")
            print("=" * 60)
            for key in ["input_tokens", "output_tokens",
                        "cache_creation_input_tokens", "cache_read_input_tokens",
                        "total_tokens"]:
                current_val = all_tokens.get(key, 0)
                base_val = baseline_tokens.get(key, 0)
                d = current_val - base_val
                print(f"  {key:35s}: {d:>+10,}  (base: {base_val:>10,} -> current: {current_val:>10,})")
            print("=" * 60)


if __name__ == "__main__":
    main()
