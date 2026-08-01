"""Dynamic memory — the execution metadata Guardian keeps.

Guardian does not store the conversation; it stores the execution state:
spent today, budget remaining, retries, the current step/tool, recent context
snapshots, and policy violation counts. That is everything the policy engine
and loop detector need to make fast, deterministic decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime


class Memory:
    def __init__(self, cfg: dict, wallet):
        self.cfg = cfg
        self.wallet = wallet
        agent_cfg = cfg.get("agent", {})
        self.current_goal = agent_cfg.get("goal", "")
        self.current_step = "Not started"
        self.current_tool = ""
        self.retries = 0
        self.snapshots: list[dict] = []
        self.stall_count = 0
        self.status = "running"  # running | paused | terminated
        self.terminate_reason = ""
        self.violations = 0
        self.approvals = 0
        self.rejections = 0
        self.steps_taken = 0

    def reset(self) -> None:
        agent_cfg = self.cfg.get("agent", {})
        self.current_goal = agent_cfg.get("goal", "")
        self.current_step = "Not started"
        self.current_tool = ""
        self.retries = 0
        self.snapshots = []
        self.stall_count = 0
        self.status = "running"
        self.terminate_reason = ""
        self.violations = 0
        self.approvals = 0
        self.rejections = 0
        self.steps_taken = 0

    def state(self) -> dict:
        budget = float(self.cfg.get("policy", {}).get("daily_budget_usd", 0))
        spent = self.wallet.spent_today()
        return {
            "current_goal": self.current_goal,
            "current_step": self.current_step,
            "current_tool": self.current_tool,
            "retries": self.retries,
            "steps_taken": self.steps_taken,
            "spent_today": spent,
            "budget_remaining": round(budget - spent, 2),
            "status": self.status,
            "stall_count": self.stall_count,
            "terminate_reason": self.terminate_reason,
            "violations": self.violations,
            "approvals": self.approvals,
            "rejections": self.rejections,
            "snapshots": list(self.snapshots[-12:][::-1]),
        }

    def record_snapshot(self, snapshot: dict) -> dict:
        snapshot = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            **snapshot,
        }
        self.snapshots.append(snapshot)
        self.current_step = snapshot.get("current_step", self.current_step)
        self.current_tool = snapshot.get("current_tool", "")
        return snapshot
