"""Entry point detector — identifies project entry points in the code graph.

Detects entry points across 6 languages (Python, TypeScript, Java, Go, Kotlin,
Rust) using 6 heuristic rules:
    1. main function: qualified_name contains __main__ or name == "main"
    2. test function/method: file_path contains "test" or name starts with "test_"
    3. CLI entry: decorators referencing typer/click/argparse/commander
    4. route handler: decorators containing HTTP method names
    5. init/constructor: __init__ method or constructor
    6. lifecycle hooks: React/Angular/Vue lifecycle method names

Design: P9 Analysis Suite — entry point detection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from tws_graph.store.interface import Store


# =============================================================================
# Data classes
# =============================================================================


@dataclass
class EntryPointResult:
    """Entry point detection result.

    Attributes:
        node_id: Graph node identifier.
        qualified_name: Fully qualified symbol name (file_path::kind.name).
        kind: Symbol kind (function/method/class).
        file_path: Relative path to the source file.
        entry_type: Classification (main/test/cli/route_handler/init/hook).
        confidence: Detection confidence 0.0~1.0.
        evidence: Human-readable description of which rule was matched.
    """

    node_id: str
    qualified_name: str
    kind: str
    file_path: str
    entry_type: str
    confidence: float
    evidence: str


# =============================================================================
# Heuristic rule sets (language-agnostic name patterns)
# =============================================================================

# Rule 1: main function detection
_MAIN_QUALIFIED_NAME_INDICATORS = ["__main__"]

# Rule 2: test patterns
_TEST_NAME_PREFIX = "test_"
_TEST_PATH_INDICATORS = ["test", "__tests__", "spec"]

# Rule 3: CLI framework decorators
_CLI_DECORATOR_KEYWORDS = ["typer", "click", "argparse", "commander"]

# Rule 4: HTTP route decorator patterns
_ROUTE_DECORATOR_PATTERNS = [
    "route(", ".get(", ".post(", ".put(", ".delete(",
    ".patch(", ".head(", ".options(",
]

# Rule 5: init / constructor names
_INIT_NAMES = {"__init__", "constructor", "init"}

# Rule 6: lifecycle hook names (React, Angular, Vue)
_LIFECYCLE_HOOK_NAMES = {
    # React
    "useEffect", "useLayoutEffect", "useState", "useReducer",
    "useContext", "useRef", "useMemo", "useCallback",
    "componentDidMount", "componentDidUpdate", "componentWillUnmount",
    "shouldComponentUpdate", "getDerivedStateFromProps", "getSnapshotBeforeUpdate",
    # Angular
    "ngOnInit", "ngOnDestroy", "ngOnChanges", "ngDoCheck",
    "ngAfterContentInit", "ngAfterContentChecked",
    "ngAfterViewInit", "ngAfterViewChecked",
    # Vue
    "mounted", "created", "beforeMount", "beforeCreate",
    "beforeDestroy", "destroyed", "updated", "beforeUpdate",
    "activated", "deactivated",
}


# =============================================================================
# EntryPointDetector
# =============================================================================


class EntryPointDetector:
    """Detect project entry points: main functions, tests, CLI commands,
    route handlers, init/constructors, and lifecycle hooks.

    Traverses the Store's function and method nodes, applying 6 heuristic
    rules. Results are deduplicated by (file_path, entry_type).
    """

    def detect(self, store: Store) -> list[EntryPointResult]:
        """Detect all entry points from the Store.

        Iterates over function and method nodes and applies all 6 heuristic
        detection rules. Results are deduplicated by (file_path, entry_type)
        to avoid redundant entries from the same file.

        Args:
            store: A graph Store containing indexed nodes.

        Returns:
            List of detected EntryPointResult objects, sorted by entry_type
            then qualified_name.
        """
        seen: set[tuple[str, str]] = set()
        results: list[EntryPointResult] = []

        for kind in ("function", "method"):
            for node in store.iter_nodes_by_kind(kind):
                r = self.is_entry_point(node)
                if r is None:
                    continue
                dedup_key = (r.file_path, r.entry_type)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                results.append(r)

        results.sort(key=lambda r: (r.entry_type, r.qualified_name))
        return results

    def is_entry_point(self, node: dict) -> Optional[EntryPointResult]:
        """Check whether a single node is an entry point.

        Applies detection rules in priority order; returns the first match.

        Args:
            node: A NodeRecord-like dict with at minimum: id, kind, name,
                  qualified_name, file_path, and optionally decorators.

        Returns:
            EntryPointResult if a rule matches, None otherwise.
        """
        # Extraction helpers (defensive — nodes may lack optional fields)
        name: str = node.get("name", "")
        qname: str = node.get("qualified_name", "")
        file_path: str = node.get("file_path", "")
        node_kind: str = node.get("kind", "")
        decorators_raw: str = node.get("decorators") or ""

        # Rule 1: main function
        result = self._check_main(name, qname, node)
        if result:
            return result

        # Rule 2: test function/method
        result = self._check_test(name, file_path, decorators_raw, node)
        if result:
            return result

        # Rule 3: CLI entry
        result = self._check_cli(name, decorators_raw, node)
        if result:
            return result

        # Rule 4: route handler
        result = self._check_route_handler(decorators_raw, node)
        if result:
            return result

        # Rule 5: init / constructor
        result = self._check_init(name, node)
        if result:
            return result

        # Rule 6: lifecycle hook
        result = self._check_lifecycle_hook(name, node)
        if result:
            return result

        return None

    # ---------------------------------------------------------------------
    # Rule 1: main function
    # ---------------------------------------------------------------------

    def _check_main(
        self, name: str, qname: str, node: dict
    ) -> Optional[EntryPointResult]:
        """Check if node is a main entry point."""
        # qualified_name contains __main__ (Python's if __name__ == "__main__")
        if "__main__" in qname:
            return self._make_result(node, "main", 0.9, "qualified_name contains '__main__'")
        # name == "main" (C, Go, Rust, Java, etc.)
        if name == "main":
            return self._make_result(node, "main", 0.9, "name matches 'main'")
        return None

    # ---------------------------------------------------------------------
    # Rule 2: test function/method
    # ---------------------------------------------------------------------

    def _check_test(
        self, name: str, file_path: str, decorators_raw: str, node: dict
    ) -> Optional[EntryPointResult]:
        """Check if node is a test entry point."""
        # Highest confidence: name starts with test_
        if name.startswith("test_"):
            return self._make_result(node, "test", 0.9, "test_ prefix in function name")

        # Medium confidence: file_path contains test-related indicators
        fp_lower = file_path.lower()
        for indicator in _TEST_PATH_INDICATORS:
            if indicator in fp_lower:
                return self._make_result(
                    node, "test", 0.7,
                    f"'test' found in file path",
                )

        # Java/JUnit-style: @Test decorator
        if decorators_raw and self._decorator_contains(decorators_raw, "Test"):
            return self._make_result(node, "test", 0.8, "@Test decorator found")

        return None

    # ---------------------------------------------------------------------
    # Rule 3: CLI entry
    # ---------------------------------------------------------------------

    def _check_cli(
        self, name: str, decorators_raw: str, node: dict
    ) -> Optional[EntryPointResult]:
        """Check if node is a CLI entry point."""
        if not decorators_raw:
            return None

        decorators_lower = decorators_raw.lower()
        for kw in _CLI_DECORATOR_KEYWORDS:
            if kw.lower() in decorators_lower:
                return self._make_result(
                    node, "cli", 0.8,
                    f"decorator references CLI framework: {kw}",
                )
        return None

    # ---------------------------------------------------------------------
    # Rule 4: route handler
    # ---------------------------------------------------------------------

    def _check_route_handler(
        self, decorators_raw: str, node: dict
    ) -> Optional[EntryPointResult]:
        """Check if node is a route handler."""
        if not decorators_raw:
            return None

        decorators_lower = decorators_raw.lower()
        for pattern in _ROUTE_DECORATOR_PATTERNS:
            if pattern in decorators_lower:
                return self._make_result(
                    node, "route_handler", 0.8,
                    f"decorator matches HTTP route pattern: {pattern}",
                )
        return None

    # ---------------------------------------------------------------------
    # Rule 5: init / constructor
    # ---------------------------------------------------------------------

    def _check_init(
        self, name: str, node: dict
    ) -> Optional[EntryPointResult]:
        """Check if node is an init method or constructor."""
        if name in _INIT_NAMES:
            return self._make_result(node, "init", 0.9, f"name is '{name}'")
        return None

    # ---------------------------------------------------------------------
    # Rule 6: lifecycle hook
    # ---------------------------------------------------------------------

    def _check_lifecycle_hook(
        self, name: str, node: dict
    ) -> Optional[EntryPointResult]:
        """Check if node is a lifecycle hook."""
        if name in _LIFECYCLE_HOOK_NAMES:
            return self._make_result(node, "hook", 0.9, f"'{name}' is a lifecycle hook")
        return None

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    def _make_result(
        self, node: dict, entry_type: str, confidence: float, evidence: str
    ) -> EntryPointResult:
        """Construct an EntryPointResult from a node dict and metadata."""
        return EntryPointResult(
            node_id=node.get("id", ""),
            qualified_name=node.get("qualified_name", ""),
            kind=node.get("kind", ""),
            file_path=node.get("file_path", ""),
            entry_type=entry_type,
            confidence=confidence,
            evidence=evidence,
        )

    @staticmethod
    def _decorator_contains(decorators_raw: str, keyword: str) -> bool:
        """Check if the raw decorators JSON string contains a keyword.

        Handles both well-formed JSON arrays and plain strings.
        """
        if not decorators_raw:
            return False
        try:
            decorators = json.loads(decorators_raw)
            if isinstance(decorators, list):
                return any(keyword in d for d in decorators)
            return keyword in str(decorators)
        except (json.JSONDecodeError, TypeError):
            return keyword in decorators_raw
