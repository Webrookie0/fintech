"""Dense text embeddings via sentence-transformers."""

from __future__ import annotations

import os

import numpy as np

DEFAULT_MODEL = "all-MiniLM-L6-v2"


class Embedder:
    """Embeds text into normalized dense vectors.

    The model is loaded lazily on first use so that importing Guardian
    stays cheap and the model download happens only when supervision
    actually runs.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or os.environ.get(
            "GUARDIAN_EMBED_MODEL", DEFAULT_MODEL
        )
        self._model = None

    def _load(self) -> "SentenceTransformer":
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is not installed. "
                    "Run: pip install sentence-transformers"
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> np.ndarray:
        """Return a normalized embedding vector for a single text string."""
        return self._load().encode([text], normalize_embeddings=True)[0]
