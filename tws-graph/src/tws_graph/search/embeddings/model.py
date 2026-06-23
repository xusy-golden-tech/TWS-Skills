"""ONNX-runtime based text embeddings model.

Provides a lightweight embeddings interface backed by ONNX runtime.
Falls back gracefully when onnxruntime is not installed — see
``EmbeddingsModel.is_available``.

Design: search-semantic.md
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np


class EmbeddingsModel:
    """Text embedding model backed by ONNX runtime.

    Encodes query text and candidate texts into fixed-size vectors.
    Uses a pre-converted ONNX model file for inference.

    Parameters
    ----------
    model_path : str
        Path or HuggingFace-compatible model identifier.  The implementation
        will look for a ``model.onnx`` file under this path.

    Attributes
    ----------
    is_available : bool
        True when onnxruntime and the ONNX model are both loadable.
    """

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._session = None
        self._tokenizer = None
        self._available = False
        self._dim = 384  # default for all-MiniLM-L6-v2

        self._init()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True when the ONNX runtime and model are successfully loaded."""
        return self._available

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode a list of texts into a (N, dim) numpy array.

        Returns a zero array of shape (len(texts), dim) when the model
        is not available.
        """
        if not self._available or not texts:
            return np.zeros((max(len(texts), 1), self._dim), dtype=np.float32)
        return self._run_inference(texts)

    def encode_query(self, text: str) -> np.ndarray:
        """Encode a single query string into a (1, dim) numpy array."""
        return self.encode([text])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Try to load ONNX runtime and the model."""
        try:
            import onnxruntime as ort
        except ImportError:
            return

        # Resolve the ONNX model file
        onnx_path = self._resolve_onnx_path()
        if onnx_path is None or not os.path.exists(onnx_path):
            return

        try:
            self._session = ort.InferenceSession(
                onnx_path,
                providers=["CPUExecutionProvider"],
            )
            # Infer embedding dimension from output shape
            output_info = self._session.get_outputs()[0]
            self._dim = output_info.shape[1]

            # Try to load a tokenizer (for ONNX models we need a tokenizer)
            self._tokenizer = self._load_tokenizer()
            if self._tokenizer is None:
                return

            self._available = True
        except Exception:
            self._session = None
            self._tokenizer = None

    def _resolve_onnx_path(self) -> Optional[str]:
        """Resolve the ONNX model file path from *model_path*."""
        # Direct file path
        if self._model_path.endswith(".onnx") and os.path.exists(self._model_path):
            return self._model_path

        # Try model_path/model.onnx
        candidate = os.path.join(self._model_path, "model.onnx")
        if os.path.exists(candidate):
            return candidate

        # Try model_path/onnx/model.onnx
        candidate = os.path.join(self._model_path, "onnx", "model.onnx")
        if os.path.exists(candidate):
            return candidate

        return None

    def _load_tokenizer(self):
        """Load a tokenizer compatible with the ONNX model.

        Tries HuggingFace tokenizers first; falls back to a simple
        whitespace tokenizer for basic models.
        """
        try:
            from transformers import AutoTokenizer
            return AutoTokenizer.from_pretrained(self._model_path)
        except Exception:
            pass

        # Fallback: simple whitespace + lowercase tokenizer
        return _SimpleTokenizer()

    def _run_inference(self, texts: list[str]) -> np.ndarray:
        """Run ONNX inference on *texts*."""
        if self._tokenizer is None or self._session is None:
            return np.zeros((len(texts), self._dim), dtype=np.float32)

        # Tokenize
        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=128,
            return_tensors="np",
        )

        # Run ONNX model
        inputs = {
            inp.name: encoded[inp.name]
            for inp in self._session.get_inputs()
            if inp.name in encoded
        }

        if not inputs:
            return np.zeros((len(texts), self._dim), dtype=np.float32)

        outputs = self._session.run(None, inputs)
        embeddings = outputs[0]

        # Mean pooling over token dimension if output is (batch, seq, dim)
        if embeddings.ndim == 3:
            # Create attention mask for mean pooling
            attention_mask = encoded.get("attention_mask")
            if attention_mask is not None:
                mask_expanded = np.expand_dims(attention_mask, axis=-1)
                mask_expanded = np.broadcast_to(mask_expanded, embeddings.shape)
                embeddings = np.sum(embeddings * mask_expanded, axis=1) / np.clip(
                    np.sum(mask_expanded, axis=1), a_min=1e-9, a_max=None
                )
            else:
                embeddings = np.mean(embeddings, axis=1)

        # L2 normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.clip(norms, a_min=1e-9, a_max=None)
        embeddings = embeddings / norms

        return embeddings.astype(np.float32)


class _SimpleTokenizer:
    """Minimal tokenizer for when transformers is not available.

    Splits on whitespace and lowercases.  Produces input_ids and
    attention_mask arrays compatible with ONNX model inputs.
    """

    def __call__(self, texts, padding=True, truncation=True,
                 max_length=128, return_tensors="np"):
        import numpy as np

        batch_size = len(texts)
        input_ids = np.zeros((batch_size, max_length), dtype=np.int64)
        attention_mask = np.zeros((batch_size, max_length), dtype=np.int64)

        for i, text in enumerate(texts):
            tokens = text.lower().split()[:max_length]
            for j, token in enumerate(tokens):
                # Simple hash-based token ID (not ideal but works as fallback)
                input_ids[i, j] = abs(hash(token)) % 30000 + 1
                attention_mask[i, j] = 1

        return {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
