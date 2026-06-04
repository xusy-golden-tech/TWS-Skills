"""Base interface for framework route resolvers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseFrameworkResolver(ABC):
    """Interface for framework-specific route detection.

    Subclasses declare which framework they handle and implement:
    - detect(): does this project use this framework?
    - extract_routes(): find all route definitions and return nodes + edges
    """

    framework_name: str = ""
    languages: list[str] = []

    @abstractmethod
    def detect(self, root_dir: str) -> bool:
        """Return True if this framework is used in the project."""

    @abstractmethod
    def extract_routes(self, root_dir: str, queries) -> dict:
        """Extract route nodes and handler edges.

        Returns: {"nodes": [dict], "edges": [dict]}
        Each node should have: id, kind='route', name, qualified_name,
        file_path, language, start_line, end_line, signature (HTTP method+path),
        framework, and docstring (handler description).
        """
