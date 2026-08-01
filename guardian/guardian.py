"""Guardian pipeline: rules -> embeddings -> similarity -> decision."""

from __future__ import annotations

import numpy as np

from .embeddings import Embedder
from .judge import Judge
from .models import Checkpoint, JudgeDecision
from .rule_engine import RuleEngine
from .similarity import cosine_similarity


class Guardian:
    """Supervises a single agent checkpoint through the full pipeline."""

    # Calibrated against all-MiniLM-L6-v2 on activity-only embeddings:
    # on-task checkpoints score ~0.63-0.73, off-task ~0.00-0.11.
    def __init__(self, similarity_threshold: float = 0.45) -> None:
        self.rule_engine = RuleEngine()
        self.embedder = Embedder()
        self.judge = Judge()
        self.similarity_threshold = similarity_threshold
        self._goal_embeddings: dict[str, np.ndarray] = {}

    def _goal_embedding(self, goal: str) -> np.ndarray:
        """Return the goal's embedding, computed once per unique goal."""
        if goal not in self._goal_embeddings:
            self._goal_embeddings[goal] = self.embedder.embed(goal)
        return self._goal_embeddings[goal]

    def supervise(self, checkpoint: Checkpoint) -> JudgeDecision:
        """Process a checkpoint and return a supervision verdict.

        Pipeline:
            1. Rule engine flags risk signals in the checkpoint.
            2. The goal embedding is fetched from the per-instance cache.
            3. The current step is embedded; cosine similarity with the goal.
            4. Fast path: similarity is high AND no rules fired -> ALLOW.
            5. Otherwise the Judge LLM renders the final verdict.
        """
        triggered_rules = self.rule_engine.evaluate(checkpoint)

        goal_embedding = self._goal_embedding(checkpoint.goal)
        step_embedding = self.embedder.embed(checkpoint.current_step)
        similarity = cosine_similarity(goal_embedding, step_embedding)

        if similarity >= self.similarity_threshold and not triggered_rules:
            return JudgeDecision(
                action="ALLOW",
                reason="Similarity is high and no rules were triggered.",
                similarity=similarity,
                source="rule-engine",
            )

        return self.judge.call(checkpoint, similarity, triggered_rules)
