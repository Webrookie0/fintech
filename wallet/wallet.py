"""Mock wallet — the enforcement layer with the last word.

The wallet TRUSTS NOTHING. It keeps its own copy of the spend policy, its own
balance and ledger, and its own kill-switch flag. Even if the autonomous agent
calls it directly with a forged approval, the wallet re-checks every criterion
itself before any money moves. This is the "even if the LLM becomes malicious,
it cannot bypass Guardian" guarantee.

No real money is ever involved. Balance is a number in a JSON file.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Wallet:
    def __init__(self, policy: dict, state_path: Path, starting_balance: float = 25.0):
        self.policy = policy
        self.state_path = Path(state_path)
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {
            "balance": float(starting_balance),
            "ledger": [],
            "kill_switch": False,
            "reset_count": 0,
        }
        self.load()

    # --- persistence -------------------------------------------------------
    def load(self) -> None:
        if self.state_path.exists():
            try:
                saved = json.loads(self.state_path.read_text(encoding="utf-8"))
                self._state.update({k: v for k, v in saved.items() if k in self._state})
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    # --- public state ------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "balance": round(self._state["balance"], 2),
                "kill_switch": self._state["kill_switch"],
                "ledger": list(self._state["ledger"][-40:][::-1]),
                "transaction_count": len(self._state["ledger"]),
            }

    def spent_today(self) -> float:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        with self._lock:
            return round(
                sum(float(tx["amount"]) for tx in self._state["ledger"] if tx["ts"][:10] == today),
                2,
            )

    def kill(self) -> dict:
        with self._lock:
            self._state["kill_switch"] = True
            self.save()
            return {"kill_switch": True}

    def unkill(self) -> dict:
        with self._lock:
            self._state["kill_switch"] = False
            self.save()
            return {"kill_switch": False}

    def reset(self, balance: float | None = None) -> dict:
        with self._lock:
            self._state["ledger"] = []
            self._state["kill_switch"] = False
            if balance is not None:
                self._state["balance"] = float(balance)
            self._state["reset_count"] += 1
            self.save()
            return self.snapshot()

    # --- the money path ----------------------------------------------------
    def pay(self, payment: dict, approval: dict | None = None) -> dict:
        """Execute (or refuse) a payment. `approval` is what Guardian issues —
        but the wallet re-checks EVERYTHING itself. A forged or missing approval
        changes nothing: the policy and the wallet's own state decide."""
        with self._lock:
            started = datetime.now(UTC).isoformat(timespec="milliseconds")

            def refuse(reason: str) -> dict:
                return {"executed": False, "reason": reason, "ledger_id": None}

            if self._state["kill_switch"]:
                return refuse("wallet kill switch is ACTIVE — frozen")
            if approval is not None and not approval.get("approved"):
                return refuse("approval token rejected by Guardian")

            recipient = (payment.get("recipient") or "").strip()
            amount = float(payment.get("amount", 0) or 0)

            allowlist = self.policy.get("allowlist_recipients", [])
            if recipient not in allowlist:
                return refuse(f"recipient '{recipient}' not allowlisted (wallet check)")

            if amount <= 0:
                return refuse("amount must be positive (wallet check)")
            if amount > float(self.policy.get("max_per_transaction_usd", 0)):
                return refuse(f"amount ${amount:.2f} over per-transaction cap (wallet check)")

            budget = float(self.policy.get("daily_budget_usd", 0))
            today = started[:10]
            spent = sum(float(tx["amount"]) for tx in self._state["ledger"] if tx["ts"][:10] == today)
            if spent + amount > budget:
                return refuse(f"${spent:.2f} spent today + ${amount:.2f} over daily budget ${budget:.2f} (wallet check)")

            if amount > float(self._state["balance"]):
                return refuse(f"insufficient balance: ${self._state['balance']:.2f} < ${amount:.2f}")

            self._state["balance"] = round(float(self._state["balance"]) - amount, 2)
            ledger_id = f"tx-{len(self._state['ledger']) + 1}"
            self._state["ledger"].append(
                {
                    "id": ledger_id,
                    "ts": started,
                    "recipient": recipient,
                    "amount": amount,
                    "currency": payment.get("currency", "USD"),
                    "reason": payment.get("reason", ""),
                    "approval_id": (approval or {}).get("approval_id"),
                }
            )
            self.save()
            return {
                "executed": True,
                "ledger_id": ledger_id,
                "balance": round(self._state["balance"], 2),
                "amount": amount,
                "recipient": recipient,
            }
