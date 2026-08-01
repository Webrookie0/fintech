"""Agent harness — the UNTRUSTED agent.

The agent is a loop that proposes actions and uses tools. It is assumed to be
able to hallucinate, drift, or go malicious. Its ONLY path to spending money is
through the Guardian API — every spend tool is a wrapper around
`guardian.submit_request`. If it tries to call the wallet directly, the wallet
refuses.

This is a deliberately simple harness: the point of the project is the
enforcement layer, not the agent's intelligence.
"""

from __future__ import annotations


class AgentHarness:
    def __init__(self, cfg: dict, guardian, plan: list[dict] | None = None):
        self.cfg = cfg
        self.guardian = guardian
        self.plan = plan or _default_plan(cfg)

    def propose_spend(self, recipient: str, amount: float, reason: str, task: str = "") -> dict:
        return {
            "task": task or f"Pay {recipient}",
            "recipient": recipient,
            "amount": amount,
            "currency": "USD",
            "reason": reason,
            "estimated_tokens": 8000,
        }

    def run(self, observer=None) -> list[dict]:
        """Execute the plan. A 'spend' step goes through Guardian; a 'work' step
        is just a snapshot update. Returns the list of step results."""
        results: list[dict] = []
        for step in self.plan:
            if self.guardian.memory.status != "running":
                results.append({"step": step, "decision": "rejected", "reason": "workflow not running"})
                break
            kind = step.get("kind")
            if kind == "work":
                self.guardian.post_snapshot(step.get("snapshot", {}))
                self.guardian.log.append("agent_action", step="work", detail=step.get("detail", ""))
                results.append({"step": step, "decision": "worked"})
            elif kind == "spend":
                intent = self.propose_spend(
                    step["recipient"], step["amount"], step.get("reason", ""), step.get("task", "")
                )
                result = self.guardian.submit_request(intent, actor="agent")
                results.append({"step": step, **result})
            if observer:
                observer(step, results[-1])
        return results


def _default_plan(cfg: dict) -> list[dict]:
    return [
        {"kind": "work", "snapshot": {"current_step": "Planning implementation", "current_tool": "none"},
         "detail": "Planning the auth feature and estimating API needs."},
        {"kind": "spend", "recipient": "OpenAI", "amount": 5, "reason": "Buy API credits for the coding task",
         "task": "Purchase OpenAI credits"},
        {"kind": "work", "snapshot": {"current_step": "Implementation in progress", "current_tool": "OpenAI API"},
         "detail": "Implementing with the purchased credits."},
    ]
