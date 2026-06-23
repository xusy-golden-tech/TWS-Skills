"""LSP language adapters.

Each adapter encapsulates language-specific knowledge: LSP server command,
import statement parsing, module path resolution, file extensions, and
availability checks.

Adapter auto-discovery (``get_all_adapters()``) uses ``pkgutil`` to find
concrete ``LspLanguageAdapter`` subclasses, matching the pattern used by
:mod:`tws_graph.indexer.registry`.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Type

from tws_graph.lsp.adapters.base import ImportInfo, LspLanguageAdapter
from tws_graph.lsp.adapters.java import JavaLspAdapter
from tws_graph.lsp.adapters.python import PythonLspAdapter
from tws_graph.lsp.adapters.typescript import TypeScriptLspAdapter

__all__ = [
    "ImportInfo",
    "JavaLspAdapter",
    "LspLanguageAdapter",
    "PythonLspAdapter",
    "TypeScriptLspAdapter",
    "get_all_adapters",
]

_REGISTERED_ADAPTERS: list[LspLanguageAdapter] = []


def get_all_adapters() -> list[LspLanguageAdapter]:
    """Discover and return all registered LSP language adapter instances.

    Uses ``pkgutil.iter_modules`` to scan the ``tws_graph.lsp.adapters``
    package for concrete :class:`LspLanguageAdapter` subclasses.  Each
    subclass is instantiated once; results are cached after the first call.

    Returns:
        A list of instantiated :class:`LspLanguageAdapter` objects, one per
        supported language.
    """
    global _REGISTERED_ADAPTERS

    if _REGISTERED_ADAPTERS:
        return _REGISTERED_ADAPTERS

    try:
        import tws_graph.lsp.adapters as pkg
    except ImportError:
        return []

    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module(f"tws_graph.lsp.adapters.{name}")
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                inspect.isclass(attr)
                and issubclass(attr, LspLanguageAdapter)
                and attr is not LspLanguageAdapter
                and not inspect.isabstract(attr)
            ):
                _REGISTERED_ADAPTERS.append(attr())

    return _REGISTERED_ADAPTERS
