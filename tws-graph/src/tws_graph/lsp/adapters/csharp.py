"""C# LSP adapter for OmniSharp.

Implements import parsing for C# syntax (``using Namespace;``,
``using static Namespace.Class;``) and namespace-to-path resolution.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter


class CSharpLspAdapter(LspLanguageAdapter):
    """Adapter for the OmniSharp language server."""

    language = "csharp"

    def get_server_command(self, workspace_root: str) -> list[str]:
        """Return the command to launch OmniSharp over stdio."""
        return ["omnisharp", "--languageserver"]

    def get_file_extensions(self) -> list[str]:
        """Return C# file extensions."""
        return [".cs"]

    def check_availability(self) -> bool:
        """Check whether omnisharp is available in PATH."""
        try:
            return shutil.which("omnisharp") is not None
        except OSError:
            return False

    def parse_import(
        self, import_statement: str, current_file: str
    ) -> ImportInfo:
        """Parse a C# using directive.

        Handles ``using System;``, ``using System.Collections.Generic;``,
        and ``using static System.Math;``.

        Args:
            import_statement: The raw using statement text.
            current_file: Absolute path of the file (unused, accepted for
                interface compatibility).

        Returns:
            Structured :class:`ImportInfo`.
        """
        s = import_statement.strip().rstrip(";").strip()

        # Using static: using static Namespace.Class;
        static_match = re.match(
            r"using\s+static\s+([\w.]+)", s
        )
        if static_match:
            module = static_match.group(1)
            return ImportInfo(
                module=module,
                symbol=module.rsplit(".", 1)[-1] if "." in module else module,
                is_relative=False,
                alias=None,
            )

        # Regular using: using Namespace.SubNamespace;
        using_match = re.match(r"using\s+([\w.]+)", s)
        if using_match:
            module = using_match.group(1)
            return ImportInfo(
                module=module,
                symbol=module.rsplit(".", 1)[-1] if "." in module else module,
                is_relative=False,
                alias=None,
            )

        # Unrecognised pattern
        return ImportInfo(module=s, symbol=s, is_relative=False, alias=None)

    def resolve_module_path(
        self, module_name: str, current_file: str
    ) -> Optional[str]:
        """Resolve a C# namespace to a file-system path.

        Converts a dotted namespace (e.g. ``SampleApp.Services``) to a
        path by replacing dots with directory separators and looking for
        ``.cs`` files in that directory.

        Args:
            module_name: Dotted namespace name.
            current_file: Absolute path of the file requesting the namespace.

        Returns:
            Absolute path to the resolved file, or ``None`` if not found.
        """
        current_dir = os.path.dirname(os.path.abspath(current_file))
        parts = module_name.split(".")
        # Try to find a .cs file with the last part as name
        cs_path = os.path.join(current_dir, *parts) + ".cs"
        if os.path.isfile(cs_path):
            return cs_path
        # Try the directory itself
        dir_path = os.path.join(current_dir, *parts)
        if os.path.isdir(dir_path):
            return dir_path
        return None
