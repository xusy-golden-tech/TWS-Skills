"""Extractor registry — auto-discovery via pkgutil.

Adding a new language requires only:
  1. Create extractors/<lang>.py with a BaseExtractor subclass
  2. That's it.

No changes to parser.py, language_detect.py, or scanner.py.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseExtractor


_EXTRACTORS_BY_EXT: dict[str, BaseExtractor] = {}
_EXTRACTORS_BY_LANG: dict[str, BaseExtractor] = {}
_REGISTRY_LOADED = False


def _discover() -> None:
    """Scan ``tws_graph.indexer.extractors`` for BaseExtractor subclasses."""
    global _EXTRACTORS_BY_EXT, _EXTRACTORS_BY_LANG, _REGISTRY_LOADED
    if _REGISTRY_LOADED:
        return

    try:
        import tws_graph.indexer.extractors as pkg
    except ImportError:
        _REGISTRY_LOADED = True
        return

    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module(f"tws_graph.indexer.extractors.{name}")
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (
                isinstance(attr, type)
                and attr.__name__ != "BaseExtractor"
                and hasattr(attr, "extensions")
                and hasattr(attr, "tree_sitter_languages")
            ):
                instance = attr()
                for ext in instance.extensions:
                    _EXTRACTORS_BY_EXT[ext] = instance
                for lang in instance.tree_sitter_languages:
                    _EXTRACTORS_BY_LANG[lang] = instance

    _REGISTRY_LOADED = True


def get_extractor(language_or_ext: str):
    """Get registered extractor by tree-sitter language name OR file extension.

    Returns a BaseExtractor instance or None.
    """
    _discover()
    if language_or_ext.startswith("."):
        return _EXTRACTORS_BY_EXT.get(language_or_ext)
    return _EXTRACTORS_BY_LANG.get(language_or_ext)


def get_all_extensions() -> set[str]:
    """All source file extensions with registered extractors."""
    _discover()
    return set(_EXTRACTORS_BY_EXT.keys())


def get_all_languages() -> set[str]:
    """All tree-sitter language names with registered extractors."""
    _discover()
    return set(_EXTRACTORS_BY_LANG.keys())


def is_language_supported(lang_or_ext: str) -> bool:
    """Check whether a language or extension has a registered extractor."""
    return get_extractor(lang_or_ext) is not None
