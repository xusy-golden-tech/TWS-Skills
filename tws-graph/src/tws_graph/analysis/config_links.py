"""Config link analysis — associate code constants with configuration file keys.

Scans a project's configuration files (.env, .yaml/.yml, .json, .toml,
.properties) and links code-level constant variables to their corresponding
config keys via three match strategies: exact_match, prefix_match, contains_match.

Design: P9 Analysis Suite — Config Link Analyzer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from tws_graph.store.interface import Store

# ---------------------------------------------------------------------------
# Config file extensions to scan
# ---------------------------------------------------------------------------

_CONFIG_EXTENSIONS = {".yaml", ".yml", ".json", ".toml", ".properties"}

# Directories excluded from config file scanning
_EXCLUDED_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".eggs",
    ".tws",
    "build",
    "dist",
    ".idea",
    ".vscode",
}


@dataclass
class ConfigLink:
    """A derived association between a code constant and a config file key.

    Attributes:
        node_id: Unique node identifier of the constant/variable.
        node_name: Simple name of the constant (e.g. "DATABASE_URL").
        file_path: Project-relative path of the source file.
        config_file: Configuration file name (e.g. ".env", "config/app.yaml").
        config_key: The matching key in the config file.
        confidence: Probability-like score between 0.0 and 1.0.
        derivation: Strategy that produced this link
            (``exact_match``, ``prefix_match``, or ``contains_match``).
    """

    node_id: str
    node_name: str
    file_path: str
    config_file: str
    config_key: str
    confidence: float = 0.0
    derivation: str = ""


class ConfigLinkAnalyzer:
    """Scan project config files and link constants to config keys.

    Detection logic:
        1. Iterate over variable nodes in *store*; filter to constants
           (``is_const`` property or all-uppercase name).
        2. Scan project root for config files (.env, .yaml, .json, .toml, .properties).
        3. Match each constant name against every config key:
           - **exact_match** (0.9): constant name == config key
           - **prefix_match** (0.7): ``APP_`` prefixed constant maps to
             ``app.xxx`` nested path in structured config files
           - **contains_match** (0.5): constant name is a substring of
             the config key or vice versa

    Usage::

        store = MemoryStore()
        # ... populate store with indexed nodes ...
        analyzer = ConfigLinkAnalyzer()
        links = analyzer.analyze(store, project_root=".")
        for link in links:
            print(f"{link.node_name} → {link.config_file}:{link.config_key} "
                  f"({link.derivation}, {link.confidence})")
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, store: Store, project_root: str = ".") -> list[ConfigLink]:
        """Run config-link analysis over *store* and *project_root*.

        Args:
            store: A graph Store with indexed nodes.
            project_root: Path to the project root directory for config file scanning.

        Returns:
            List of ``ConfigLink`` objects.
        """
        # Step 1: Collect constant variable nodes
        constants: list[dict] = []
        for node in store.iter_nodes_by_kind("variable"):
            if self._is_constant_node(node):
                constants.append({
                    "name": node["name"],
                    "id": node["id"],
                    "file_path": node["file_path"],
                })

        if not constants:
            return []

        # Step 2: Delegate to project-level analysis
        return self.analyze_project_constants(constants, project_root)

    def analyze_project_constants(
        self,
        constants: list[dict],
        project_root: str = ".",
    ) -> list[ConfigLink]:
        """Match a list of constants against scanned config files.

        This method is the core matching engine, separated so that tests
        can inject constant lists without a full Store.

        Args:
            constants: List of dicts with keys ``name``, ``id``, ``file_path``.
            project_root: Path to the project root directory.

        Returns:
            List of ``ConfigLink`` objects.
        """
        # Scan config files → {config_key: config_file}
        key_to_file = self._scan_config_files(project_root)

        if not key_to_file:
            return []

        config_keys = list(key_to_file.keys())
        links: list[ConfigLink] = []

        for const in constants:
            const_name: str = const["name"]

            # Strategy 1: exact_match (0.9)
            if const_name in key_to_file:
                links.append(ConfigLink(
                    node_id=const["id"],
                    node_name=const_name,
                    file_path=const["file_path"],
                    config_file=key_to_file[const_name],
                    config_key=const_name,
                    confidence=0.9,
                    derivation="exact_match",
                ))

            # Strategy 2: prefix_match (0.7)
            prefix_results = self._try_prefix_match(const_name, key_to_file, config_keys)
            for config_key, config_file in prefix_results:
                links.append(ConfigLink(
                    node_id=const["id"],
                    node_name=const_name,
                    file_path=const["file_path"],
                    config_file=config_file,
                    config_key=config_key,
                    confidence=0.7,
                    derivation="prefix_match",
                ))

            # Strategy 3: contains_match (0.5)
            contains_results = self._try_contains_match(const_name, key_to_file, config_keys)
            for config_key, config_file in contains_results:
                links.append(ConfigLink(
                    node_id=const["id"],
                    node_name=const_name,
                    file_path=const["file_path"],
                    config_file=config_file,
                    config_key=config_key,
                    confidence=0.5,
                    derivation="contains_match",
                ))

        return links

    # ------------------------------------------------------------------
    # Constant detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_constant_node(node: dict) -> bool:
        """Return True if *node* represents a constant variable.

        A node is a constant if:
        1. Its ``properties`` JSON contains ``is_const: true``, OR
        2. Its name is all uppercase (Python convention heuristic).
        """
        # Check explicit is_const property
        props_raw = node.get("properties")
        if props_raw:
            try:
                props = json.loads(props_raw) if isinstance(props_raw, str) else props_raw
                if isinstance(props, dict):
                    is_const = props.get("is_const")
                    if is_const is not None:
                        return bool(is_const)
            except (json.JSONDecodeError, TypeError):
                pass

        # Heuristic: all-uppercase name (common for Python/JS constants)
        name: str = node.get("name", "")
        if not name:
            return False
        # Must contain at least one letter (avoid purely numeric/symbol names)
        has_alpha = any(c.isalpha() for c in name)
        return has_alpha and name == name.upper()

    # ------------------------------------------------------------------
    # Config file scanning
    # ------------------------------------------------------------------

    def _scan_config_files(self, project_root: str) -> dict[str, str]:
        """Scan *project_root* for config files and return {key: file_path}.

        Keys from different files are merged; when the same key appears
        in multiple files the first file encountered wins.

        Returns a relative-path to absolute-path mapping for keys.
        """
        key_to_file: dict[str, str] = {}

        for dirpath, dirnames, filenames in os.walk(project_root):
            # Filter out excluded directories
            dirnames[:] = [
                d for d in dirnames
                if d not in _EXCLUDED_DIRS and not d.startswith(".")
            ]

            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(file_path, project_root)

                parsed: Optional[dict[str, str]] = None

                # .env files (including .env.production, .env.local, etc.)
                if filename == ".env" or filename.startswith(".env."):
                    parsed = self._parse_dotenv(file_path)
                else:
                    _, ext = os.path.splitext(filename)
                    ext_lower = ext.lower()
                    if ext_lower == ".yaml" or ext_lower == ".yml":
                        parsed = self._parse_yaml(file_path)
                    elif ext_lower == ".json":
                        parsed = self._parse_json(file_path)
                    elif ext_lower == ".toml":
                        parsed = self._parse_toml(file_path)
                    elif ext_lower == ".properties":
                        parsed = self._parse_properties(file_path)

                if parsed:
                    for key in parsed:
                        if key not in key_to_file:
                            key_to_file[key] = rel_path

        return key_to_file

    # ------------------------------------------------------------------
    # File parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dotenv(file_path: str) -> dict[str, str]:
        """Parse a .env file and return {key: filename} mapping.

        Lines are trimmed; empty lines and comment lines (#) are skipped.
        Supports quoted and unquoted values.
        """
        result: dict[str, str] = {}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, _, _ = line.partition("=")
                    key = key.strip()
                    if key:
                        result[key] = os.path.basename(file_path)
        except (OSError, UnicodeDecodeError):
            pass
        return result

    @staticmethod
    def _parse_yaml(file_path: str) -> dict[str, str]:
        """Parse a YAML file and flatten nested keys with dot notation."""
        try:
            import yaml
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception:
            return {}

        if not isinstance(data, dict):
            return {}

        filename = os.path.basename(file_path)
        return _flatten_dict(data, prefix="", filename=filename)

    @staticmethod
    def _parse_json(file_path: str) -> dict[str, str]:
        """Parse a JSON file and flatten nested keys with dot notation."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return {}

        if not isinstance(data, dict):
            return {}

        filename = os.path.basename(file_path)
        return _flatten_dict(data, prefix="", filename=filename)

    @staticmethod
    def _parse_toml(file_path: str) -> dict[str, str]:
        """Parse a TOML file and flatten nested keys with dot notation."""
        try:
            # Python 3.11+ has tomllib; fall back to tomli
            try:
                import tomllib
            except ImportError:
                import tomli as tomllib  # type: ignore[no-redef]

            with open(file_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            return {}

        if not isinstance(data, dict):
            return {}

        filename = os.path.basename(file_path)
        return _flatten_dict(data, prefix="", filename=filename)

    @staticmethod
    def _parse_properties(file_path: str) -> dict[str, str]:
        """Parse a .properties file and return {key: filename} mapping.

        Supports key=value, key:value, key value formats.
        Ignores empty lines and lines starting with # or !.
        """
        result: dict[str, str] = {}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("!"):
                        continue
                    # Try key=value, key:value, key value
                    for sep in ("=", ":", " "):
                        if sep in line:
                            key, _, _ = line.partition(sep)
                            key = key.strip()
                            if key:
                                result[key] = os.path.basename(file_path)
                            break
        except (OSError, UnicodeDecodeError):
            pass
        return result

    # ------------------------------------------------------------------
    # Match strategies
    # ------------------------------------------------------------------

    @staticmethod
    def _try_prefix_match(
        const_name: str,
        key_to_file: dict[str, str],
        config_keys: list[str],
    ) -> list[tuple[str, str]]:
        """Try prefix_match: ``APP_PORT`` → ``app.port`` in nested config.

        Identifies a leading uppercase prefix (e.g. ``APP_``, ``DB_``)
        in the constant name, strips it, converts the rest to dotted
        lowercase path, and searches config keys for a match.
        """
        results: list[tuple[str, str]] = []

        # Find the first underscore to split prefix from the rest
        underscore_pos = const_name.find("_")
        if underscore_pos <= 0:
            return results

        prefix = const_name[:underscore_pos]
        # Prefix must be all uppercase (at least 2 chars) — e.g. APP, DB, SERVER
        if len(prefix) < 2 or not prefix.isalpha() or prefix != prefix.upper():
            return results

        # Rest after the prefix underscore, e.g. "PORT" from "APP_PORT"
        rest = const_name[underscore_pos + 1:]
        if not rest:
            return results

        # Convert rest from UPPER_SNAKE to lower.dotted path
        # e.g. "DATABASE_URL" → "database.url"
        dotted_rest = rest.lower().replace("_", ".")

        for ck in config_keys:
            ck_lower = ck.lower()
            # Check if dotted_rest appears as a suffix/subpath of config key
            # e.g. "database.url" in "app.database.url"
            if dotted_rest in ck_lower or ck_lower in dotted_rest:
                # But avoid trivial matches: dotted_rest must be a meaningful part
                if len(dotted_rest) >= 2:
                    results.append((ck, key_to_file[ck]))

        return results

    @staticmethod
    def _try_contains_match(
        const_name: str,
        key_to_file: dict[str, str],
        config_keys: list[str],
    ) -> list[tuple[str, str]]:
        """Try contains_match: one string contains the other.

        Returns matches where either:
        - constant name is a substring of the config key, OR
        - config key is a substring of the constant name.
        """
        results: list[tuple[str, str]] = []

        for ck in config_keys:
            # Skip if already would match via exact — handled upstream
            if const_name == ck:
                continue

            if const_name in ck or ck in const_name:
                results.append((ck, key_to_file[ck]))

        return results


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _flatten_dict(
    data: dict,
    prefix: str = "",
    *,
    filename: str = "",
) -> dict[str, str]:
    """Recursively flatten a nested dict into dot-separated keys.

    Example:
        {"app": {"port": 8080, "db": {"url": "..."}}}
        → {"app.port": filename, "app.db.url": filename}
    """
    result: dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            result.update(_flatten_dict(value, full_key, filename=filename))
        else:
            result[full_key] = filename
    return result
