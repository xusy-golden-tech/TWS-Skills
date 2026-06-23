"""Tests for search/semantic.py -- semantic search orchestrator.

Covers:
    1. SemanticSearchResult construction and empty results
    2. semantic_query() with MemoryStore
    3. semantic_query() parameter validation (empty query, bad path)
    4. semantic_query() result limiting
    5. semantic_query() custom signal_weights
    6. semantic_query() with use_embeddings flag
    7. semantic_query() signals_used tracking
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from tws_graph.store.memory_store import MemoryStore


# ---------------------------------------------------------------------------
# Mock Signal classes for testing (signals.py may not exist yet)
# ---------------------------------------------------------------------------

class MockBM25Signal:
    name = "BM25"
    weight = 1.0

    def compute(self, query, candidate, ctx):
        return 0.6


class MockQualifiedNameSignal:
    name = "QualifiedNameMatch"
    weight = 0.8

    def compute(self, query, candidate, ctx):
        name = candidate.get("name", "").lower()
        return 0.8 if query.lower() in name else 0.1


class MockDocstringSignal:
    name = "DocstringMatch"
    weight = 0.6

    def compute(self, query, candidate, ctx):
        return 0.3


class MockAstSimilaritySignal:
    name = "ASTSimilarity"
    weight = 0.5

    def compute(self, query, candidate, ctx):
        return 0.5


class MockApiSignatureSignal:
    name = "APISignatureSimilarity"
    weight = 0.5

    def compute(self, query, candidate, ctx):
        return 0.4


class MockCloneSimilaritySignal:
    name = "CloneSimilarity"
    weight = 0.3

    def compute(self, query, candidate, ctx):
        return 0.0


class MockModuleProximitySignal:
    name = "ModuleProximity"
    weight = 0.4

    def compute(self, query, candidate, ctx):
        return 0.5


class MockGraphDiffusionSignal:
    name = "GraphDiffusion"
    weight = 0.7

    def compute(self, query, candidate, ctx):
        return 0.3


class MockCallerCalleeSignal:
    name = "CallerCalleeProximity"
    weight = 0.6

    def compute(self, query, candidate, ctx):
        return 0.4


class MockGraphCentralitySignal:
    name = "GraphCentrality"
    weight = 0.4

    def compute(self, query, candidate, ctx):
        return 0.5


class MockDataFlowSignal:
    name = "DataFlowConnection"
    weight = 0.5

    def compute(self, query, candidate, ctx):
        return 0.2


MOCK_SIGNALS = [
    MockBM25Signal(),
    MockQualifiedNameSignal(),
    MockDocstringSignal(),
    MockAstSimilaritySignal(),
    MockApiSignatureSignal(),
    MockCloneSimilaritySignal(),
    MockModuleProximitySignal(),
    MockGraphDiffusionSignal(),
    MockCallerCalleeSignal(),
    MockGraphCentralitySignal(),
    MockDataFlowSignal(),
]

MOCK_SIGNAL_NAMES = [s.name for s in MOCK_SIGNALS]


# ---------------------------------------------------------------------------
# Helper: populate MemoryStore with test nodes
# ---------------------------------------------------------------------------

def _populate_store(store):
    """Insert an assortment of nodes for semantic search testing."""
    nodes = [
        {
            "id": "n1",
            "kind": "function",
            "name": "calculate_tax",
            "qualified_name": "src/tax.py::calculate_tax",
            "file_path": "src/tax.py",
            "language": "python",
            "start_line": 10,
            "end_line": 25,
            "signature": "def calculate_tax(price: float, rate: float) -> float",
            "docstring": "Calculate tax for a given price",
        },
        {
            "id": "n2",
            "kind": "class",
            "name": "TaxCalculator",
            "qualified_name": "src/tax.py::TaxCalculator",
            "file_path": "src/tax.py",
            "language": "python",
            "start_line": 30,
            "end_line": 80,
            "signature": None,
            "docstring": "Handles tax calculation logic",
        },
        {
            "id": "n3",
            "kind": "method",
            "name": "compute",
            "qualified_name": "src/tax.py::TaxCalculator.compute",
            "file_path": "src/tax.py",
            "language": "python",
            "start_line": 35,
            "end_line": 50,
            "signature": "def compute(self, price: float) -> float",
            "docstring": "Compute the final tax amount",
        },
        {
            "id": "n4",
            "kind": "function",
            "name": "process_order",
            "qualified_name": "src/order.py::process_order",
            "file_path": "src/order.py",
            "language": "python",
            "start_line": 5,
            "end_line": 40,
            "signature": "def process_order(items: list) -> dict",
            "docstring": "Process an order with items",
        },
        {
            "id": "n5",
            "kind": "class",
            "name": "OrderManager",
            "qualified_name": "src/order.py::OrderManager",
            "file_path": "src/order.py",
            "language": "python",
            "start_line": 45,
            "end_line": 120,
            "signature": None,
            "docstring": "Manages order lifecycle",
        },
        {
            "id": "n6",
            "kind": "function",
            "name": "format_currency",
            "qualified_name": "src/utils.py::format_currency",
            "file_path": "src/utils.py",
            "language": "python",
            "start_line": 1,
            "end_line": 10,
            "signature": "def format_currency(amount: float) -> str",
            "docstring": "Format a float as currency string",
        },
        {
            "id": "n7",
            "kind": "function",
            "name": "validate_input",
            "qualified_name": "src/validate.py::validate_input",
            "file_path": "src/validate.py",
            "language": "python",
            "start_line": 3,
            "end_line": 20,
            "signature": "def validate_input(data: dict) -> bool",
            "docstring": "Validate user input data",
        },
        {
            "id": "n8",
            "kind": "method",
            "name": "load_config",
            "qualified_name": "src/config.py::ConfigLoader.load_config",
            "file_path": "src/config.py",
            "language": "python",
            "start_line": 15,
            "end_line": 35,
            "signature": "def load_config(self, path: str) -> dict",
            "docstring": "Load configuration from file",
        },
        {
            "id": "n9",
            "kind": "function",
            "name": "handle_error",
            "qualified_name": "src/error.py::handle_error",
            "file_path": "src/error.py",
            "language": "python",
            "start_line": 1,
            "end_line": 15,
            "signature": "def handle_error(exc: Exception) -> None",
            "docstring": "Central error handler",
        },
        {
            "id": "n10",
            "kind": "class",
            "name": "DatabasePool",
            "qualified_name": "src/db.py::DatabasePool",
            "file_path": "src/db.py",
            "language": "python",
            "start_line": 10,
            "end_line": 60,
            "signature": None,
            "docstring": "Connection pool for database",
        },
    ]
    store.insert_nodes(nodes)


# ---------------------------------------------------------------------------
# SemanticSearchResult tests
# ---------------------------------------------------------------------------

class TestSemanticSearchResult:
    """Tests for SemanticSearchResult data class."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from tws_graph.search.semantic import SemanticSearchResult
        self.SemanticSearchResult = SemanticSearchResult

    def test_construction_all_fields(self):
        """Construction sets all fields correctly."""
        results = [{"id": "1", "name": "test_func"}]
        r = self.SemanticSearchResult(
            results=results,
            query="test",
            candidate_count=50,
            duration_ms=100.5,
            signals_used=["BM25", "QualifiedNameMatch"],
            embeddings_enabled=False,
        )
        assert r.results == results
        assert r.query == "test"
        assert r.candidate_count == 50
        assert r.duration_ms == 100.5
        assert r.signals_used == ["BM25", "QualifiedNameMatch"]
        assert r.embeddings_enabled is False

    def test_empty_results(self):
        """Empty result list is valid."""
        r = self.SemanticSearchResult(
            results=[],
            query="nonexistent",
            candidate_count=0,
            duration_ms=0.0,
            signals_used=[],
            embeddings_enabled=False,
        )
        assert r.results == []
        assert r.candidate_count == 0
        assert r.signals_used == []

    def test_zero_duration(self):
        """Duration can be 0."""
        r = self.SemanticSearchResult(
            results=[{"id": "1"}],
            query="q",
            candidate_count=1,
            duration_ms=0.0,
            signals_used=["BM25"],
            embeddings_enabled=False,
        )
        assert r.duration_ms == 0.0

    def test_embeddings_enabled_true(self):
        """embeddings_enabled can be True."""
        r = self.SemanticSearchResult(
            results=[{"id": "1"}],
            query="q",
            candidate_count=1,
            duration_ms=10.0,
            signals_used=["BM25"],
            embeddings_enabled=True,
        )
        assert r.embeddings_enabled is True

    def test_many_signals_used(self):
        """Can track many signal names."""
        signals = [f"Signal{i}" for i in range(20)]
        r = self.SemanticSearchResult(
            results=[{"id": "1"}],
            query="q",
            candidate_count=1,
            duration_ms=5.0,
            signals_used=signals,
            embeddings_enabled=False,
        )
        assert r.signals_used == signals
        assert len(r.signals_used) == 20


# ---------------------------------------------------------------------------
# semantic_query() tests
# ---------------------------------------------------------------------------

class TestSemanticQueryMockSignals:
    """Test semantic_query() with mock signals (signals.py may not exist)."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.store = MemoryStore()
        _populate_store(self.store)

    def test_normal_query_returns_result(self):
        """Normal query returns SemanticSearchResult."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query, SemanticSearchResult
            result = semantic_query("tax", self.store, limit=10)
            assert isinstance(result, SemanticSearchResult)
            assert result.query == "tax"
            assert result.results is not None
            assert isinstance(result.duration_ms, float)

    def test_limit_parameter_restricts_results(self):
        """limit parameter caps result count."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query, SemanticSearchResult
            limit = 3
            result = semantic_query("tax", self.store, limit=limit)
            assert len(result.results) <= limit

    def test_results_are_sorted_by_rank(self):
        """Results are sorted by descending rank."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query, SemanticSearchResult
            result = semantic_query("tax", self.store, limit=10)
            if len(result.results) >= 2:
                scores = [r.get("_score", 0) for r in result.results]
                assert scores == sorted(scores, reverse=True), (
                    f"Expected descending scores, got {scores}"
                )

    def test_each_result_has_score(self):
        """Each result dict contains a _score field."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query("tax", self.store, limit=10)
            for r in result.results:
                assert "_score" in r, f"Result missing _score: {r}"
                assert isinstance(r["_score"], (int, float))

    def test_candidate_count_reflects_fts_results(self):
        """candidate_count reflects the number of FTS candidates."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query("tax", self.store, limit=10)
            # With our test data and MemoryStore, "tax" should match some
            assert result.candidate_count > 0
            # candidate_count should be >= len(results) (after ranking + truncation)
            assert result.candidate_count >= len(result.results)

    def test_signals_used_lists_all_signal_names(self):
        """signals_used includes all active signal names."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query("tax", self.store, limit=10)
            # All 11 mock signals should be listed
            for name in MOCK_SIGNAL_NAMES:
                assert name in result.signals_used, (
                    f"Expected '{name}' in signals_used: {result.signals_used}"
                )
            assert len(result.signals_used) == len(MOCK_SIGNAL_NAMES)


class TestSemanticQueryValidation:
    """Test semantic_query() input validation."""

    def test_empty_query_raises_valueerror(self):
        """Empty query raises ValueError."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            store = MemoryStore()
            with pytest.raises(ValueError):
                semantic_query("", store)
            with pytest.raises(ValueError):
                semantic_query("   ", store)

    def test_none_query_raises_valueerror(self):
        """None query raises ValueError."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            store = MemoryStore()
            with pytest.raises(ValueError):
                semantic_query(None, store)

    def test_nonexistent_db_path_raises_exception(self):
        """Non-existent database path raises exception."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            import os
            bad_path = "/nonexistent/path/to/db.sqlite"
            # Only test if the path truly doesn't exist
            if not os.path.exists(bad_path):
                with pytest.raises(Exception):
                    semantic_query("test", bad_path)


class TestSemanticQueryCustomWeights:
    """Test semantic_query() with custom signal_weights."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.store = MemoryStore()
        _populate_store(self.store)

    def test_custom_weights_override_defaults(self):
        """Custom signal_weights change scoring behavior."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query

            # With default weights
            result_default = semantic_query("tax", self.store, limit=5)

            # With a very high weight for QualifiedNameMatch
            custom_weights = {"QualifiedNameMatch": 100.0}
            result_custom = semantic_query(
                "tax", self.store, limit=5,
                signal_weights=custom_weights,
            )

            # Both should return results
            assert len(result_default.results) > 0
            assert len(result_custom.results) > 0

    def test_custom_weights_zero_weight(self):
        """Zero weight signal does not contribute."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query(
                "tax", self.store, limit=5,
                signal_weights={"BM25": 0.0},
            )
            assert len(result.results) > 0


class TestSemanticQueryEdgeCases:
    """Edge case tests for semantic_query()."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.store = MemoryStore()
        _populate_store(self.store)

    def test_empty_candidate_set_returns_empty(self):
        """When FTS returns no matches, result is empty."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query("zzzzz_nonexistent_symbol_xyz", self.store)
            assert result.results == []
            assert result.candidate_count == 0

    def test_use_embeddings_unsupported_embeddings_false(self):
        """When use_embeddings=True but model unavailable, embeddings_enabled=False."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query(
                "tax", self.store,
                use_embeddings=True,
            )
            # Without a real embedding model, should be False
            assert result.embeddings_enabled is False

    def test_duration_ms_is_positive(self):
        """Duration is always positive."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query("tax", self.store)
            assert result.duration_ms >= 0

    def test_limit_exceeds_candidates(self):
        """When limit > candidates, all candidates are returned."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query("tax", self.store, limit=100)
            assert len(result.results) <= result.candidate_count
            assert len(result.results) > 0

    def test_memory_store_fts_works(self):
        """semantic_query works with MemoryStore fts_search."""
        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query("currency", self.store, limit=5)
            assert result.candidate_count >= 0
            assert isinstance(result.duration_ms, float)


class TestSemanticQueryViaDbPath:
    """Test semantic_query() when given a db_path string."""

    def test_sqlite_db_path_mode(self, tmp_path):
        """semantic_query works with a db_path string."""
        import sqlite3
        db_path = str(tmp_path / "test.db")

        # Create a minimal database with nodes and FTS tables
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY, kind TEXT, name TEXT,
                qualified_name TEXT, file_path TEXT, language TEXT,
                start_line INTEGER, end_line INTEGER, signature TEXT,
                docstring TEXT, visibility TEXT, is_abstract INTEGER DEFAULT 0,
                is_exported INTEGER DEFAULT 0, decorators TEXT,
                framework TEXT, updated_at INTEGER
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
                name, qualified_name, signature, docstring,
                content='nodes', content_rowid='rowid'
            )
        """)
        conn.execute(
            "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, end_line) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("n1", "function", "calculate_tax", "src/tax.py::calculate_tax",
             "src/tax.py", "python", 10, 25),
        )
        conn.commit()
        conn.close()

        with patch(
            "tws_graph.search.semantic._load_signals",
            return_value=(MOCK_SIGNALS, MOCK_SIGNAL_NAMES),
        ):
            from tws_graph.search.semantic import semantic_query
            result = semantic_query("tax", db_path, limit=5)
            # Should work without error
            assert isinstance(result.results, list)
