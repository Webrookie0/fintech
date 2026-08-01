"""Rule-based checks that flag risk signals in an agent checkpoint."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .models import Checkpoint


@dataclass
class RuleEngine:
    """Cheap, deterministic safeguards evaluated before any LLM is called.

    The only state kept is a per-session counter of tool usage so that
    repeated-tool detection works across multiple checkpoints.
    """

    max_retries: int = 3
    budget_floor: float = 0.0
    max_tool_uses: int = 5
    _tool_usage: Counter = field(default_factory=Counter, init=False, repr=False)

    def evaluate(self, checkpoint: Checkpoint) -> list[str]:
        """Return the names of every rule triggered by the checkpoint."""
        triggered: list[str] = []

        if checkpoint.retry_count > self.max_retries:
            triggered.append("retry_limit_exceeded")

        if checkpoint.budget_remaining <= self.budget_floor:
            triggered.append("budget_exhausted")

        self._tool_usage[checkpoint.tool_name] += 1
        if self._tool_usage[checkpoint.tool_name] > self.max_tool_uses:
            # Placeholder: production would count usage from the session
            # activity log rather than an in-memory counter.
            triggered.append("repeated_tool_usage")

        return triggered
