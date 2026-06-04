"""FastAPI framework route resolver."""

from __future__ import annotations

import hashlib
import os
import re
import json

from .base import BaseFrameworkResolver

# HTTP methods to detect
HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")


class FastAPIResolver(BaseFrameworkResolver):
    framework_name = "fastapi"
    languages = ["python"]

    def detect(self, root_dir: str) -> bool:
        """Check if FastAPI is used in the project."""
        # Check requirements.txt
        req_path = os.path.join(root_dir, "requirements.txt")
        if os.path.isfile(req_path):
            try:
                with open(req_path, "r", encoding="utf-8") as f:
                    if "fastapi" in f.read().lower():
                        return True
            except OSError:
                pass

        # Check pyproject.toml
        pp_path = os.path.join(root_dir, "pyproject.toml")
        if os.path.isfile(pp_path):
            try:
                with open(pp_path, "r", encoding="utf-8") as f:
                    if "fastapi" in f.read().lower():
                        return True
            except OSError:
                pass

        # Quick scan: grep for fastapi import in Python files
        try:
            for root, dirs, files in os.walk(root_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                           ("node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tws")]
                for fname in files:
                    if fname.endswith(".py"):
                        fpath = os.path.join(root, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read(4096)
                                if "fastapi" in content.lower():
                                    return True
                        except OSError:
                            continue
        except OSError:
            pass

        return False

    def extract_routes(self, root_dir: str, queries) -> dict:
        """Scan Python files for FastAPI route decorators."""
        nodes = []
        edges = []

        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in
                       ("node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tws")]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except OSError:
                    continue

                rel_path = os.path.relpath(fpath, root_dir).replace("\\", "/")

                result = self._extract_file_routes(content, fpath, rel_path)
                nodes.extend(result["nodes"])
                edges.extend(result["edges"])

        return {"nodes": nodes, "edges": edges}

    def _extract_file_routes(self, content: str, full_path: str, rel_path: str) -> dict:
        """Extract FastAPI route decorators from a single Python file."""
        nodes = []
        edges = []

        lines = content.split("\n")
        # Pattern: @<router>.<method>("<path>", ...)
        route_pat = re.compile(
            r'@(\w+)\.(' + "|".join(HTTP_METHODS) + r')\s*\(\s*[\'"]([^\'"]*)[\'"]'
        )

        for i, line in enumerate(lines):
            m = route_pat.search(line)
            if not m:
                # Also try without dot: @app.get("/path")
                continue

            router_var = m.group(1)
            method = m.group(2).upper()
            path = m.group(3)
            line_num = i + 1

            # Look ahead for function definition on next 1-3 lines
            handler_name = self._find_handler(lines, i)

            if not handler_name:
                # Decorator might be on same line as def
                def_match = re.match(r'\s*@.*?\.\w+\(.*?\)\s*(?:async\s+)?def\s+(\w+)\s*\(', line)
                if def_match:
                    handler_name = def_match.group(1)

            if not handler_name:
                continue

            route_name = f"{method} {path}"
            qname = f"{rel_path}::{handler_name}::__route__{method}{path}"
            nid = hashlib.sha256(f"{rel_path}:{qname}".encode()).hexdigest()[:32]

            node = {
                "id": nid,
                "kind": "route",
                "name": route_name,
                "qualified_name": qname,
                "file_path": rel_path,
                "language": "python",
                "start_line": line_num,
                "end_line": line_num,
                "signature": f"{method} {path}",
                "docstring": f"FastAPI route: handler={handler_name}",
                "visibility": "public",
                "is_abstract": 0,
                "is_exported": 1,
                "decorators": json.dumps([f"@{router_var}.{method.lower()}"], ensure_ascii=False),
                "framework": "fastapi",
            }
            nodes.append(node)

            # Edge: route → handler function
            handler_qname = f"{rel_path}::{handler_name}"
            handler_id = hashlib.sha256(
                f"{rel_path}:{handler_qname}".encode()
            ).hexdigest()[:32]

            edge = {
                "source": nid,
                "target": handler_id,
                "target_text": handler_qname,
                "kind": "references",
                "source_loc": f"{rel_path}:{line_num}",
                "provenance": "tree-sitter",
            }
            edges.append(edge)

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _find_handler(lines: list[str], decorator_line: int) -> str | None:
        """Look for function definition after a decorator line."""
        for offset in range(1, 4):
            idx = decorator_line + offset
            if idx >= len(lines):
                break
            candidate = lines[idx].strip()
            # Skip comments, blank lines, and additional decorators
            if not candidate or candidate.startswith("#"):
                continue
            if candidate.startswith("@"):
                continue
            # Match def handler_name(...) or async def handler_name(...)
            m = re.match(r'(?:async\s+)?def\s+(\w+)\s*\(', candidate)
            if m:
                return m.group(1)
            break  # non-blank, non-decorator line that's not a def = stop looking
        return None
