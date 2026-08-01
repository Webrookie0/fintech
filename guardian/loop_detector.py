"""Loop detector — pure code, no LLM.

The most common failure mode of autonomous agents is repeating the same action
forever while burning tokens and money. The detector fingerprints each context
snapshot (current step + current tool + step count). If nothing changes for N
snapshots, or the retry counter blows past its cap, Guardian pauses or
terminates the workflow — before the spend gets out of hand.
"""

from __future__ import annotations

import hashlib


def fingerprint(snapshot: dict) -> str:
    step = (snapshot.get("current_step") or "").strip().lower()
    tool = (snapshot.get("current_tool") or "").strip().lower()
    # token spend is a progress signal too: an identical step that consumed a
    # large number of tokens is still stuck.
    tokens = int(snapshot.get("tokens", 0) or 0)
    if tokens > 500:
        tokens = 500
    raw = f"{step}|{tool}|{tokens}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class LoopDetector:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.policy = cfg.get("policy", {})
        self._last_hash: str | None = None
        self.stall_count = 0
        self.checks = 0
        self.terminated = False

    def reset(self) -> None:
        self._last_hash = None
        self.stall_count = 0
        self.checks = 0
        self.terminated = False

    def observe(self, snapshot: dict, memory) -> dict:
        """Returns {"state": "running"|"paused"|"terminated", "stall_count", "reason"}"""
        if self.terminated:
            return {"state": "terminated", "stall_count": self.stall_count, "reason": memory.terminate_reason}

        fp = fingerprint(snapshot)
        self.checks += 1
        max_stall = int(self.policy.get("max_snapshots_without_progress", 0))
        max_retries = int(self.policy.get("max_retries", 0))

        if fp == self._last_hash:
            self.stall_count += 1
        else:
            self.stall_count = 0
        self._last_hash = fp

        retries = int(memory.retries)

        reason = ""
        state = "running"
        if retries > max_retries:
            state = "terminated"
            reason = f"retry limit exceeded ({retries} retries > cap {max_retries})"
        elif self.stall_count >= max_stall:
            state = "terminated"
            reason = f"no progress for {self.stall_count} snapshots (stall threshold {max_stall})"
        elif self.stall_count >= max_stall - 1:
            state = "paused"
            reason = f"approaching stall threshold ({self.stall_count}/{max_stall} unchanged snapshots)"

        if state in ("paused", "terminated"):
            self.terminated = True
            memory.status = state
            memory.terminate_reason = reason

        return {"state": state, "stall_count": self.stall_count, "reason": reason}
