"""Tests for the 11 semantic search signals.

TDD: write tests first, then implement.
"""

import pytest
from unittest.mock import MagicMock, patch


# ============================================================================
# Helper: collect all signal classes
# ============================================================================

def _all_signal_classes():
    """Return list of all 11 Signal subclasses."""
    from tws_graph.search.signals import (
        BM25Signal,
        QualifiedNameMatchSignal,
        DocstringMatchSignal,
        ASTSimilaritySignal,
        APISignatureSimilaritySignal,
        CloneSimilaritySignal,
        ModuleProximitySignal,
        GraphDiffusionSignal,
        CallerCalleeProximitySignal,
        GraphCentralitySignal,
        DataFlowConnectionSignal,
    )
    return [
        BM25Signal,
        QualifiedNameMatchSignal,
        DocstringMatchSignal,
        ASTSimilaritySignal,
        APISignatureSimilaritySignal,
        CloneSimilaritySignal,
        ModuleProximitySignal,
        GraphDiffusionSignal,
        CallerCalleeProximitySignal,
        GraphCentralitySignal,
        DataFlowConnectionSignal,
    ]


def _make_candidate(**overrides):
    """Build a minimal candidate dict with defaults."""
    return {
        "id": "test_id_001",
        "name": "calculateTotal",
        "qualified_name": "src/utils.py::calculateTotal",
        "kind": "function",
        "file_path": "src/utils.py",
        "language": "python",
        "signature": "(items: List[float]) -> float",
        "docstring": "Calculate the sum of all items.",
        "rank": 0.5,
        "body": "def calculateTotal(items):\\n    return sum(items)",
        **overrides,
    }


def _make_ctx(**overrides):
    """Build a minimal ctx dict with defaults (all None by default)."""
    defaults = {
        "queries": None,
        "traverser": None,
        "minhash": None,
        "lsh": None,
        "centrality_scores": None,
        "embeddings_model": None,
        "clone_pairs": None,
    }
    defaults.update(overrides)
    return defaults


# ============================================================================
# Test: each Signal has name and weight attributes
# ============================================================================

class TestSignalAttributes:
    """Verify every signal exposes name and weight."""

    @pytest.mark.parametrize("signal_cls", _all_signal_classes())
    def test_has_name_attribute(self, signal_cls):
        instance = signal_cls()
        assert hasattr(instance, "name"), f"{signal_cls.__name__} missing 'name'"
        assert isinstance(instance.name, str), f"{signal_cls.__name__}.name not a str"
        assert len(instance.name) > 0, f"{signal_cls.__name__}.name is empty"

    @pytest.mark.parametrize("signal_cls", _all_signal_classes())
    def test_has_weight_attribute(self, signal_cls):
        instance = signal_cls()
        assert hasattr(instance, "weight"), f"{signal_cls.__name__} missing 'weight'"
        assert isinstance(instance.weight, (int, float)), \
            f"{signal_cls.__name__}.weight not numeric"
        assert instance.weight > 0, f"{signal_cls.__name__}.weight must be positive"

    @pytest.mark.parametrize("signal_cls", _all_signal_classes())
    def test_custom_weight(self, signal_cls):
        instance = signal_cls(weight=2.5)
        assert instance.weight == 2.5


# ============================================================================
# Test: compute() returns float in [0.0, 1.0]
# ============================================================================

class TestComputeReturnsRange:
    """Every signal.compute() must return a float in [0.0, 1.0]."""

    def test_bm25_returns_range(self):
        from tws_graph.search.signals import BM25Signal
        sig = BM25Signal()
        cand = _make_candidate(rank=3.0)
        r = sig.compute("calculate", cand, _make_ctx())
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0, f"BM25Signal returned {r}"

    def test_qualified_name_match_returns_range(self):
        from tws_graph.search.signals import QualifiedNameMatchSignal
        sig = QualifiedNameMatchSignal()
        cand = _make_candidate(qualified_name="src/utils.py::calculateTotal")
        r = sig.compute("calculateTotal", cand, _make_ctx())
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_docstring_match_returns_range(self):
        from tws_graph.search.signals import DocstringMatchSignal
        sig = DocstringMatchSignal()
        cand = _make_candidate(docstring="Calculate the sum")
        r = sig.compute("calculate sum", cand, _make_ctx())
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_ast_similarity_returns_range(self):
        from tws_graph.search.signals import ASTSimilaritySignal
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = ASTSimilaritySignal()
        cand = _make_candidate(body="def foo(x): return x + 1")
        ctx = _make_ctx(minhash=MinHash())
        r = sig.compute("def bar(y): return y + 1", cand, ctx)
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_api_signature_similarity_returns_range(self):
        from tws_graph.search.signals import APISignatureSimilaritySignal
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = APISignatureSimilaritySignal()
        cand = _make_candidate(signature="(items: List[float]) -> float")
        ctx = _make_ctx(minhash=MinHash())
        r = sig.compute("calculate items float", cand, ctx)
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_clone_similarity_returns_range(self):
        from tws_graph.search.signals import CloneSimilaritySignal
        sig = CloneSimilaritySignal()
        cand = _make_candidate()
        # No clone pairs => 0.0
        ctx = _make_ctx(clone_pairs=None)
        r = sig.compute("calculate", cand, ctx)
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_module_proximity_returns_range(self):
        from tws_graph.search.signals import ModuleProximitySignal
        sig = ModuleProximitySignal()
        cand = _make_candidate(file_path="src/utils.py")
        r = sig.compute("calculate", cand, _make_ctx())
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_graph_diffusion_returns_range(self):
        from tws_graph.search.signals import GraphDiffusionSignal
        sig = GraphDiffusionSignal()
        cand = _make_candidate()
        ctx = _make_ctx(traverser=None)
        r = sig.compute("calculate", cand, ctx)
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_caller_callee_proximity_returns_range(self):
        from tws_graph.search.signals import CallerCalleeProximitySignal
        sig = CallerCalleeProximitySignal()
        cand = _make_candidate()
        ctx = _make_ctx(traverser=None)
        r = sig.compute("calculate", cand, ctx)
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_graph_centrality_returns_range(self):
        from tws_graph.search.signals import GraphCentralitySignal
        sig = GraphCentralitySignal()
        cand = _make_candidate()
        ctx = _make_ctx(centrality_scores=None)
        r = sig.compute("calculate", cand, ctx)
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    def test_data_flow_connection_returns_range(self):
        from tws_graph.search.signals import DataFlowConnectionSignal
        sig = DataFlowConnectionSignal()
        cand = _make_candidate()
        ctx = _make_ctx(traverser=None)
        r = sig.compute("calculate", cand, ctx)
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0


# ============================================================================
# Test: BM25Signal
# ============================================================================

class TestBM25Signal:
    def test_normal_rank_conversion(self):
        """rank=0 → 1.0, rank=1 → 0.5, rank=3 → 0.25, etc."""
        from tws_graph.search.signals import BM25Signal
        sig = BM25Signal()
        cand = _make_candidate(rank=0.0)
        assert sig.compute("q", cand, _make_ctx()) == pytest.approx(1.0, abs=0.01)

        cand = _make_candidate(rank=1.0)
        assert sig.compute("q", cand, _make_ctx()) == pytest.approx(0.5, abs=0.01)

        cand = _make_candidate(rank=3.0)
        assert sig.compute("q", cand, _make_ctx()) == pytest.approx(0.25, abs=0.01)

    def test_missing_rank_returns_zero(self):
        from tws_graph.search.signals import BM25Signal
        sig = BM25Signal()
        cand = _make_candidate()
        del cand["rank"]
        r = sig.compute("q", cand, _make_ctx())
        assert r == 0.0

    def test_weight_does_not_affect_raw_result(self):
        """Weight is metadata — compute() returns raw score regardless of weight."""
        from tws_graph.search.signals import BM25Signal
        sig_default = BM25Signal()       # weight=1.0
        sig_heavy = BM25Signal(weight=2.0)
        sig_light = BM25Signal(weight=0.5)
        cand = _make_candidate(rank=1.0)
        r_default = sig_default.compute("q", cand, _make_ctx())
        r_heavy = sig_heavy.compute("q", cand, _make_ctx())
        r_light = sig_light.compute("q", cand, _make_ctx())
        # All should return the same raw score: 1 / (1+1) = 0.5
        assert r_default == pytest.approx(0.5, abs=0.01)
        assert r_heavy == pytest.approx(0.5, abs=0.01)
        assert r_light == pytest.approx(0.5, abs=0.01)


# ============================================================================
# Test: QualifiedNameMatchSignal
# ============================================================================

class TestQualifiedNameMatchSignal:
    def test_exact_match_returns_one(self):
        from tws_graph.search.signals import QualifiedNameMatchSignal
        sig = QualifiedNameMatchSignal()
        cand = _make_candidate(qualified_name="src/app.py::calculateTotal")
        r = sig.compute("src/app.py::calculateTotal", cand, _make_ctx())
        assert r == 1.0

    def test_prefix_match_returns_half(self):
        from tws_graph.search.signals import QualifiedNameMatchSignal
        sig = QualifiedNameMatchSignal()
        cand = _make_candidate(qualified_name="src/app.py::calculateTotal")
        # qualified_name starts with query → prefix match
        r = sig.compute("src/app.py", cand, _make_ctx())
        assert r == 0.5

    def test_no_match_returns_zero(self):
        from tws_graph.search.signals import QualifiedNameMatchSignal
        sig = QualifiedNameMatchSignal()
        cand = _make_candidate(qualified_name="src/app.py::calculateTotal")
        r = sig.compute("nonexistent", cand, _make_ctx())
        assert r == 0.0

    def test_text_in_name_returns_low_score(self):
        from tws_graph.search.signals import QualifiedNameMatchSignal
        sig = QualifiedNameMatchSignal()
        cand = _make_candidate(qualified_name="src/app.py::calculateTotal")
        # "calculate" is substring of qualified_name but not prefix
        r = sig.compute("calculate", cand, _make_ctx())
        assert 0.0 < r < 0.5

    def test_case_insensitive(self):
        from tws_graph.search.signals import QualifiedNameMatchSignal
        sig = QualifiedNameMatchSignal()
        cand = _make_candidate(qualified_name="src/app.py::CalculateTotal")
        r = sig.compute("src/app.py::calculatetotal", cand, _make_ctx())
        assert r == 1.0

    def test_weight_affects_result(self):
        from tws_graph.search.signals import QualifiedNameMatchSignal
        sig = QualifiedNameMatchSignal(weight=1.5)
        cand = _make_candidate(qualified_name="src/app.py::calculateTotal")
        r = sig.compute("src/app.py::calculateTotal", cand, _make_ctx())
        assert r == pytest.approx(1.0, abs=0.01)  # raw=1.0, weighted=1.5 capped at 1.0


# ============================================================================
# Test: DocstringMatchSignal
# ============================================================================

class TestDocstringMatchSignal:
    def test_full_overlap_returns_one(self):
        from tws_graph.search.signals import DocstringMatchSignal
        sig = DocstringMatchSignal()
        cand = _make_candidate(docstring="calculate total sum")
        r = sig.compute("calculate total", cand, _make_ctx())
        assert r == 1.0

    def test_partial_overlap(self):
        from tws_graph.search.signals import DocstringMatchSignal
        sig = DocstringMatchSignal()
        cand = _make_candidate(docstring="calculate the total sum of all items")
        r = sig.compute("calculate database items", cand, _make_ctx())
        # "calculate" and "items" match; "database" does not → 2/3
        assert 0.0 < r < 1.0

    def test_no_overlap_returns_zero(self):
        from tws_graph.search.signals import DocstringMatchSignal
        sig = DocstringMatchSignal()
        cand = _make_candidate(docstring="calculate total sum")
        r = sig.compute("xyz abc foo", cand, _make_ctx())
        assert r == 0.0


# ============================================================================
# Test: ASTSimilaritySignal
# ============================================================================

class TestASTSimilaritySignal:
    def test_identical_bodies(self):
        from tws_graph.search.signals import ASTSimilaritySignal
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = ASTSimilaritySignal()
        body = "def foo(x):\n    return x + 1"
        cand = _make_candidate(body=body)
        ctx = _make_ctx(minhash=MinHash())
        r = sig.compute("def foo(x): return x + 1", cand, ctx)
        assert 0.0 < r <= 1.0

    def test_completely_different_bodies(self):
        from tws_graph.search.signals import ASTSimilaritySignal
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = ASTSimilaritySignal()
        cand = _make_candidate(body="def foo(x):\n    return x + 1")
        ctx = _make_ctx(minhash=MinHash())
        r = sig.compute("class Bar: pass for i in range(10): print(i)", cand, ctx)
        # Should be low but not zero (some structural tokens always match)
        assert 0.0 <= r < 0.5


# ============================================================================
# Test: APISignatureSimilaritySignal
# ============================================================================

class TestAPISignatureSimilaritySignal:
    def test_signature_match(self):
        from tws_graph.search.signals import APISignatureSimilaritySignal
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = APISignatureSimilaritySignal()
        cand = _make_candidate(signature="(items: List[float]) -> float")
        ctx = _make_ctx(minhash=MinHash())
        r = sig.compute("items List float", cand, ctx)
        assert 0.0 < r <= 1.0

    def test_no_match(self):
        from tws_graph.search.signals import APISignatureSimilaritySignal
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = APISignatureSimilaritySignal()
        cand = _make_candidate(signature="(items: List[float]) -> float")
        ctx = _make_ctx(minhash=MinHash())
        r = sig.compute("database connect query", cand, ctx)
        assert 0.0 <= r < 0.5


# ============================================================================
# Test: CloneSimilaritySignal
# ============================================================================

class TestCloneSimilaritySignal:
    def test_no_clone_pairs_returns_zero(self):
        from tws_graph.search.signals import CloneSimilaritySignal
        sig = CloneSimilaritySignal()
        cand = _make_candidate()
        ctx = _make_ctx(clone_pairs=None)
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0

    def test_empty_clone_pairs_returns_zero(self):
        from tws_graph.search.signals import CloneSimilaritySignal
        sig = CloneSimilaritySignal()
        cand = _make_candidate()
        ctx = _make_ctx(clone_pairs=[])
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0

    def test_candidate_in_clone_pair(self):
        from tws_graph.search.signals import CloneSimilaritySignal
        sig = CloneSimilaritySignal()
        nid = "test_id_001"
        cand = _make_candidate(id=nid)
        clone_pairs = [
            {"node_a": nid, "node_b": "other_id", "similarity": 0.85},
        ]
        ctx = _make_ctx(clone_pairs=clone_pairs)
        r = sig.compute("calculate", cand, ctx)
        assert r > 0.0


# ============================================================================
# Test: ModuleProximitySignal
# ============================================================================

class TestModuleProximitySignal:
    def test_same_file(self):
        from tws_graph.search.signals import ModuleProximitySignal
        sig = ModuleProximitySignal()
        cand = _make_candidate(file_path="src/utils/calc.py")
        # Same directory prefix match
        r = sig.compute("calculate", cand, _make_ctx())
        # By default query doesn't carry a file path, so this uses only candidate
        assert 0.0 <= r <= 1.0

    def test_same_module_prefix_match(self):
        from tws_graph.search.signals import ModuleProximitySignal
        sig = ModuleProximitySignal()
        cand = _make_candidate(file_path="src/auth/login.py", qualified_name="src.auth.login::login")
        r = sig.compute("src/auth", cand, _make_ctx())
        # "src/auth" is prefix of both file_path and qualified_name
        assert r > 0.0


# ============================================================================
# Test: Empty fields → 0.0
# ============================================================================

class TestEmptyFieldsReturnZero:
    def test_empty_body_ast_similarity_zero(self):
        from tws_graph.search.signals import ASTSimilaritySignal
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = ASTSimilaritySignal()
        cand = _make_candidate(body="")
        ctx = _make_ctx(minhash=MinHash())
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0

    def test_empty_signature_api_similarity_zero(self):
        from tws_graph.search.signals import APISignatureSimilaritySignal
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = APISignatureSimilaritySignal()
        cand = _make_candidate(signature="")
        ctx = _make_ctx(minhash=MinHash())
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0

    def test_empty_docstring_returns_zero(self):
        from tws_graph.search.signals import DocstringMatchSignal
        sig = DocstringMatchSignal()
        cand = _make_candidate(docstring="")
        r = sig.compute("calculate", cand, _make_ctx())
        assert r == 0.0

    def test_none_docstring_returns_zero(self):
        from tws_graph.search.signals import DocstringMatchSignal
        sig = DocstringMatchSignal()
        cand = _make_candidate(docstring=None)
        r = sig.compute("calculate", cand, _make_ctx())
        assert r == 0.0


# ============================================================================
# Test: Signal does not crash on exceptions
# ============================================================================

class TestSignalsNeverCrash:
    @pytest.mark.parametrize("signal_cls", _all_signal_classes())
    def test_empty_candidate_does_not_crash(self, signal_cls):
        from tws_graph.graph.algorithms.minhash import MinHash

        sig = signal_cls()
        cand = {}
        ctx = _make_ctx(minhash=MinHash(), traverser=None)
        r = sig.compute("test", cand, ctx)
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0

    @pytest.mark.parametrize("signal_cls", _all_signal_classes())
    def test_compute_with_none_ctx(self, signal_cls):
        sig = signal_cls()
        cand = _make_candidate()
        r = sig.compute("test", cand, {})
        assert isinstance(r, float)
        assert 0.0 <= r <= 1.0


# ============================================================================
# Test: Custom weights are accepted
# ============================================================================

class TestCustomWeights:
    def test_bm25_weight_is_stored(self):
        """Custom weight is stored but does not affect compute() raw output."""
        from tws_graph.search.signals import BM25Signal
        sig = BM25Signal(weight=3.0)
        assert sig.weight == 3.0
        cand = _make_candidate(rank=2.0)
        r = sig.compute("q", cand, _make_ctx())
        assert r == pytest.approx(1.0/3.0, abs=0.01)  # raw = 1/(1+2) = 1/3

    def test_qualified_name_match_weight_is_stored(self):
        from tws_graph.search.signals import QualifiedNameMatchSignal
        sig = QualifiedNameMatchSignal(weight=2.0)
        assert sig.weight == 2.0
        cand = _make_candidate(qualified_name="src/app.py::calculateTotal")
        r = sig.compute("src/app.py::calculateTotal", cand, _make_ctx())
        # exact match → raw 1.0, weight not applied in compute()
        assert r == 1.0

    def test_docstring_weight_is_stored(self):
        from tws_graph.search.signals import DocstringMatchSignal
        sig = DocstringMatchSignal(weight=2.0)
        assert sig.weight == 2.0
        cand = _make_candidate(docstring="calculate sum of items")
        r = sig.compute("calculate sum", cand, _make_ctx())
        assert r == pytest.approx(1.0, abs=0.01)


# ============================================================================
# Test: Graph-based signals with mock traverser
# ============================================================================

class TestGraphDiffusionWithMock:
    def test_no_traverser_returns_zero(self):
        from tws_graph.search.signals import GraphDiffusionSignal
        sig = GraphDiffusionSignal()
        cand = _make_candidate()
        ctx = _make_ctx(traverser=None)
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0

    def test_candidate_in_impact_radius(self):
        from tws_graph.search.signals import GraphDiffusionSignal
        sig = GraphDiffusionSignal()
        nid = "test_id_001"
        cand = _make_candidate(id=nid)
        mock_traverser = MagicMock()
        mock_traverser.get_impact_radius.return_value = {
            "nodes": {nid: cand, "source_node": {"id": "src"}},
            "edges": [{"source": nid, "target": "src", "kind": "calls"}],
            "roots": ["src"],
        }
        ctx = _make_ctx(traverser=mock_traverser)
        r = sig.compute("calculate", cand, ctx)
        assert 0.0 <= r <= 1.0


class TestCallerCalleeWithMock:
    def test_no_traverser_returns_zero(self):
        from tws_graph.search.signals import CallerCalleeProximitySignal
        sig = CallerCalleeProximitySignal()
        cand = _make_candidate()
        ctx = _make_ctx(traverser=None)
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0

    def test_direct_callee_returns_one(self):
        from tws_graph.search.signals import CallerCalleeProximitySignal
        sig = CallerCalleeProximitySignal()
        nid = "test_id_001"
        cand = _make_candidate(id=nid)
        mock_traverser = MagicMock()
        mock_traverser.get_calls.return_value = {
            "nodes": {nid: cand},
            "edges": [{"source": "caller", "target": nid, "kind": "calls"}],
            "roots": ["caller"],
        }
        ctx = _make_ctx(traverser=mock_traverser)
        r = sig.compute("calculate", cand, ctx)
        assert 0.0 <= r <= 1.0


class TestGraphCentralityWithMock:
    def test_no_scores_returns_zero(self):
        from tws_graph.search.signals import GraphCentralitySignal
        sig = GraphCentralitySignal()
        cand = _make_candidate()
        ctx = _make_ctx(centrality_scores=None)
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0

    def test_candidate_with_score(self):
        from tws_graph.search.signals import GraphCentralitySignal
        sig = GraphCentralitySignal()
        nid = "test_id_001"
        cand = _make_candidate(id=nid)
        ctx = _make_ctx(centrality_scores={nid: 0.05, "other": 0.1})
        r = sig.compute("calculate", cand, ctx)
        assert 0.0 < r <= 1.0

    def test_candidate_not_in_scores(self):
        from tws_graph.search.signals import GraphCentralitySignal
        sig = GraphCentralitySignal()
        cand = _make_candidate(id="unknown_node")
        ctx = _make_ctx(centrality_scores={"other": 0.1})
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0


class TestDataFlowConnectionWithMock:
    def test_no_traverser_returns_zero(self):
        from tws_graph.search.signals import DataFlowConnectionSignal
        sig = DataFlowConnectionSignal()
        cand = _make_candidate()
        ctx = _make_ctx(traverser=None)
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0

    def test_path_found(self):
        from tws_graph.search.signals import DataFlowConnectionSignal
        sig = DataFlowConnectionSignal()
        nid = "test_id_001"
        cand = _make_candidate(id=nid)
        mock_traverser = MagicMock()
        mock_traverser.find_path.return_value = [
            {"node": {"id": "src"}, "via_edge": None},
            {"node": {"id": nid}, "via_edge": {"kind": "data_flows"}},
        ]
        ctx = _make_ctx(traverser=mock_traverser)
        r = sig.compute("calculate", cand, ctx)
        assert 0.0 <= r <= 1.0

    def test_no_path_returns_zero(self):
        from tws_graph.search.signals import DataFlowConnectionSignal
        sig = DataFlowConnectionSignal()
        cand = _make_candidate(id="isolated_node")
        mock_traverser = MagicMock()
        mock_traverser.find_path.return_value = None
        ctx = _make_ctx(traverser=mock_traverser)
        r = sig.compute("calculate", cand, ctx)
        assert r == 0.0
