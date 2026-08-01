"""Core data types for the Guardian supervision engine."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Checkpoint:
    """A snapshot of an AI agent's state at a given moment.

    Attributes:
        goal: The original objective the agent was asked to complete.
        current_step: The action the agent is currently performing.
        retry_count: How many times the current step has been retried.
        budget_remaining: How much execution budget is left.
        tool_name: The tool currently in use by the agent.
    """

    goal: str
    current_step: str
    retry_count: int
    budget_remaining: float
    tool_name: str

    def to_summary(self) -> str:
        """Render a rich textual summary of the checkpoint.

        This summary is the primary input to the embedding engine: it
        captures not just the current step but also the surrounding agent
        state (tool, retries, budget) in natural language.
        """
        return (
            "Goal Context:\n"
            f"{self.goal}\n"
            "\n"
            "Current Activity:\n"
            f"{self.current_step}\n"
            "\n"
            "Tool:\n"
            f"{self.tool_name}\n"
            "\n"
            "Retry Count:\n"
            f"{self.retry_count}\n"
            "\n"
            "Budget Remaining:\n"
            f"{self.budget_remaining}"
        )


@dataclass(frozen=True)
class JudgeDecision:
    """The final supervision verdict produced by Guardian.

    Attributes:
        action: One of ALLOW, PAUSE or TERMINATE.
        reason: Human-readable justification for the verdict.
        similarity: Cosine similarity between the goal and the checkpoint
            summary embeddings.
        source: Which stage produced the verdict, either "rule-engine"
            (fast path) or "judge" (LLM call).
        triggered_rules: Names of the rules that fired for this checkpoint.
    """

    action: str
    reason: str
    similarity: float
    source: str
    triggered_rules: tuple[str, ...] = field(default_factory=tuple)
