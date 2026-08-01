"""Policy engine — the deterministic hard gate.

Pure, config-driven, zero LLM. The autonomous agent can never modify these
rules. A policy verdict can only be overridden by nothing; it is the final
word on whether an intent may proceed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuleResult:
    rule: str
    passed: bool
    message: str


@dataclass
class Verdict:
    decision: str  # "approve" | "reject"
    rules: list[RuleResult] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.decision == "approve"

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reasons": self.reasons,
            "rules": [{"rule": r.rule, "passed": r.passed, "message": r.message} for r in self.rules],
        }


class PolicyEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.policy = cfg.get("policy", {})

    def kill_switch_active(self) -> bool:
        return bool(self.policy.get("kill_switch", False))

    def rule_recipient_allowlist(self, intent: dict) -> RuleResult:
        recipient = (intent.get("recipient") or "").strip()
        allowlist = self.policy.get("allowlist_recipients", [])
        blocklist = self.policy.get("blocklist_recipients", [])
        if recipient in blocklist:
            return RuleResult("recipient_allowlist", False, f"recipient '{recipient}' is blocklisted")
        if recipient not in allowlist:
            return RuleResult("recipient_allowlist", False, f"recipient '{recipient}' is not allowlisted")
        return RuleResult("recipient_allowlist", True, f"recipient '{recipient}' is allowlisted")

    def rule_amount_limit(self, intent: dict) -> RuleResult:
        amount = float(intent.get("amount", 0) or 0)
        cap = float(self.policy.get("max_per_transaction_usd", 0))
        if amount <= 0:
            return RuleResult("amount_limit", False, "amount must be positive")
        if amount > cap:
            return RuleResult("amount_limit", False, f"amount ${amount:.2f} exceeds per-transaction cap ${cap:.2f}")
        return RuleResult("amount_limit", True, f"amount ${amount:.2f} within per-transaction cap")

    def rule_daily_budget(self, intent: dict, spent_today: float) -> RuleResult:
        amount = float(intent.get("amount", 0) or 0)
        budget = float(self.policy.get("daily_budget_usd", 0))
        if spent_today + amount > budget:
            return RuleResult(
                "daily_budget",
                False,
                f"${spent_today:.2f} spent today + ${amount:.2f} exceeds daily budget ${budget:.2f}",
            )
        return RuleResult("daily_budget", True, f"daily budget stays within ${budget:.2f}")

    def rule_retry_limit(self, retries: int) -> RuleResult:
        cap = int(self.policy.get("max_retries", 0))
        if retries > cap:
            return RuleResult("retry_limit", False, f"retry count {retries} exceeds cap {cap}")
        return RuleResult("retry_limit", True, f"retries ({retries}) within limit")

    def rule_token_budget(self, intent: dict) -> RuleResult:
        tokens = int(intent.get("estimated_tokens", 0) or 0)
        cap = int(self.policy.get("max_estimated_tokens_per_task", 0))
        if tokens > cap:
            return RuleResult("token_budget", False, f"estimated {tokens} tokens exceeds cap {cap}")
        return RuleResult("token_budget", True, f"estimated {tokens} tokens within budget")

    def evaluate(self, intent: dict, *, spent_today: float, retries: int) -> Verdict:
        if self.kill_switch_active():
            return Verdict("reject", reasons=["KILL SWITCH ACTIVE — all transactions frozen"])

        rules = [
            self.rule_recipient_allowlist(intent),
            self.rule_amount_limit(intent),
            self.rule_daily_budget(intent, spent_today),
            self.rule_retry_limit(retries),
            self.rule_token_budget(intent),
        ]
        failures = [r for r in rules if not r.passed]
        if failures:
            return Verdict("reject", rules=rules, reasons=[r.message for r in failures])
        return Verdict("approve", rules=rules, reasons=["all policy rules passed"])

    def as_dict(self) -> dict:
        return {
            "daily_budget_usd": self.policy.get("daily_budget_usd"),
            "max_per_transaction_usd": self.policy.get("max_per_transaction_usd"),
            "allowlist_recipients": self.policy.get("allowlist_recipients", []),
            "blocklist_recipients": self.policy.get("blocklist_recipients", []),
            "allowlist_domains": self.policy.get("allowlist_domains", []),
            "max_retries": self.policy.get("max_retries"),
            "max_snapshots_without_progress": self.policy.get("max_snapshots_without_progress"),
            "max_estimated_tokens_per_task": self.policy.get("max_estimated_tokens_per_task"),
            "kill_switch": self.policy.get("kill_switch", False),
        }
