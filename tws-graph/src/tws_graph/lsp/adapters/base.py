"""Abstract base class for LSP language adapters and the ImportInfo data class.

Provides the common interface that every language-specific LSP adapter must
implement: server command, import parsing, module path resolution, file
extension list, and availability check.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ImportInfo:
    """Information about an import statement parsed from source code.

    Attributes:
        module: The module path being imported from.
        symbol: The specific symbol being imported (may equal module
            for plain ``import X`` statements, or be ``*`` for namespace
            imports, or ``\"\"`` for side-effect imports).
        is_relative: Whether the import uses a relative path / dot-prefix.
        alias: The local alias assigned via ``as``, or None.
    """

    module: str
    symbol: str
    is_relative: bool
    alias: Optional[str] = None


class LspLanguageAdapter(abc.ABC):
    """Abstract base class for language-specific LSP server adapters.

    Each concrete adapter provides the knowledge needed to launch and
    interact with a language's LSP server, parse its import syntax, and
    resolve module references to file paths.

    Subclasses must set the ``language`` class attribute and implement all
    five abstract methods.
    """

    language: str

    @abc.abstractmethod
    def get_server_command(self, workspace_root: str) -> list[str]:
        """Return the command line to launch the LSP server process.

        Args:
            workspace_root: Absolute path to the workspace root directory.

        Returns:
            A list of command-line tokens suitable for ``subprocess.Popen``.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def parse_import(
        self, import_statement: str, current_file: str
    ) -> ImportInfo:
        """Parse a single import statement into structured :class:`ImportInfo`.

        Args:
            import_statement: The raw import statement text (one line).
            current_file: Absolute path of the file containing the import.

        Returns:
            Parsed import information.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def resolve_module_path(
        self, module_name: str, current_file: str
    ) -> Optional[str]:
        """Resolve a module name to a file-system path.

        Args:
            module_name: The module/package name (dotted or relative).
            current_file: Absolute path of the file requesting the module.

        Returns:
            Absolute path to the resolved file, or ``None`` if unresolvable.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get_file_extensions(self) -> list[str]:
        """Return the list of file extensions this language handles.

        Returns:
            A list of extensions with leading dot, e.g. ``['.py', '.pyi']``.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def check_availability(self) -> bool:
        """Check whether the LSP server binary is available in PATH.

        Returns:
            ``True`` if the server is available, ``False`` otherwise.
        """
        raise NotImplementedError
