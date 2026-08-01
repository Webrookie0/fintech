"""Guardian service — the trusted execution layer.

Every financial action proposed by the untrusted agent passes through here:

    intent ──▶ Judge (advisory) ──▶ Policy (hard gate) ──▶ Wallet (re-checks)
                │                          │                       │
                └────────── all appended to the audit log ────────┘

The agent never talks to the wallet directly. Even if it did, the wallet
independently re-verifies everything.
"""

from __future__ import annotations

import time
import uuid

from .events import EventLog
from .judge import Judge
from .llm import LLMClient
from .loop_detector import LoopDetector
from .memory import Memory
from .policy import PolicyEngine


class Guardian:
    def __init__(self, cfg: dict, log: EventLog, wallet):
        self.cfg = cfg
        self.log = log
        self.wallet = wallet
        self.policy = PolicyEngine(cfg)
        self.llm = LLMClient(
            provider=cfg.get("llm", {}).get("provider", "auto"),
            model=cfg.get("llm", {}).get("judge_model", ""),
        )
        self.memory = Memory(cfg, wallet)
        self.judge = Judge(cfg, self.llm, log)
        self.loop = LoopDetector(cfg)

        self.log.append("system", event="guardian_started", judge=self.llm.describe())

    # --- the money path -----------------------------------------------------
    def submit_request(self, intent: dict, actor: str = "agent") -> dict:
        intent = {
            "task": intent.get("task", ""),
            "recipient": intent.get("recipient", ""),
            "amount": float(intent.get("amount", 0) or 0),
            "currency": intent.get("currency", "USD"),
            "reason": intent.get("reason", ""),
            "estimated_tokens": int(intent.get("estimated_tokens", 0) or 0),
        }
        self.log.append("intent_received", actor=actor, intent=intent)

        if self.policy.kill_switch_active():
            return self._reject(intent, "KILL SWITCH ACTIVE — all spending frozen")

        if self.memory.status != "running":
            result = {
                "decision": "reject",
                "status": self.memory.status,
                "reason": f"workflow {self.memory.status}: {self.memory.terminate_reason}",
                "approval_id": None,
                "wallet": None,
            }
            self.log.append("decision", **result)
            return result

        state = self.memory.state()
        judge = self.judge.review(intent, state)
        verdict = self.policy.evaluate(intent, spent_today=state["spent_today"], retries=self.memory.retries)
        self.log.append("policy_verdict", **verdict.to_dict())

        # The binding decision. Policy veto wins over the judge's advice.
        decision, source, reason = self._combine(verdict, judge)

        if decision == "reject":
            return self._reject(intent, reason, source=source, judge=judge)

        approval_id = f"ap-{uuid.uuid4().hex[:10]}"
        approval = {"approved": True, "approval_id": approval_id}
        self.log.append("decision", decision="approve", source=source, approval_id=approval_id,
                        reason=reason, intent=intent)

        wallet_result = self.wallet.pay(intent, approval=approval)
        self.log.append("wallet_result", **wallet_result, intent=intent)
        self.memory.approvals += 1

        return {
            "decision": "approve",
            "source": source,
            "reason": reason,
            "judge": judge,
            "approval_id": approval_id,
            "wallet": wallet_result,
            "status": self.memory.status,
        }

    def _reject(self, intent: dict, reason: str, source: str = "policy", judge: dict | None = None) -> dict:
        self.memory.rejections += 1
        self.memory.violations += 1
        self.log.append("decision", decision="reject", source=source, reason=reason, intent=intent)
        return {
            "decision": "reject",
            "source": source,
            "reason": reason,
            "judge": judge,
            "approval_id": None,
            "wallet": None,
            "status": self.memory.status,
        }

    @staticmethod
    def _combine(verdict, judge: dict) -> tuple[str, str, str]:
        if not verdict.passed:
            return "reject", "policy", verdict.reasons[0] if verdict.reasons else "policy violation"
        if judge.get("recommendation") == "reject" and float(judge.get("confidence", 0)) >= 0.8:
            return "reject", "judge", f"judge review: {judge.get('reason')}"
        if judge.get("drift"):
            return "reject", "judge", f"judge flagged objective drift: {judge.get('reason')}"
        return "approve", "policy", "approved by policy"

    # --- loop detection -----------------------------------------------------
    def post_snapshot(self, snapshot: dict) -> dict:
        recorded = self.memory.record_snapshot(snapshot)
        self.log.append("snapshot", snapshot=recorded)
        observed = self.loop.observe(recorded, self.memory)
        if observed["reason"]:
            self.log.append("loop_detector", **observed)
        if observed["state"] in ("paused", "terminated"):
            self.log.append(
                "system",
                event="workflow_" + observed["state"],
                reason=observed["reason"],
                spend_saved=self.memory.state()["spent_today"],
            )
        return observed

    def record_retry(self) -> dict:
        self.memory.retries += 1
        self.log.append("retry", retries=self.memory.retries)
        return {"retries": self.memory.retries}

    # --- controls -----------------------------------------------------------
    def kill_switch(self, active: bool) -> dict:
        if active:
            self.policy.policy["kill_switch"] = True
            self.wallet.kill()
            self.log.append("kill_switch", active=True)
            self.log.append("system", event="kill_switch_engaged")
        else:
            self.policy.policy["kill_switch"] = False
            self.wallet.unkill()
            self.log.append("kill_switch", active=False)
            self.log.append("system", event="kill_switch_released")
        return {"kill_switch": active}

    def reset(self) -> dict:
        self.wallet.reset(balance=self.cfg.get("wallet", {}).get("starting_balance_usd", 25.0))
        self.memory.reset()
        self.loop.reset()
        self.policy.policy["kill_switch"] = False
        self.log.append("system", event="reset")
        return self.status()

    def status(self) -> dict:
        state = self.memory.state()
        wallet = self.wallet.snapshot()
        return {
            "policy": self.policy.as_dict(),
            "state": {
                **state,
                "budget_remaining": state["budget_remaining"],
            },
            "wallet": wallet,
            "judge": self.llm.describe(),
            "status": state["status"],
            "ts": time.time(),
        }
