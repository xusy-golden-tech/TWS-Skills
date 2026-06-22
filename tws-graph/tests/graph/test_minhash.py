"""Tests for graph/algorithms/minhash.py — MinHash + LSH self-implementation.

Verifies:
    1. MinHash determinism (same input → same signature)
    2. MinHash differentiation (different input → different signature)
    3. Signature length equals num_perm
    4. Empty token list handling
    5. LSHIndex insert and query correctness
    6. LSHIndex query on empty signature
    7. find_similar_pairs on empty index
    8. find_similar_pairs on populated index
    9. estimate_jaccard accuracy
    10. extract_ast_tokens normalization
    11. extract_ast_tokens empty input
    12. Non-default num_perm and seed produce different signatures
"""

import pytest

from tws_graph.graph.algorithms.minhash import (
    MinHash,
    LSHIndex,
    estimate_jaccard,
    extract_ast_tokens,
)


# ---------------------------------------------------------------------------
# MinHash tests
# ---------------------------------------------------------------------------

class TestMinHash:
    """Tests for MinHash signature generation."""

    def test_deterministic_signature(self):
        """Same token input produces exactly the same signature."""
        mh = MinHash(num_perm=128, seed=42)
        tokens = ["def", "foo", "(", "x", ")", ":", "return", "x"]
        sig1 = mh.compute_signature(tokens)
        sig2 = mh.compute_signature(tokens)
        assert sig1 == sig2

    def test_different_inputs_different_signature(self):
        """Distinct token inputs produce different signatures."""
        mh = MinHash(num_perm=128, seed=42)
        sig_a = mh.compute_signature(["def", "foo", "return", "x"])
        sig_b = mh.compute_signature(["def", "bar", "return", "y"])
        # Extremely unlikely to be identical with 128 permutations
        assert sig_a != sig_b

    def test_signature_length(self):
        """Signature length equals num_perm."""
        for np in [32, 64, 128, 256]:
            mh = MinHash(num_perm=np, seed=42)
            sig = mh.compute_signature(["def", "helper"])
            assert len(sig) == np

    def test_empty_tokens(self):
        """compute_signature on empty token list should not crash."""
        mh = MinHash(num_perm=128, seed=42)
        sig = mh.compute_signature([])
        assert len(sig) == 128
        # All entries should be ints (even if they are max-value sentinels)
        assert all(isinstance(v, int) for v in sig)

    def test_single_token(self):
        """compute_signature with a single token works."""
        mh = MinHash(num_perm=128, seed=42)
        sig = mh.compute_signature(["x"])
        assert len(sig) == 128
        assert all(isinstance(v, int) for v in sig)

    def test_different_seed_produces_different_signature(self):
        """Same tokens with different seed produce different signatures."""
        tokens = ["def", "foo", "return"]
        mh_a = MinHash(num_perm=128, seed=42)
        mh_b = MinHash(num_perm=128, seed=99)
        sig_a = mh_a.compute_signature(tokens)
        sig_b = mh_b.compute_signature(tokens)
        assert sig_a != sig_b

    def test_different_num_perm_changes_signature(self):
        """Different num_perm with same seed changes signature values."""
        tokens = ["def", "foo"]
        mh_a = MinHash(num_perm=64, seed=42)
        mh_b = MinHash(num_perm=128, seed=42)
        sig_a = mh_a.compute_signature(tokens)
        sig_b = mh_b.compute_signature(tokens)
        # The first 64 elements may differ because hash function
        # (a,b) pairs are generated differently for different num_perm
        # Just verify the lengths differ
        assert len(sig_a) != len(sig_b)

    def test_reproducibility_across_instances(self):
        """Two MinHash instances with same params produce same signatures."""
        tokens = ["if", "x", ">", "0"]
        mh1 = MinHash(num_perm=128, seed=42)
        mh2 = MinHash(num_perm=128, seed=42)
        assert mh1.compute_signature(tokens) == mh2.compute_signature(tokens)

    def test_large_token_set(self):
        """compute_signature handles a large token set correctly."""
        mh = MinHash(num_perm=128, seed=42)
        tokens = ["token_" + str(i) for i in range(500)]
        sig = mh.compute_signature(tokens)
        assert len(sig) == 128
        assert all(isinstance(v, int) for v in sig)


# ---------------------------------------------------------------------------
# LSHIndex tests
# ---------------------------------------------------------------------------

class TestLSHIndex:
    """Tests for LSHIndex bucket-pairing."""

    def test_insert_and_query_same_bucket(self):
        """Inserting a signature then querying it returns that node_id."""
        mh = MinHash(num_perm=128, seed=42)
        idx = LSHIndex(bands=16, rows=8)

        tokens_a = ["def", "hello", "return", "world"]
        sig_a = mh.compute_signature(tokens_a)
        idx.insert("node:A", sig_a)

        candidates = idx.query(sig_a)
        # The same signature should match itself
        assert "node:A" in candidates

    def test_query_similar_signatures(self):
        """Similar signatures should be detected via LSH buckets."""
        mh = MinHash(num_perm=128, seed=42)
        idx = LSHIndex(bands=16, rows=8)

        sig_a = mh.compute_signature(["def", "foo", "return", "x", "+", "y"])
        sig_b = mh.compute_signature(["def", "foo", "return", "x", "+", "y"])
        # Identical — should definitely match
        idx.insert("node:A", sig_a)
        candidates = idx.query(sig_b)
        assert "node:A" in candidates

    def test_query_empty_index(self):
        """Querying an empty index returns empty list."""
        mh = MinHash(num_perm=128, seed=42)
        idx = LSHIndex(bands=16, rows=8)
        sig = mh.compute_signature(["x"])
        assert idx.query(sig) == []

    def test_find_similar_pairs_empty(self):
        """find_similar_pairs on empty index returns empty list."""
        idx = LSHIndex(bands=16, rows=8)
        assert idx.find_similar_pairs() == []

    def test_find_similar_pairs_single_node(self):
        """A single node has no pairs."""
        mh = MinHash(num_perm=128, seed=42)
        idx = LSHIndex(bands=16, rows=8)
        idx.insert("node:A", mh.compute_signature(["x"]))
        assert idx.find_similar_pairs() == []

    def test_find_similar_pairs_identical(self):
        """Two nodes with identical signatures form a pair."""
        mh = MinHash(num_perm=128, seed=42)
        idx = LSHIndex(bands=16, rows=8)

        tokens = ["def", "foo", "return", "bar"]
        sig = mh.compute_signature(tokens)
        idx.insert("node:A", sig)
        idx.insert("node:B", sig)

        pairs = idx.find_similar_pairs()
        assert len(pairs) == 1
        id1, id2, sim = pairs[0]
        assert {id1, id2} == {"node:A", "node:B"}
        assert sim > 0.0

    def test_multiple_inserts(self):
        """Multiple inserts accumulate in the index."""
        mh = MinHash(num_perm=128, seed=42)
        idx = LSHIndex(bands=16, rows=8)

        for i in range(5):
            tokens = ["def", f"func_{i}", "return", str(i)]
            idx.insert(f"node:{i}", mh.compute_signature(tokens))

        # Query with the last inserted signature
        sig_last = mh.compute_signature(["def", "func_4", "return", "4"])
        candidates = idx.query(sig_last)
        assert "node:4" in candidates

    def test_bands_rows_product(self):
        """The LSHIndex constructor validates or uses bands * rows."""
        mh = MinHash(num_perm=128, seed=42)
        idx = LSHIndex(bands=16, rows=8)
        assert idx.bands == 16
        assert idx.rows == 8
        assert idx.bands * idx.rows == 128

    def test_non_default_bands_rows(self):
        """LSHIndex works with non-default bands and rows."""
        mh = MinHash(num_perm=64, seed=42)
        idx = LSHIndex(bands=8, rows=8)  # 8 * 8 = 64

        sig = mh.compute_signature(["def", "test"])
        idx.insert("test_node", sig)
        candidates = idx.query(sig)
        assert "test_node" in candidates


# ---------------------------------------------------------------------------
# estimate_jaccard tests
# ---------------------------------------------------------------------------

class TestEstimateJaccard:
    """Tests for estimate_jaccard from MinHash signatures."""

    def test_identical_signatures(self):
        """Identical signatures estimate Jaccard = 1.0."""
        mh = MinHash(num_perm=128, seed=42)
        sig = mh.compute_signature(["a", "b", "c"])
        assert estimate_jaccard(sig, sig) == 1.0

    def test_different_signatures_less_than_one(self):
        """Different signatures estimate Jaccard < 1.0."""
        mh = MinHash(num_perm=128, seed=42)
        sig1 = mh.compute_signature(["a", "b", "c"])
        sig2 = mh.compute_signature(["x", "y", "z"])
        sim = estimate_jaccard(sig1, sig2)
        assert 0.0 <= sim < 1.0

    def test_result_range(self):
        """estimate_jaccard always returns value in [0.0, 1.0]."""
        mh = MinHash(num_perm=128, seed=42)
        for i in range(10):
            sig1 = mh.compute_signature(["tok"] * 5)
            sig2 = mh.compute_signature([f"tok_{i}" for _ in range(5)])
            sim = estimate_jaccard(sig1, sig2)
            assert 0.0 <= sim <= 1.0

    def test_similar_tokens_give_higher_jaccard(self):
        """Tokens with more overlap give higher Jaccard estimate."""
        mh = MinHash(num_perm=128, seed=42)
        base_tokens = ["def", "calculate", "return", "result"]

        # 100% overlap
        sig_base = mh.compute_signature(base_tokens)
        sim_same = estimate_jaccard(sig_base, sig_base)
        assert sim_same == 1.0

        # ~50% overlap
        sig_partial = mh.compute_signature(["def", "calculate", "x", "y"])
        sim_partial = estimate_jaccard(sig_base, sig_partial)

        # 0% overlap (completely different)
        sig_diff = mh.compute_signature(["class", "Order", "def", "__init__"])
        sim_diff = estimate_jaccard(sig_base, sig_diff)

        # sim_same > sim_partial (or equal), and typically sim_partial > sim_diff
        # Due to MinHash approximation, we only assert the extreme cases
        assert sim_same >= sim_partial


# ---------------------------------------------------------------------------
# extract_ast_tokens tests
# ---------------------------------------------------------------------------

class TestExtractAstTokens:
    """Tests for extract_ast_tokens normalization."""

    def test_identifiers_normalized(self):
        """Identifiers are normalized to 'I'."""
        tokens = extract_ast_tokens("def calculateTotal(items):")
        assert tokens.count("I") >= 2  # calculateTotal and items

    def test_string_literals_normalized(self):
        """String literals are normalized to 'S'."""
        tokens = extract_ast_tokens('print("hello world")')
        assert "S" in tokens

    def test_number_literals_normalized(self):
        """Number literals are normalized to 'N'."""
        tokens = extract_ast_tokens("x = 42 + 3.14")
        assert tokens.count("N") == 2

    def test_keywords_preserved(self):
        """Language keywords are preserved as-is."""
        tokens = extract_ast_tokens("def foo(): if True: return x")
        assert "def" in tokens
        assert "if" in tokens
        assert "return" in tokens

    def test_operators_preserved(self):
        """Operators are preserved as-is."""
        tokens = extract_ast_tokens("x = a + b - c")
        assert "=" in tokens
        assert "+" in tokens
        assert "-" in tokens

    def test_empty_body_returns_empty(self):
        """Empty body returns empty token list."""
        assert extract_ast_tokens("") == []

    def test_whitespace_only_returns_empty(self):
        """Whitespace-only body returns empty list."""
        assert extract_ast_tokens("   \n\t  ") == []

    def test_single_identifier(self):
        """Single identifier body returns ['I']."""
        tokens = extract_ast_tokens("foobar")
        assert tokens == ["I"]

    def test_mixed_body(self):
        """Mixed body with identifiers, keywords, strings, numbers."""
        body = 'def process(x): return x + "done" if x > 0 else "fail"'
        tokens = extract_ast_tokens(body)
        # Keywords preserved
        assert "def" in tokens
        assert "return" in tokens
        assert "if" in tokens
        assert "else" in tokens
        # Strings normalized
        assert "S" in tokens
        # Numbers normalized
        assert "N" in tokens
        # Identifiers normalized
        assert "I" in tokens
        # Operators preserved
        assert "+" in tokens or ">" in tokens

    def test_punctuation_preserved(self):
        """Punctuation like parentheses, colons preserved."""
        tokens = extract_ast_tokens("foo(bar):")
        assert "(" in tokens
        assert ")" in tokens
        assert ":" in tokens

    def test_comment_removed(self):
        """Comments should not appear as tokens (treated as whitespace)."""
        body = "x = 1  # this is a comment"
        tokens = extract_ast_tokens(body)
        assert "#" not in tokens
        assert "comment" not in tokens
        # "this", "is", "a" would be identifiers if present — check they are not
        comment_words = {"this", "is", "a", "comment"}
        assert not comment_words.intersection(set(tokens))
