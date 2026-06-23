"""PHP LSP adapter for Intelephense.

Implements import parsing for PHP syntax (``use Namespace\\Class;``,
``require 'file.php';``) and namespace-to-path resolution.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter


class PhpLspAdapter(LspLanguageAdapter):
    """Adapter for the Intelephense language server."""

    language = "php"

    def get_server_command(self, workspace_root: str) -> list[str]:
        """Return the command to launch Intelephense over stdio."""
        return ["intelephense", "--stdio"]

    def get_file_extensions(self) -> list[str]:
        """Return PHP file extensions."""
        return [".php"]

    def check_availability(self) -> bool:
        """Check whether intelephense is available in PATH."""
        try:
            return shutil.which("intelephense") is not None
        except OSError:
            return False

    def parse_import(
        self, import_statement: str, current_file: str
    ) -> ImportInfo:
        """Parse a PHP use/require/include statement.

        Handles ``use Namespace\\ClassName;`` and ``require 'file.php';``.

        Args:
            import_statement: The raw import statement text.
            current_file: Absolute path of the file (unused, accepted for
                interface compatibility).

        Returns:
            Structured :class:`ImportInfo`.
        """
        s = import_statement.strip().rstrip(";").strip()

        # use Namespace\Class as Alias;
        use_match = re.match(r"use\s+([\w\\]+)(?:\s+as\s+(\w+))?", s)
        if use_match:
            module = use_match.group(1)
            alias = use_match.group(2) if use_match.lastindex and use_match.lastindex >= 2 else None
            symbol = module.rsplit("\\", 1)[-1] if "\\" in module else module
            return ImportInfo(
                module=module,
                symbol=symbol,
                is_relative=False,
                alias=alias,
            )

        # require / require_once / include / include_once
        req_match = re.match(
            r"(?:require(?:_once)?|include(?:_once)?)\s+['\"]([^'\"]+)['\"]", s
        )
        if req_match:
            path = req_match.group(1)
            return ImportInfo(
                module=path,
                symbol=os.path.basename(path).rsplit(".", 1)[0],
                is_relative=path.startswith("./") or path.startswith("../"),
                alias=None,
            )

        # Unrecognised pattern
        return ImportInfo(module=s, symbol=s, is_relative=False, alias=None)

    def resolve_module_path(
        self, module_name: str, current_file: str
    ) -> Optional[str]:
        """Resolve a PHP namespace to a file-system path.

        Converts a PHP namespace (e.g. ``App\\Services\\OrderService``) to
        a path by replacing backslashes with directory separators and
        appending ``.php``.

        Args:
            module_name: Namespace-qualified class name.
            current_file: Absolute path of the file requesting the class.

        Returns:
            Absolute path to the resolved file, or ``None`` if not found.
        """
        current_dir = os.path.dirname(os.path.abspath(current_file))
        parts = module_name.split("\\")
        php_path = os.path.join(current_dir, *parts) + ".php"
        if os.path.isfile(php_path):
            return php_path
        return None
