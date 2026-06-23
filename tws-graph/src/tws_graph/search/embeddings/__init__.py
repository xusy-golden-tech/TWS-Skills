"""Semantic embeddings module for the TWS Code Graph.

Provides ONNX-runtime-based text embeddings for semantic search signals.
"""

from .model import EmbeddingsModel  # noqa: F401

__all__ = ["EmbeddingsModel"]
