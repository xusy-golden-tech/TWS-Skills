"""File scanner — enumerates source files for indexing.

Ported from CodeGraph: prefers git ls-files, falls back to os.walk.
File extensions are derived from the extractor registry — no hardcoded list.
"""

import os
import subprocess
from typing import Optional

from .registry import get_all_extensions

# Directories to always skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt", "target",  # Java build
    ".tws", ".claude", ".codegraph",
    "coverage", ".tox", ".mypy_cache", ".pytest_cache",
    "egg-info", ".eggs",
}


def scan_directory(root: str) -> list[str]:
    """Return a sorted list of relative source file paths under root.

    Tries git ls-files first; falls back to os.walk.
    """
    root = os.path.abspath(root)

    git_files = _scan_git(root)
    if git_files is not None:
        return git_files

    return _scan_fs(root)


def _scan_git(root: str) -> Optional[list[str]]:
    """Use `git ls-files` to get tracked source files (respects .gitignore)."""
    try:
        result = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        files = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            ext = os.path.splitext(line)[1].lower()
            if ext in get_all_extensions() and _not_skipped(line):
                files.append(line)
        files.sort()
        return files
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _scan_fs(root: str) -> list[str]:
    """os.walk fallback: collect all source files, skipping known noise dirs."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in get_all_extensions():
                continue
            rel_path = f"{rel_dir}/{fname}" if rel_dir else fname
            if _not_skipped(rel_path):
                files.append(rel_path)
    files.sort()
    return files


def _not_skipped(rel_path: str) -> bool:
    """Check if path-parts overlap with skip dirs."""
    parts = rel_path.replace("\\", "/").split("/")
    return not any(p in SKIP_DIRS for p in parts)
