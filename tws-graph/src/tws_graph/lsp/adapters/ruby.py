"""Ruby LSP adapter for Solargraph.

Implements import parsing for Ruby syntax (``require 'gem'``,
``require_relative 'file'``) and module-to-path resolution.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Optional

from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter


class RubyLspAdapter(LspLanguageAdapter):
    """Adapter for the Solargraph language server."""

    language = "ruby"

    def get_server_command(self, workspace_root: str) -> list[str]:
        """Return the command to launch Solargraph over stdio."""
        return ["solargraph", "stdio"]

    def get_file_extensions(self) -> list[str]:
        """Return Ruby file extensions."""
        return [".rb"]

    def check_availability(self) -> bool:
        """Check whether solargraph is available in PATH."""
        try:
            return shutil.which("solargraph") is not None
        except OSError:
            return False

    def parse_import(
        self, import_statement: str, current_file: str
    ) -> ImportInfo:
        """Parse a Ruby require/require_relative/load statement.

        Handles ``require 'json'``, ``require_relative 'helpers'``, and
        ``load 'config.rb'``.

        Args:
            import_statement: The raw import statement text.
            current_file: Absolute path of the file (unused, accepted for
                interface compatibility).

        Returns:
            Structured :class:`ImportInfo`.
        """
        s = import_statement.strip()

        # require_relative 'path'
        rel_match = re.match(r"require_relative\s+['\"]([^'\"]+)['\"]", s)
        if rel_match:
            path = rel_match.group(1)
            return ImportInfo(
                module=path,
                symbol=os.path.basename(path).rsplit(".", 1)[0],
                is_relative=True,
                alias=None,
            )

        # require 'path'
        req_match = re.match(r"require\s+['\"]([^'\"]+)['\"]", s)
        if req_match:
            path = req_match.group(1)
            return ImportInfo(
                module=path,
                symbol=os.path.basename(path).rsplit(".", 1)[0],
                is_relative=False,
                alias=None,
            )

        # load 'path'
        load_match = re.match(r"load\s+['\"]([^'\"]+)['\"]", s)
        if load_match:
            path = load_match.group(1)
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
        """Resolve a Ruby module name to a file-system path.

        Converts a Ruby module name (e.g. ``json``) to a path like
        ``<current_dir>/json.rb`` by appending ``.rb``.

        Args:
            module_name: The module name to resolve.
            current_file: Absolute path of the file requesting the module.

        Returns:
            Absolute path to the resolved file, or ``None`` if not found.
        """
        current_dir = os.path.dirname(os.path.abspath(current_file))
        rb_path = os.path.join(current_dir, module_name + ".rb")
        if os.path.isfile(rb_path):
            return rb_path
        # Try without .rb extension (for bare module names)
        alt_path = os.path.join(current_dir, module_name)
        if os.path.isfile(alt_path):
            return alt_path
        return None
