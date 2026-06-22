"""Python LSP adapter for pyright-langserver.

Implements import parsing for Python syntax (``import X``,
``from Y import Z``, relative imports) and module-to-path resolution
(.py, .pyi, __init__.py).
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter


class PythonLspAdapter(LspLanguageAdapter):
    """Adapter for the Pyright language server (pyright-langserver)."""

    language = "python"

    def get_server_command(self, workspace_root: str) -> list[str]:
        """Return the command to launch pyright-langserver over stdio."""
        return ["pyright-langserver", "--stdio"]

    def get_file_extensions(self) -> list[str]:
        """Return Python file extensions."""
        return [".py", ".pyi"]

    def check_availability(self) -> bool:
        """Check whether pyright-langserver is available in PATH."""
        try:
            return shutil.which("pyright-langserver") is not None
        except OSError:
            return False

    def parse_import(
        self, import_statement: str, current_file: str
    ) -> ImportInfo:
        """Parse a Python import statement.

        Handles ``import X [as Y]``, ``from X import Y [as Z]``, and
        relative imports (``from . import``, ``from ..pkg import``).
        For multi-symbol imports only the first symbol is returned.

        Args:
            import_statement: The raw import statement text.
            current_file: Absolute path of the file (unused, accepted for
                interface compatibility).

        Returns:
            Structured :class:`ImportInfo`.
        """
        s = import_statement.strip()

        # from X import Y [as Z]
        from_match = re.match(r"from\s+([.\w]+)\s+import\s+(.+)", s)
        if from_match:
            module = from_match.group(1)
            rest = from_match.group(2).strip()

            # Extract the first imported name
            first_import_match = re.match(r"(\w+)(?:\s+as\s+(\w+))?", rest)
            if first_import_match:
                symbol = first_import_match.group(1)
                alias = first_import_match.group(2)
            else:
                symbol = rest
                alias = None

            is_relative = module.startswith(".")
            return ImportInfo(
                module=module, symbol=symbol, is_relative=is_relative, alias=alias
            )

        # import X [as Y]
        import_match = re.match(
            r"import\s+([\w.]+)(?:\s+as\s+(\w+))?", s
        )
        if import_match:
            module = import_match.group(1)
            alias = import_match.group(2)
            return ImportInfo(
                module=module,
                symbol=module,
                is_relative=False,
                alias=alias,
            )

        # Unrecognised pattern — return as-is
        return ImportInfo(module=s, symbol=s, is_relative=False, alias=None)

    def resolve_module_path(
        self, module_name: str, current_file: str
    ) -> Optional[str]:
        """Resolve a Python dotted module name to a file-system path.

        Tries the following in order relative to *current_file*'s directory:

        1. ``<module>.py``
        2. ``<module>/__init__.py`` (package)
        3. ``<module>.pyi`` (stub)

        Args:
            module_name: Dotted module name (e.g. ``"foo.bar"``).
            current_file: Absolute path of the file requesting the module.

        Returns:
            Absolute path to the resolved file, or ``None`` if not found.
        """
        current_dir = os.path.dirname(os.path.abspath(current_file))
        parts = module_name.split(".")

        # Try .py file
        py_path = os.path.join(current_dir, *parts) + ".py"
        if os.path.isfile(py_path):
            return py_path

        # Try package __init__.py
        init_path = os.path.join(current_dir, *parts, "__init__.py")
        if os.path.isfile(init_path):
            return init_path

        # Try .pyi stub file
        pyi_path = os.path.join(current_dir, *parts) + ".pyi"
        if os.path.isfile(pyi_path):
            return pyi_path

        return None
