"""Extraction context and base extractor interface.

Defines the contract all language extractors must implement.
ExtractionContext provides shared infrastructure (add_node/add_edge callbacks,
name stack, scope management) that was previously duplicated per extractor.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# ExtractionResult
# ---------------------------------------------------------------------------

@dataclass
class ExtractionResult:
    """Result of extracting symbols and edges from a single source file."""
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def hash_id(qualified_name: str, file_path: str) -> str:
    """Deterministic node ID without line number."""
    raw = f"{file_path}:{qualified_name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def node_text(node, source: bytes) -> str:
    """Decode the source bytes spanned by a tree-sitter node."""
    return source[node.start_byte():node.end_byte()].decode("utf-8")


# ---------------------------------------------------------------------------
# P50: Materialised child accessors.
# These return plain lists instead of generators, avoiding Python generator
# overhead in the hot extraction loop.
# ---------------------------------------------------------------------------


def children(node):
    """Children of *node* (named and unnamed), materialised as a list.

    P50: Materialised instead of generator — avoids per-child ``yield``
    overhead in the hot extraction loop.
    """
    return [node.child(i) for i in range(node.child_count())]


def named_children(node):
    """Named children of *node*, materialised as a list."""
    return [node.named_child(i) for i in range(node.named_child_count())]


def find_child(node, kind: str):
    """Find first child of given kind (named or unnamed)."""
    for child in children(node):
        if child.kind() == kind:
            return child
    return None


def find_named_child(node, kind: str):
    """Find first named child of given kind."""
    for child in named_children(node):
        if child.kind() == kind:
            return child
    return None


def find_all_named_children(node, kind: str) -> list:
    """Find all named children of given kind."""
    return [c for c in named_children(node) if c.kind() == kind]


def resolve_qualified_target(callee_name: str, file_path: str) -> str:
    """Convert dotted import-style names to :: separators with file path."""
    return f"{file_path}::{callee_name.replace('.', '::')}"


def make_structural_node(source_id: str, name: str, kind: str, file_path: str,
                         line: int, language: str, end_line: int | None = None) -> dict:
    """Create a node dict for a structural element (HTML, CSS, YAML, etc.).

    Used by standalone structural extractors that don't have access to
    ExtractionContext.  Returns a dict conforming to the node schema so
    wrapper classes can append it directly to ctx.result.nodes.
    """
    return {
        "id": source_id,
        "kind": kind,
        "name": name,
        "qualified_name": f"{file_path}::{name}",
        "file_path": file_path,
        "language": language,
        "start_line": line,
        "end_line": end_line if end_line is not None else line,
        "visibility": "public",
        "is_abstract": 0,
        "is_exported": 0,
    }


# ---------------------------------------------------------------------------
# ExtractionContext
# ---------------------------------------------------------------------------

class ExtractionContext:
    """Mutable context carried through a single file's extraction.

    Provides add_node / add_edge closures that build qualified names from the
    current scope stack.  Each extractor gets its own context instance and
    mutates ctx.result in place.
    """

    __slots__ = (
        "file_path", "language", "result",
        "name_stack", "node_stack", "scope_stack",
    )

    def __init__(self, file_path: str, language: str):
        self.file_path = file_path
        self.language = language
        self.result = ExtractionResult()
        self.name_stack: list[str] = []
        self.node_stack: list[str] = []
        self.scope_stack: list[str] = []  # for kotlin-type scope tracking

    # -- qualified name ------------------------------------------------

    def make_qualified(self, simple_name: str) -> str:
        parts = [self.file_path] + self.name_stack + [simple_name]
        return "::".join(parts)

    # -- add node ------------------------------------------------------

    def add_node(
        self,
        kind: str,
        simple_name: str,
        node,
        /,
        **extra,
    ) -> str:
        """Create a node dict, append to result.nodes, return its id."""
        qname = self.make_qualified(simple_name)
        nid = hash_id(qname, self.file_path)
        sp = node.start_position()
        ep = node.end_position()
        record = {
            "id": nid,
            "kind": kind,
            "name": simple_name,
            "qualified_name": qname,
            "file_path": self.file_path,
            "language": self.language,
            "start_line": sp.row + 1,
            "end_line": ep.row + 1,
            "visibility": "public",
            "is_abstract": 0,
            "is_exported": 0,
        }
        record.update(extra)
        self.result.nodes.append(record)
        return nid

    # -- add edge ------------------------------------------------------

    def add_edge(
        self,
        source: str,
        target: str,
        kind: str,
        line: int,
        target_text: str | None = None,
    ) -> None:
        """Append an edge dict to result.edges."""
        edge = {
            "source": source,
            "target": target,
            "kind": kind,
            "source_loc": f"{self.file_path}:{line}",
            "provenance": "tree-sitter",
        }
        if target_text:
            edge["target_text"] = target_text
        self.result.edges.append(edge)

    # -- scope management ----------------------------------------------

    def push_scope(self, name: str, node_id: str, kind: str = "") -> None:
        """Enter a named scope (class, function, etc.)."""
        self.name_stack.append(name)
        self.node_stack.append(node_id)
        self.scope_stack.append(kind)

    def pop_scope(self) -> None:
        """Leave the current scope."""
        if self.name_stack:
            self.name_stack.pop()
        if self.node_stack:
            self.node_stack.pop()
        if self.scope_stack:
            self.scope_stack.pop()


# ---------------------------------------------------------------------------
# BaseExtractor
# ---------------------------------------------------------------------------

class BaseExtractor(ABC):
    """Interface all language extractors must implement.

    Subclasses declare their capabilities via class attributes and implement
    ``extract()``.  Registry auto-discovers them — no manual wiring needed.
    """

    # -- declarative class attributes ----------------------------------

    extensions: list[str] = []            # e.g. [".py"]
    tree_sitter_languages: list[str] = []  # e.g. ["python"]
    language_name: str = ""               # e.g. "python"

    # -- core contract -------------------------------------------------

    @abstractmethod
    def extract(self, source: bytes, tree, ctx: ExtractionContext) -> None:
        """Extract nodes and edges from a parsed tree.

        Mutate ``ctx.result`` in place.  The parser handles tree creation,
        language loading, and error capture — extractors focus purely on
        AST → node/edge conversion.
        """

    # -- optional hooks (override for language-specific behaviour) ----

    def visibility_from_node(self, node, name: str, source: bytes) -> str:
        """Derive visibility from a node and its name.

        Default heuristic: double-underscore prefix = private,
        single underscore = protected, everything else = public.
        """
        if name.startswith("__") and not name.endswith("__"):
            return "private"
        if name.startswith("_"):
            return "protected"
        return "public"

    def is_exported(self, node) -> bool:
        """Determine whether a node is exported. Default: False.

        TypeScript / ES modules override this to check for ``export`` keyword.
        """
        return False

    # -- convenience helpers (callable on any extractor instance) ----

    @staticmethod
    def node_text(node, source: bytes) -> str:
        """Decode source bytes spanned by node."""
        return node_text(node, source)

    @staticmethod
    def children(node):
        """All children (P50: materialised list)."""
        return children(node)

    @staticmethod
    def named_children(node):
        """Named children (P50: materialised list)."""
        return named_children(node)

    @staticmethod
    def find_child(node, kind: str):
        return find_child(node, kind)

    @staticmethod
    def find_named_child(node, kind: str):
        return find_named_child(node, kind)

    @staticmethod
    def find_all_named_children(node, kind: str) -> list:
        return find_all_named_children(node, kind)

    @staticmethod
    def hash_id(qualified_name: str, file_path: str) -> str:
        return hash_id(qualified_name, file_path)

    @staticmethod
    def resolve_qualified_target(callee_name: str, file_path: str) -> str:
        return resolve_qualified_target(callee_name, file_path)
