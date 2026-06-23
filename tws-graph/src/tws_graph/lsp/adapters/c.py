"""C LSP adapter for clangd.

Implements import parsing for C #include directives and header path resolution.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter


class CLspAdapter(LspLanguageAdapter):
    """Adapter for the clangd language server (C language)."""

    language = "c"

    def get_server_command(self, workspace_root: str) -> list[str]:
        """Return the command to launch clangd over stdio."""
        return ["clangd"]

    def get_file_extensions(self) -> list[str]:
        """Return C file extensions."""
        return [".c", ".h"]

    def check_availability(self) -> bool:
        """Check whether clangd is available in PATH."""
        try:
            return shutil.which("clangd") is not None
        except OSError:
            return False

    def parse_import(
        self, import_statement: str, current_file: str
    ) -> ImportInfo:
        """Parse a C #include directive.

        Handles system includes (``#include <stdio.h>``) and local includes
        (``#include "mylib.h"``).

        Args:
            import_statement: The raw #include line.
            current_file: Absolute path of the file (unused, accepted for
                interface compatibility).

        Returns:
            Structured :class:`ImportInfo`.
        """
        s = import_statement.strip()

        # System include: #include <foo.h>
        sys_match = re.match(r'#include\s+<([^>]+)>', s)
        if sys_match:
            header = sys_match.group(1)
            return ImportInfo(
                module=header,
                symbol=header,
                is_relative=False,
                alias=None,
            )

        # Local include: #include "foo.h"
        local_match = re.match(r'#include\s+"([^"]+)"', s)
        if local_match:
            header = local_match.group(1)
            return ImportInfo(
                module=header,
                symbol=header,
                is_relative=True,
                alias=None,
            )

        # Unrecognised pattern
        return ImportInfo(module=s, symbol=s, is_relative=False, alias=None)

    def resolve_module_path(
        self, module_name: str, current_file: str
    ) -> Optional[str]:
        """Resolve a C header name to a file-system path.

        For local includes, resolves relative to ``current_file``'s directory.
        For system includes, returns ``None`` (system headers not tracked).

        Args:
            module_name: Header file name (e.g. ``"mylib.h"``).
            current_file: Absolute path of the file containing the #include.

        Returns:
            Absolute path to the resolved header file, or ``None`` if not found.
        """
        current_dir = os.path.dirname(os.path.abspath(current_file))

        # Try relative to current file
        candidate = os.path.join(current_dir, module_name)
        if os.path.isfile(candidate):
            return candidate

        # Try common include directories relative to workspace
        # Walk up to find a reasonable project root
        search_dir = current_dir
        for _ in range(5):
            parent = os.path.dirname(search_dir)
            if parent == search_dir:
                break
            search_dir = parent
            for sub in ("include", "src", "lib"):
                candidate = os.path.join(search_dir, sub, module_name)
                if os.path.isfile(candidate):
                    return candidate

        return None
