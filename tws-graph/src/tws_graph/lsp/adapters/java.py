"""Java LSP adapter for Eclipse JDT.LS (jdtls).

Implements import parsing for Java syntax (``import pkg.Class;``,
``import pkg.*;``, ``import static pkg.Class.member;``) and
dotted-module-to-path resolution (.java files in directory hierarchy).
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter


class JavaLspAdapter(LspLanguageAdapter):
    """Adapter for the Eclipse JDT.LS language server (jdtls)."""

    language = "java"

    def get_server_command(self, workspace_root: str) -> list[str]:
        """Return the command to launch jdtls over stdio."""
        return ["jdtls"]

    def get_file_extensions(self) -> list[str]:
        """Return Java file extensions."""
        return [".java"]

    def check_availability(self) -> bool:
        """Check whether jdtls is available in PATH."""
        try:
            return shutil.which("jdtls") is not None
        except OSError:
            return False

    def parse_import(
        self, import_statement: str, current_file: str
    ) -> ImportInfo:
        """Parse a Java import statement.

        Handles single-class imports (``import java.util.List;``),
        wildcard imports (``import java.util.*;``), and static imports
        (``import static java.lang.Math.PI;``, ``import static pkg.Class.*;``).

        Args:
            import_statement: The raw import statement text.
            current_file: Absolute path of the file (unused, accepted for
                interface compatibility).

        Returns:
            Structured :class:`ImportInfo`.
        """
        s = import_statement.strip().rstrip(";").strip()

        # Static import: import static <pkg.Class>.<member>
        static_match = re.match(
            r"import\s+static\s+([\w.]+)\.(\w+|\*)", s
        )
        if static_match:
            module = static_match.group(1)
            symbol = static_match.group(2)
            return ImportInfo(
                module=module,
                symbol=symbol,
                is_relative=False,
                alias=None,
            )

        # Regular import: import <pkg>.<Class or *>
        import_match = re.match(r"import\s+([\w.]+)\.(\w+|\*)", s)
        if import_match:
            module = import_match.group(1)
            symbol = import_match.group(2)
            return ImportInfo(
                module=module,
                symbol=symbol,
                is_relative=False,
                alias=None,
            )

        # Unrecognised pattern -- return as-is
        return ImportInfo(module=s, symbol=s, is_relative=False, alias=None)

    def resolve_module_path(
        self, module_name: str, current_file: str
    ) -> Optional[str]:
        """Resolve a Java dotted class name to a file-system path.

        Converts a fully-qualified class name (e.g. ``com.example.Foo``) to
        a path like ``<current_dir>/com/example/Foo.java`` by replacing dots
        with directory separators and appending ``.java``.

        Args:
            module_name: Dotted class name (e.g. ``"com.example.Foo"``).
            current_file: Absolute path of the file requesting the class.

        Returns:
            Absolute path to the resolved file, or ``None`` if not found.
        """
        current_dir = os.path.dirname(os.path.abspath(current_file))
        parts = module_name.split(".")
        java_path = os.path.join(current_dir, *parts) + ".java"
        if os.path.isfile(java_path):
            return java_path
        return None
