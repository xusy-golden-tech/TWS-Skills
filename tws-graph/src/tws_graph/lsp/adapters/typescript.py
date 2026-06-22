"""TypeScript / JavaScript LSP adapter for typescript-language-server.

Implements import parsing for ES module syntax (named, default, namespace,
side-effect, type-only imports) and module-to-path resolution (with
extension priority: .ts > .tsx > .js > .jsx > index files).
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter


class TypeScriptLspAdapter(LspLanguageAdapter):
    """Adapter for the TypeScript language server (typescript-language-server)."""

    language = "typescript"

    def get_server_command(self, workspace_root: str) -> list[str]:
        """Return the command to launch typescript-language-server over stdio."""
        return ["typescript-language-server", "--stdio"]

    def get_file_extensions(self) -> list[str]:
        """Return TypeScript / JavaScript file extensions."""
        return [".ts", ".tsx", ".js", ".jsx"]

    def check_availability(self) -> bool:
        """Check whether typescript-language-server is available in PATH."""
        try:
            return shutil.which("typescript-language-server") is not None
        except OSError:
            return False

    def parse_import(
        self, import_statement: str, current_file: str
    ) -> ImportInfo:
        """Parse a TypeScript / ES module import statement.

        Handles named (``import {X} from``), default (``import X from``),
        namespace (``import * as X from``), combined, side-effect
        (``import 'mod'``), and type-only (``import type {X} from``)
        imports.  Trailing semicolons and surrounding whitespace are
        tolerated.

        Args:
            import_statement: The raw import statement text.
            current_file: Absolute path of the file (unused, accepted for
                interface compatibility).

        Returns:
            Structured :class:`ImportInfo`.
        """
        s = import_statement.strip().rstrip(";").strip()

        # Side-effect import: import 'module'  or  import "module"
        side_match = re.match(r"""import\s+['"]([^'"]+)['"]""", s)
        if side_match:
            module = side_match.group(1)
            return ImportInfo(
                module=module,
                symbol="",
                is_relative=module.startswith("."),
                alias=None,
            )

        # General form: import [type] <bindings> from 'module'
        gen_match = re.match(
            r"""import\s+(?:type\s+)?(.+?)\s+from\s+['"]([^'"]+)['"]""",
            s,
        )
        if gen_match:
            bindings = gen_match.group(1).strip()
            module = gen_match.group(2)
            is_relative = module.startswith(".")

            # Namespace import: import * as X from ...
            ns_match = re.match(r"\*\s+as\s+(\w+)", bindings)
            if ns_match:
                return ImportInfo(
                    module=module,
                    symbol="*",
                    is_relative=is_relative,
                    alias=ns_match.group(1),
                )

            # Named import(s): import {X, Y}  or  import {X as Y}
            named_match = re.match(r"\{([^}]+)\}", bindings)
            if named_match:
                items = named_match.group(1)
                first = items.split(",")[0].strip()
                alias_match = re.match(r"(\w+)\s+as\s+(\w+)", first)
                if alias_match:
                    return ImportInfo(
                        module=module,
                        symbol=alias_match.group(1),
                        is_relative=is_relative,
                        alias=alias_match.group(2),
                    )
                else:
                    return ImportInfo(
                        module=module,
                        symbol=first,
                        is_relative=is_relative,
                        alias=None,
                    )

            # Default import (possibly with named after comma):
            #   import React, {useState} from 'react'
            comma_idx = bindings.find(",")
            if comma_idx >= 0:
                default_name = bindings[:comma_idx].strip()
            else:
                default_name = bindings.strip()

            return ImportInfo(
                module=module,
                symbol=default_name,
                is_relative=is_relative,
                alias=None,
            )

        # Unrecognised pattern — return as-is
        return ImportInfo(module=s, symbol=s, is_relative=False, alias=None)

    def resolve_module_path(
        self, module_name: str, current_file: str
    ) -> Optional[str]:
        """Resolve a TypeScript module specifier to a file-system path.

        Relative specifiers (starting with ``.``) are resolved against
        *current_file*'s directory.  Extension resolution follows the
        priority order ``.ts`` > ``.tsx`` > ``.js`` > ``.jsx`` > index
        files (e.g. ``index.ts``).

        Args:
            module_name: Module specifier (e.g. ``"./foo"``, ``"../bar"``).
            current_file: Absolute path of the file requesting the module.

        Returns:
            Absolute path to the resolved file, or ``None`` if not found.
        """
        current_dir = os.path.dirname(os.path.abspath(current_file))
        resolved_dir = os.path.normpath(
            os.path.join(current_dir, module_name)
        )

        extensions = [".ts", ".tsx", ".js", ".jsx"]

        # Try direct file match
        for ext in extensions:
            path = resolved_dir + ext
            if os.path.isfile(path):
                return path

        # Try index file under directory
        for ext in extensions:
            path = os.path.join(resolved_dir, "index" + ext)
            if os.path.isfile(path):
                return path

        return None
