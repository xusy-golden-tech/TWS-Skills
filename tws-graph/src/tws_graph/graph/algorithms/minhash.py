"""MinHash + LSH self-implementation — zero external dependencies.

MinHash computes compact signatures for token sequences, enabling
efficient Jaccard-similarity estimation. LSHIndex buckets signatures
so that similar items collide into the same bucket with high probability.

References:
- Broder, A. "On the resemblance and containment of documents" (1997)
- Indyk, P. & Motwani, R. "Approximate nearest neighbors" (1998)
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

# ---------------------------------------------------------------------------
# MinHash constants
# ---------------------------------------------------------------------------

# Mersenne prime 2^61 - 1 used as modulus for the hash family
_MERSENNE_PRIME: int = (1 << 61) - 1

# Sentinel returned when the token set is empty
_MAX_HASH: int = _MERSENNE_PRIME

# ---------------------------------------------------------------------------
# Python keywords (frozenset for O(1) membership test)
# ---------------------------------------------------------------------------

_PYTHON_KEYWORDS: frozenset[str] = frozenset({
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
    "try", "while", "with", "yield",
})

# ---------------------------------------------------------------------------
# Token pattern for extract_ast_tokens
# ---------------------------------------------------------------------------

_TOKEN_RE: re.Pattern = re.compile(
    r'"[^"]*"'             # double-quoted string literal
    r"|'[^']*'"             # single-quoted string literal
    r"|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?"  # number (int / float / scientific)
    r"|[a-zA-Z_]\w*"        # identifier or keyword
    r"|[^\s\w]"             # single punctuation / operator character
)


# ===================================================================
# MinHash
# ===================================================================


class MinHash:
    """MinHash signature generator.

    Computes a compact, fixed-size signature for a token sequence using
    *num_perm* random hash functions. Signatures are deterministic given
    the same *seed* and *num_perm*.

    Parameters
    ----------
    num_perm : int
        Number of hash permutations (signature length). Default 128.
    seed : int
        Seed for generating the (a, b) hash-coefficient pairs. Default 42.
    """

    def __init__(self, num_perm: int = 128, seed: int = 42) -> None:
        self.num_perm = num_perm
        self.seed = seed

        # Pre-compute (a, b) pairs using hashlib for deterministic output
        self._hash_params: list[tuple[int, int]] = []
        for i in range(num_perm):
            a = self._derive_uint64(f"{seed}:{i}:a")
            b = self._derive_uint64(f"{seed}:{i}:b")
            # Ensure a is not 0 (degenerate hash function)
            if a == 0:
                a = 1
            self._hash_params.append((a, b))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_signature(self, tokens: list[str]) -> list[int]:
        """Compute MinHash signature for *tokens*.

        Returns a list of *num_perm* integers. When *tokens* is empty,
        every entry is ``_MAX_HASH`` (a sentinel distinguishable from
        any real hash value).
        """
        if not tokens:
            return [_MAX_HASH] * self.num_perm

        # Pre-hash every token once — O(T) instead of O(P * T)
        token_hashes: list[int] = []
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            token_hashes.append(int.from_bytes(digest[:8], "big"))

        sig: list[int] = []
        for a, b in self._hash_params:
            min_val = _MAX_HASH
            for th in token_hashes:
                # h(x) = (a * x + b) mod M
                h_val = (a * th + b) % _MERSENNE_PRIME
                if h_val < min_val:
                    min_val = h_val
            sig.append(min_val)

        return sig

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_uint64(seed_str: str) -> int:
        """Deterministically derive a 64-bit unsigned integer from a string."""
        digest = hashlib.sha256(seed_str.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big")


# ===================================================================
# LSH Index
# ===================================================================


class LSHIndex:
    """Locality-Sensitive Hashing index for MinHash signatures.

    Splits each 128-element signature into *bands* bands of *rows* rows.
    Two signatures that share at least one identical band are treated as
    candidate pairs.

    Default configuration: ``bands=16, rows=8`` (for 128-perm MinHash).

    Parameters
    ----------
    bands : int
        Number of bands. Default 16.
    rows : int
        Rows per band. Default 8. ``bands * rows`` should match the
        signature length.
    """

    def __init__(self, bands: int = 16, rows: int = 8) -> None:
        self.bands = bands
        self.rows = rows

        # Per-band bucket store: _buckets[b] is dict[bucket_key -> set[node_id]]
        self._buckets: list[dict[int, set[str]]] = [
            {} for _ in range(bands)
        ]

        # node_id -> signature for similarity computation
        self._signatures: dict[str, list[int]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert(self, node_id: str, signature: list[int]) -> None:
        """Insert a node and its signature into the index."""
        self._signatures[node_id] = signature
        for b in range(self.bands):
            key = self._band_key(b, signature)
            self._buckets[b].setdefault(key, set()).add(node_id)

    def query(self, signature: list[int]) -> list[str]:
        """Return node_ids whose signatures collide with *signature*
        in at least one band.

        Returns an empty list when the index is empty or no collision found.
        """
        candidates: set[str] = set()
        for b in range(self.bands):
            key = self._band_key(b, signature)
            bucket = self._buckets[b].get(key)
            if bucket:
                candidates.update(bucket)
        return list(candidates)

    def find_similar_pairs(self) -> list[tuple[str, str, float]]:
        """Find all candidate similar pairs across all LSH buckets.

        Returns a list of ``(node_id_a, node_id_b, estimated_jaccard)``
        triples. Pairs are deduplicated — each unordered pair appears
        at most once.

        Returns an empty list when fewer than 2 nodes are indexed.
        """
        # Collect unordered pairs from all buckets (dedup with set)
        pairs: set[tuple[str, str]] = set()
        for b in range(self.bands):
            for bucket in self._buckets[b].values():
                if len(bucket) < 2:
                    continue
                ids = sorted(bucket)
                for i in range(len(ids)):
                    for j in range(i + 1, len(ids)):
                        pairs.add((ids[i], ids[j]))

        # Compute similarity for each candidate pair
        result: list[tuple[str, str, float]] = []
        for id1, id2 in pairs:
            sig1 = self._signatures.get(id1)
            sig2 = self._signatures.get(id2)
            if sig1 is not None and sig2 is not None:
                sim = estimate_jaccard(sig1, sig2)
                result.append((id1, id2, sim))

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _band_key(self, band_idx: int, signature: list[int]) -> int:
        """Compute a deterministic bucket key for a band slice."""
        start = band_idx * self.rows
        band_slice = tuple(signature[start:start + self.rows])
        return hash((band_idx, band_slice))


# ===================================================================
# estimate_jaccard
# ===================================================================


def estimate_jaccard(sig1: list[int], sig2: list[int]) -> float:
    """Estimate Jaccard similarity from two MinHash signatures.

    Returns a float in [0.0, 1.0]. Equal signatures return 1.0.

    Raises
    ------
    ValueError
        If the two signatures have different lengths.
    """
    if len(sig1) != len(sig2):
        raise ValueError(
            f"Signature length mismatch: {len(sig1)} != {len(sig2)}"
        )
    if len(sig1) == 0:
        return 0.0

    matches = sum(1 for a, b in zip(sig1, sig2) if a == b)
    return matches / len(sig1)


# ===================================================================
# extract_ast_tokens
# ===================================================================


def extract_ast_tokens(body: str) -> list[str]:
    """Extract and normalise tokens from function-body source code.

    Normalisation rules (simplified — no tree-sitter dependency):
        - Identifier  → ``"I"``
        - String      → ``"S"``
        - Number      → ``"N"``
        - Keyword     → kept as-is (e.g. ``"def"``, ``"return"``)
        - Operator / punctuation → kept as-is (e.g. ``"+"``, ``"("``)

    Returns an empty list when *body* is empty or only whitespace.
    """
    if not body or not body.strip():
        return []

    # Strip line comments (Python-style ``# ...``)
    body = re.sub(r"#.*$", "", body, flags=re.MULTILINE)

    # Tokenise
    raw_tokens: list[str] = _TOKEN_RE.findall(body)

    # Classify
    result: list[str] = []
    for tok in raw_tokens:
        if tok.startswith(('"', "'")):
            result.append("S")
        elif tok[0].isdigit():
            result.append("N")
        elif tok[0].isalpha() or tok[0] == "_":
            if tok in _PYTHON_KEYWORDS:
                result.append(tok)
            else:
                result.append("I")
        else:
            # Punctuation / operator — preserve
            result.append(tok)

    return result
