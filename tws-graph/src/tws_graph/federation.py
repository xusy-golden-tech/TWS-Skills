"""Multi-repo federation registry.

Manages a ``federation.json`` file stored in ``.tws/codegraph/`` that
registers other TWS-indexed repos so that cross-repo queries (search, calls,
impact, trace) and import resolution can span multiple repositories.

Usage::

    from tws_graph.federation import FederationRegistry

    reg = FederationRegistry("/path/to/project/.tws/codegraph")
    reg.add("other-repo", "/path/to/other-repo")
    for name, info in reg.list_all().items():
        print(name, info["db"])
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_FEDERATION_FILE = "federation.json"


@dataclass
class RepoInfo:
    """Metadata for a single federated repo."""
    name: str
    path: str
    db: str


@dataclass
class FederationRegistry:
    """Reads, writes and validates the federation registry file.

    The registry lives as a JSON file inside the codegraph directory
    (``.tws/codegraph/federation.json``).  Each entry maps a logical name
    to a repo path and its index database path.

    Attributes:
        codegraph_dir: Absolute path to the ``.tws/codegraph`` directory that
            owns this federation (the *local* project).
        _path: Absolute path to the ``federation.json`` file.
        _data: In-memory cache of the JSON content.
    """

    codegraph_dir: str
    _path: str = field(init=False)
    _data: dict = field(default_factory=lambda: {"repos": {}})

    def __post_init__(self) -> None:
        self._path = os.path.join(self.codegraph_dir, DEFAULT_FEDERATION_FILE)
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load (or create) the federation registry file."""
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self._data = {"repos": {}}
                self._save()  # overwrite corrupted file with fresh empty registry
        else:
            self._data = {"repos": {}}

        # Normalise: ensure "repos" key exists
        if "repos" not in self._data:
            self._data["repos"] = {}

    def _save(self) -> None:
        """Persist the in-memory data to disk."""
        os.makedirs(self.codegraph_dir, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, name: str, repo_path: str) -> RepoInfo:
        """Register a federated repo.

        Args:
            name: Logical name for the repo (used as alias in queries).
            repo_path: Absolute path to the repo root directory.  Must contain
                ``.tws/codegraph/index.db``.

        Returns:
            RepoInfo with the registered repo details.

        Raises:
            FileNotFoundError: If ``repo_path`` does not contain a valid
                ``.tws/codegraph/index.db``.
            ValueError: If *name* is already registered under a different path.
        """
        repo_path = os.path.abspath(repo_path)
        db_path = os.path.join(repo_path, ".tws", "codegraph", "index.db")

        if not os.path.isfile(db_path):
            raise FileNotFoundError(
                f"索引数据库不存在: {db_path}\n"
                f"请先在 {repo_path} 中运行 tws-graph index。"
            )

        # Check for duplicate name
        existing = self._data["repos"].get(name)
        if existing is not None and existing.get("path") != repo_path:
            raise ValueError(
                f"已存在名为 '{name}' 的联邦仓库 (路径: {existing['path']})。"
                f" 请先运行 'tws-graph federate remove {name}' 移除。"
            )

        self._data["repos"][name] = {
            "path": repo_path,
            "db": db_path,
        }
        self._save()

        return RepoInfo(name=name, path=repo_path, db=db_path)

    def remove(self, name: str) -> None:
        """Remove a repo from the federation.

        Args:
            name: Logical name of the repo to remove.

        Raises:
            KeyError: If *name* is not registered.
        """
        if name not in self._data["repos"]:
            raise KeyError(f"未注册的联邦仓库: '{name}'")
        del self._data["repos"][name]
        self._save()

    def list_all(self) -> dict[str, dict[str, str]]:
        """Return all registered repos.

        Returns:
            Dict mapping ``{name: {"path": ..., "db": ...}}``.
        """
        return dict(self._data["repos"])

    def get(self, name: str) -> Optional[dict[str, str]]:
        """Get a single repo by name, or None if not found."""
        return self._data["repos"].get(name)

    def is_active(self) -> bool:
        """True if at least one repo is registered."""
        return len(self._data["repos"]) > 0

    def count(self) -> int:
        """Number of registered repos."""
        return len(self._data["repos"])

    # ------------------------------------------------------------------
    # Multi-DB query helpers
    # ------------------------------------------------------------------

    def attach_all(self, conn, alias_prefix: str = "fed_") -> list[str]:
        """ATTACH all federated databases to *conn*.

        Returns a list of attached schema names (aliases).
        """
        attached: list[str] = []
        for name, info in self._data["repos"].items():
            alias = alias_prefix + name.replace("-", "_")
            db_path = info["db"]
            if os.path.isfile(db_path):
                try:
                    conn.execute(f"ATTACH DATABASE '{db_path}' AS {alias}")
                    attached.append(alias)
                except Exception:
                    # Already attached or locked — skip
                    pass
        return attached

    def detach_all(self, conn, aliases: list[str]) -> None:
        """DETACH previously attached databases."""
        for alias in aliases:
            try:
                conn.execute(f"DETACH DATABASE {alias}")
            except Exception:
                pass

    def iter_federated_dbs(self) -> list[dict[str, str]]:
        """Yield ``{name, path, db}`` for every registered repo whose DB exists."""
        result: list[dict[str, str]] = []
        for name, info in self._data["repos"].items():
            db_path = info["db"]
            if os.path.isfile(db_path):
                result.append({"name": name, "path": info["path"], "db": db_path})
        return result


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def load_federation(project_root: str) -> FederationRegistry:
    """Load the federation registry for *project_root*.

    Returns a FederationRegistry even if no federation.json exists yet
    (``is_active()`` will be ``False``).
    """
    codegraph_dir = os.path.join(os.path.abspath(project_root), ".tws", "codegraph")
    return FederationRegistry(codegraph_dir)
