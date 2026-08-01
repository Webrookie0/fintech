"""Vector similarity metrics."""

from __future__ import annotations

import numpy as np


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Return the cosine similarity between two vectors, in [0, 1]."""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        # Degenerate zero vectors share no direction; treat as unrelated.
        return 0.0
    return float(np.dot(a, b) / denom)
