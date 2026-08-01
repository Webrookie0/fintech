"""CLI entry point for the Guardian supervision engine.

Usage:
    python -m guardian.main --goal "Build a login page." \
        --step "Adding the email input field to the login form." \
        --retries 0 --budget 100 --tool "vscode-edit"
"""

from __future__ import annotations

import argparse

from .guardian import Guardian
from .models import Checkpoint


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="guardian",
        description="Supervise an AI agent checkpoint through the Guardian pipeline.",
    )
    parser.add_argument("--goal", required=True, help="The agent's original goal.")
    parser.add_argument("--step", required=True, help="The agent's current step.")
    parser.add_argument("--retries", type=int, default=0, help="Current retry count.")
    parser.add_argument("--budget", type=float, default=100.0, help="Budget remaining.")
    parser.add_argument("--tool", default="terminal", help="Tool currently in use.")
    return parser.parse_args()


def _print_report(checkpoint: Checkpoint, decision, guardian: Guardian) -> None:
    """Print the full pipeline trace to the terminal."""
    separator = "=" * 64
    print(separator)
    print("GUARDIAN SUPERVISION REPORT")
    print(separator)

    print("\nCheckpoint")
    print(f"  Goal:             {checkpoint.goal}")
    print(f"  Current step:     {checkpoint.current_step}")
    print(f"  Retry count:      {checkpoint.retry_count}")
    print(f"  Budget remaining: ${checkpoint.budget_remaining:.2f}")
    print(f"  Tool:             {checkpoint.tool_name}")

    print("\nCheckpoint summary (human-readable)")
    print(f"  {checkpoint.to_summary().replace(chr(10), chr(10) + '  ')}")

    print("\nEmbedded text (current_step)")
    print(f"  {checkpoint.current_step}")

    print("\n[1/3] Rule engine")
    if decision.triggered_rules:
        print(f"  Triggered rules:  {', '.join(decision.triggered_rules)}")
    else:
        print("  Triggered rules:  none")

    print("\n[2/3] Embeddings + similarity")
    print(f"  Embedding model:  {guardian.embedder.model_name}")
    print(f"  Cosine similarity (goal vs current_step): {decision.similarity:.4f}")

    print("\n[3/3] Decision")
    source = "rule-engine (fast path)" if decision.source == "rule-engine" else "judge (LLM)"
    print(f"  Source:           {source}")
    print(f"  Verdict:          {decision.action}")
    print(f"  Reason:           {decision.reason}")
    print(separator)


def main() -> None:
    args = _parse_args()
    checkpoint = Checkpoint(
        goal=args.goal,
        current_step=args.step,
        retry_count=args.retries,
        budget_remaining=args.budget,
        tool_name=args.tool,
    )
    guardian = Guardian()
    decision = guardian.supervise(checkpoint)
    _print_report(checkpoint, decision, guardian)


if __name__ == "__main__":
    main()
