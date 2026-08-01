"""Small Judge LLM — an advisory review layer.

The judge reads a structured transaction intent and produces a recommendation
with a reason. It can flag objective drift and suspicious loops. It is NOT the
enforcement layer: the PolicyEngine has the final word. This keeps an LLM out
of the trust path — prompting can advise, it can never guarantee.

If no LLM provider is configured the judge degrades to a deterministic
heuristic, so the whole system still runs offline for the demo.
"""

from __future__ import annotations

from .llm import LLMClient, parse_json

JUDGE_PROMPT = """You are the Judge in "Guardian", a policy-enforced control plane for autonomous AI agents.
Your ONLY job: review one structured transaction intent and produce a recommendation.
You are ADVISORY. A separate deterministic policy engine makes the binding decision.

Consider: is the request within the spirit of the policy? Does it show objective drift?
Does it look like a retry loop (same request repeated with no progress)? Be concise.

Respond with ONLY JSON:
{{"recommendation": "approve" | "reject" | "review", "confidence": 0.0-1.0, "reason": "one sentence", "drift": true|false, "loop_suspect": true|false}}

Policy summary:
{policy}

Execution state:
{state}

Transaction intent:
{intent}
"""


class Judge:
    def __init__(self, cfg: dict, client: LLMClient, log):
        self.cfg = cfg
        self.client = client
        self.log = log
        self.policy = cfg.get("policy", {})

    def review(self, intent: dict, state: dict) -> dict:
        if self.client.available:
            try:
                result = self._llm_review(intent, state)
                self.log.append("judge_review", source=self.client.describe(), **result)
                return result
            except Exception as exc:
                self.log.append("judge_review", source="llm-error", error=str(exc), fallback=True)
        result = self._heuristic(intent, state)
        self.log.append("judge_review", source="heuristic", **result)
        return result

    def _llm_review(self, intent: dict, state: dict) -> dict:
        prompt = JUDGE_PROMPT.format(
            policy=_fmt_policy(self.policy),
            state=_fmt_state(state),
            intent=_fmt_intent(intent),
        )
        text = self.client.complete(prompt)
        data = parse_json(text) or {}
        return {
            "recommendation": data.get("recommendation", "review"),
            "confidence": float(data.get("confidence", 0.5)),
            "reason": str(data.get("reason", "no reason given")),
            "drift": bool(data.get("drift", False)),
            "loop_suspect": bool(data.get("loop_suspect", False)),
        }

    def _heuristic(self, intent: dict, state: dict) -> dict:
        recipient = (intent.get("recipient") or "").strip()
        amount = float(intent.get("amount", 0) or 0)
        allowlist = self.policy.get("allowlist_recipients", [])
        retries = int(state.get("retries", 0))
        cap = float(self.policy.get("max_per_transaction_usd", 0))
        budget = float(self.policy.get("daily_budget_usd", 0))
        spent = float(state.get("spent_today", 0))

        if recipient not in allowlist:
            return _heuristic_result("reject", 0.95, f"'{recipient}' is not an allowlisted counterparty")
        if amount > cap:
            return _heuristic_result("reject", 0.9, f"amount exceeds the per-transaction cap")
        if spent + amount > budget:
            return _heuristic_result("reject", 0.85, "would breach the daily budget")
        if retries > int(self.policy.get("max_retries", 0)):
            return _heuristic_result("reject", 0.9, "too many retries — likely stuck", loop_suspect=True)
        return _heuristic_result(
            "approve", 0.8, "intent is consistent with the goal and within policy bounds"
        )


def _heuristic_result(rec: str, conf: float, reason: str, drift: bool = False, loop_suspect: bool = False) -> dict:
    return {
        "recommendation": rec,
        "confidence": conf,
        "reason": reason,
        "drift": drift,
        "loop_suspect": loop_suspect,
    }


def _fmt_policy(p: dict) -> str:
    lines = [
        f"  daily_budget_usd: {p.get('daily_budget_usd')}",
        f"  max_per_transaction_usd: {p.get('max_per_transaction_usd')}",
        f"  allowlist_recipients: {p.get('allowlist_recipients', [])}",
        f"  max_retries: {p.get('max_retries')}",
        f"  max_estimated_tokens_per_task: {p.get('max_estimated_tokens_per_task')}",
    ]
    return "\n".join(lines)


def _fmt_state(s: dict) -> str:
    return (
        f"  spent_today: ${s.get('spent_today', 0)}\n"
        f"  budget_remaining: ${s.get('budget_remaining', 0)}\n"
        f"  retries: {s.get('retries', 0)}\n"
        f"  current_step: {s.get('current_step', 'unknown')}"
    )


def _fmt_intent(i: dict) -> str:
    return (
        f"  task: {i.get('task')}\n"
        f"  recipient: {i.get('recipient')}\n"
        f"  amount: {i.get('amount')} {i.get('currency', 'USD')}\n"
        f"  reason: {i.get('reason')}\n"
        f"  estimated_tokens: {i.get('estimated_tokens')}"
    )
