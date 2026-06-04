"""Language detection from file extension — driven by extractor registry."""

import os

from .registry import get_extractor


def detect_language(file_path: str) -> str:
    """Map a file path to a language name string.

    Looks up the file extension in the extractor registry.
    Returns ``"unknown"`` when no registered extractor handles it.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if not ext:
        return "unknown"
    extractor = get_extractor(ext)
    return extractor.language_name if extractor else "unknown"
