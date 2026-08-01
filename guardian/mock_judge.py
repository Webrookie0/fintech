"""Deterministic, offline stand-in for the Judge LLM."""

from __future__ import annotations

from .models import Checkpoint, JudgeDecision


class MockJudge:
    """Deterministic stand-in for the Judge LLM (offline demos only).

    Implements the same interface as Judge.call, so it can be bound to
    guardian.judge without any Guardian changes. It only adjudicates
    checkpoints that Guardian has already escalated (low similarity or
    rule violations); it never re-implements Guardian's fast-path logic.

    The drift floor is derived from Guardian's configured similarity
    threshold (drift_floor = threshold * 0.25) instead of a hardcoded
    constant, keeping the mock consistent with core configuration.
    Measured with all-MiniLM-L6-v2 at threshold 0.45, step similarity
    ratios vs the threshold are ~0.41-0.62x for legitimate generic
    setup steps and ~0.02-0.10x for clear goal drift.
    """

    def __init__(self, similarity_threshold: float) -> None:
        self.similarity_threshold = similarity_threshold
        self.drift_floor = similarity_threshold * 0.25

    def call(
        self,
        checkpoint: Checkpoint,
        similarity: float,
        triggered_rules: list[str],
    ) -> JudgeDecision:
        """Render a verdict with the following priority:

        1. budget_exhausted AND retry_limit_exceeded AND severe drift -> TERMINATE
        2. any triggered rules                                          -> PAUSE
        3. clear semantic drift (similarity below the drift floor)      -> PAUSE
        4. otherwise                                                    -> ALLOW
        """
        rules = set(triggered_rules)

        if (
            "budget_exhausted" in rules
            and "retry_limit_exceeded" in rules
            and similarity < self.drift_floor
        ):
            return self._decision(
                checkpoint, similarity, rules,
                "TERMINATE",
                "Budget exhausted, retry limit exceeded, and severe semantic drift.",
            )

        if rules:
            return self._decision(
                checkpoint, similarity, rules,
                "PAUSE",
                "Rule violation: " + ", ".join(sorted(rules)),
            )

        if similarity < self.drift_floor:
            return self._decision(
                checkpoint, similarity, rules,
                "PAUSE",
                f"Low similarity ({similarity:.4f}) to the goal; possible goal drift.",
            )

        return self._decision(
            checkpoint, similarity, rules,
            "ALLOW",
            "No rule violations and similarity above the drift floor.",
        )

    def _decision(
        self,
        checkpoint: Checkpoint,
        similarity: float,
        rules: set[str],
        action: str,
        reason: str,
    ) -> JudgeDecision:
        """Wrap a verdict in the shared JudgeDecision shape (source=judge)."""
        return JudgeDecision(
            action=action,
            reason=reason,
            similarity=similarity,
            source="judge",
            triggered_rules=tuple(sorted(rules)),
        )
