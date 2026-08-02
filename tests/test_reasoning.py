"""Unit tests for the reasoning supervision layer.

Covers the fixes that closed the agent's escape hatch:
  - P1: a passing checkpoint cannot un-pause a paused session
  - P2: drift is anchored to the session's own first goal, not the config goal
  - P3: repeated identical tool+args (>=3x) is a repeat-strategy signal
  - P0: a paused/terminated session freezes the wallet's spend path
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import yaml  # noqa: E402

from guardian.core import Guardian  # noqa: E402
from guardian.events import EventLog  # noqa: E402
from guardian.reasoning import CheckpointDB, Embedder, ReasoningTrail, detect_signals  # noqa: E402
from wallet.wallet import Wallet  # noqa: E402


def _llm_client():
    from guardian.llm import LLMClient

    return LLMClient(provider="mock", model="")


def _env(cfg: dict, tmp: str) -> tuple[Guardian, ReasoningTrail, Wallet]:
    log = EventLog(Path(tmp) / "events.jsonl")
    wallet = Wallet(cfg["policy"], Path(tmp) / "wallet.json", starting_balance=cfg["wallet"]["starting_balance_usd"])
    guardian = Guardian(cfg, log, wallet)
    trail = ReasoningTrail(cfg, CheckpointDB(str(Path(tmp) / "cp.db")), guardian.llm, log=log, wallet=wallet)
    guardian.reasoning = trail
    return guardian, trail, wallet


def _cfg() -> dict:
    return yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))


def test_paused_session_cannot_self_resume():
    """P1: once paused, a clean checkpoint stays paused until the owner resumes."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        # First checkpoint: stable goal, no signals → running.
        r1 = trail.record("s", "## Goal\nBuild auth\n\n## Plan\nscaffold", tool_trail=[{"tool": "write", "input": {"filePath": "a.py"}, "status": "completed"}])
        assert r1["state"] == "running"
        # First contradiction is a WARNING (escalation ladder), not a pause.
        r2 = trail.record(
            "s",
            "## Goal\nBuild auth\n\n## Plan\nall tests pass now",
            tool_trail=[{"tool": "bash", "input": {"command": "pytest"}, "status": "error", "output": "FAILED: 3 tests failed"}],
        )
        assert r2["state"] == "running", "first signal must warn, not pause"
        assert "WARNING" in r2["reason"]
        # Repeating the same contradiction escalates to a pause.
        r2b = trail.record(
            "s",
            "## Goal\nBuild auth\n\n## Plan\nall tests pass now",
            tool_trail=[{"tool": "bash", "input": {"command": "pytest"}, "status": "error", "output": "FAILED: 3 tests failed"}],
        )
        assert r2b["state"] == "paused"
        # Now a perfectly clean checkpoint (the exploit) must NOT un-pause.
        r3 = trail.record(
            "s",
            "## Goal\nBuild auth\n\n## Plan\nmake progress",
            tool_trail=[{"tool": "write", "input": {"filePath": "auth.py"}, "status": "completed"}],
        )
        assert r3["state"] == "paused", "clean checkpoint must not un-pause a paused session"
        assert "awaiting owner review" in r3["reason"]
        # Only the owner can resume; the agent then continues the SAME plan.
        assert trail.resume("s")["status"] == "running"
        r4 = trail.record(
            "s",
            "## Goal\nBuild auth\n\n## Plan\nmake progress\n\n## Progress\nlogin handler wired",
            tool_trail=[{"tool": "write", "input": {"filePath": "login.py"}, "status": "completed"}],
        )
        assert r4["state"] == "running"


def test_wallet_frozen_while_paused_and_unfrozen_on_resume():
    """P0: a paused session cannot spend; resume re-enables it."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        guardian, trail, wallet = _env(cfg, tmp)
        intent = {
            "task": "Purchase OpenAI credits", "recipient": "OpenAI", "amount": 5,
            "currency": "USD", "reason": "x", "estimated_tokens": 5000,
        }
        assert guardian.submit_request(intent, session_id="s")["decision"] == "approve"
        # First hit warns; second hit pauses.
        trail.record(
            "s",
            "## Goal\nBuild auth\n\n## Plan\nall tests pass",
            tool_trail=[{"tool": "bash", "status": "error", "output": "FAILED"}],
        )
        trail.record(
            "s",
            "## Goal\nBuild auth\n\n## Plan\nall tests pass",
            tool_trail=[{"tool": "bash", "status": "error", "output": "FAILED"}],
        )
        assert trail.status("s")["status"] == "paused"
        assert wallet.frozen("s") is True
        rejected = guardian.submit_request(intent, session_id="s")
        assert rejected["decision"] == "reject"
        assert "paused" in rejected["reason"]
        assert rejected["wallet"] is None
        # owner top-up + resume re-authorizes spend
        trail.resume("s")
        assert wallet.frozen("s") is False
        assert guardian.submit_request(intent, session_id="s")["decision"] == "approve"


def test_wallet_pay_refuses_frozen_session_directly():
    """P0: even a direct wallet call from a frozen session is refused."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, wallet = _env(cfg, tmp)
        for _ in range(2):
            trail.record(
                "s",
                "## Goal\nBuild auth\n\n## Plan\nall tests pass",
                tool_trail=[{"tool": "bash", "status": "error", "output": "FAILED"}],
            )
        assert trail.status("s")["status"] == "paused"
        res = wallet.pay(
            {"recipient": "OpenAI", "amount": 5, "currency": "USD", "reason": "x"},
            approval={"approved": True, "approval_id": "ap-forged"},
            session_id="s",
        )
        assert res["executed"] is False
        assert "frozen" in res["reason"]


def test_anchor_drift_not_config_goal():
    """P2: drift is measured against the session's first goal, not config goal."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        # Session anchors on its own goal (solve two-sum), even though the
        # config goal is "build auth feature" — no false drift.
        r1 = trail.record("s2", "## Goal\nSolve two sum with a hash map", tool_trail=[{"tool": "write", "status": "completed"}])
        assert r1["state"] == "running", "first real goal must not false-drift vs config goal"
        # Keeping the SAME goal text across checkpoints → still running.
        r2 = trail.record("s2", "## Goal\nSolve two sum with a hash map\n\n## Plan\nmore progress", tool_trail=[{"tool": "write", "status": "completed"}])
        assert r2["state"] == "running", "stable goal text must not drift"
        # Changing the goal to something entirely different → drift warns first.
        r3 = trail.record("s2", "## Goal\nBuild a crypto trading bot that gambles money", tool_trail=[{"tool": "write", "status": "completed"}])
        assert r3["state"] == "running", "first drift must warn, not pause"
        assert "WARNING" in r3["reason"]
        # Same off-anchor goal again → pause.
        r3b = trail.record("s2", "## Goal\nBuild a crypto trading bot that gambles money", tool_trail=[{"tool": "write", "status": "completed"}])
        assert r3b["state"] == "paused", "repeated drift must pause"
        assert "drift" in r3b["reason"]


def test_repeated_identical_action_is_loop():
    """P3: same tool+args 3+ times → repeat-strategy, even if context is fresh."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        trail.record("s3", "## Goal\nBuild auth\n\n## Plan\nstep 1", tool_trail=[{"tool": "write", "status": "completed"}])
        trail_trail = [
            {"tool": "bash", "input": {"command": "g++ two_sum.cpp && ./two_sum"}, "status": "completed", "output": "ok"},
            {"tool": "bash", "input": {"command": "g++ two_sum.cpp && ./two_sum"}, "status": "completed", "output": "ok"},
            {"tool": "bash", "input": {"command": "g++ two_sum.cpp && ./two_sum"}, "status": "completed", "output": "ok"},
        ]
        r_warn = trail.record("s3", "## Goal\nBuild auth\n\n## Plan\nstill step 1, trying again", tool_trail=trail_trail)
        assert any(rr.get("count") and rr["count"] >= 3 for rr in r_warn["signals"]["repeat_strategy"])
        assert r_warn["state"] == "running", "first repeat-strategy must warn"
        r = trail.record("s3", "## Goal\nBuild auth\n\n## Plan\nstill step 1, trying again", tool_trail=trail_trail)
        assert r["state"] == "paused", "repeated repeat-strategy must pause"


def test_waste_receipt_present_on_pause():
    """P4: paused verdicts carry a measurable waste receipt."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        trail_trail = [
            {"tool": "bash", "status": "error", "output": "FAILED: 3 tests failed"},
            {"tool": "bash", "status": "error", "output": "FAILED: 3 tests failed"},
            {"tool": "bash", "status": "error", "output": "FAILED: 3 tests failed"},
        ]
        trail.record("s4", "## Goal\nBuild auth\n\n## Plan\nall pass", tool_trail=trail_trail)
        r = trail.record("s4", "## Goal\nBuild auth\n\n## Plan\nall pass", tool_trail=trail_trail)
        assert r["state"] == "paused"
        receipt = r.get("waste", {})
        assert "elapsed_s" in receipt
        assert receipt["tokens_burned_est"] > 0
        assert receipt["repeats"] >= 3
        assert any("same 'bash' repeated" in e for e in receipt["evidence"])


def test_stall_requires_four_identical_checkpoints():
    """P5: stall fires only after 4 identical checkpoints in a row, not on the 2nd.
    Distinct tool calls per checkpoint so the repeat-strategy signal (P3) cannot fire.
    The first stall warns; a second identical run escalates to a pause."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        cp = "## Goal\nBuild auth\n\n## Plan\nstep 1\n\n## Progress\nunchanged"
        state = None
        for i in range(4):
            state = trail.record(
                "s5", cp,
                tool_trail=[{"tool": "write", "input": {"filePath": f"file{i}.py"}, "status": "completed"}],
            )
            if i < 3:
                assert state["state"] == "running", f"checkpoint #{i + 1} must NOT stall (2nd/3rd identical repeat)"
            else:
                assert state["state"] == "running", "4th identical checkpoint must warn, not pause yet"
                assert "WARNING" in state["reason"], "first stall must be a warning"
        # A second stalled run (now 8 identical) escalates to a pause.
        pause = None
        for i in range(4, 8):
            state = trail.record(
                "s5", cp,
                tool_trail=[{"tool": "write", "input": {"filePath": f"file{i}.py"}, "status": "completed"}],
            )
            if state["state"] == "paused" and pause is None:
                pause = state
        assert pause is not None, "repeated stall must pause"
        assert "stall" in pause["reason"]


def _paused_session(trail, sid="sp"):
    for _ in range(2):
        trail.record(
            sid,
            "## Goal\nBuild auth\n\n## Plan\nall tests pass",
            tool_trail=[{"tool": "bash", "status": "error", "output": "FAILED"}],
        )
    assert trail.status(sid)["status"] == "paused"
    return sid


def test_gate_carries_mode_and_override_fields():
    """M1: gate returns {status, reason, mode, override, allowed}."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        gate = trail.gate("m1")
        assert set(gate) >= {"status", "reason", "mode", "override", "allowed"}
        assert gate["mode"] in ("enforce", "watch", "ask")
        assert gate["allowed"] is True


def test_watch_mode_never_blocks_paused_session():
    """M2: watch mode keeps a paused session's gate open."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        _paused_session(trail, "mw")
        trail.set_mode("watch")
        gate = trail.gate("mw")
        assert gate["mode"] == "watch"
        assert gate["allowed"] is True
        assert gate["status"] == "running", "gate must report running so stale plugins can't block"
        assert trail.status("mw")["status"] == "paused", "true verdict still visible to the dashboard"


def test_ask_mode_blocks_then_override_opens_gate():
    """M3: ask mode blocks a paused session, but Allow-for-5-min opens the gate."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        _paused_session(trail, "ma")
        trail.set_mode("ask")
        gate = trail.gate("ma")
        assert gate["allowed"] is False, "ask mode blocks by default"
        assert gate["mode"] == "ask"
        # Owner allows for 5 minutes → gate opens without changing the verdict.
        res = trail.override("ma", minutes=5)
        assert res["override"] is True
        assert trail.gate("ma")["allowed"] is True
        assert trail.gate("ma")["override"] is True
        assert trail.status("ma")["status"] == "paused", "verdict unchanged — only the gate opens"


def test_override_expires():
    """M4: a time-boxed override is temporary; it closes after the minutes elapse."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        _paused_session(trail, "mx")
        trail.set_mode("ask")
        res = trail.override("mx", minutes=5)
        assert res["override"] is True
        assert trail.gate("mx")["allowed"] is True
        # Simulate the window elapsing: the gate must close again.
        with trail._lock:
            trail._sessions["mx"]["override_until"] = 0.0
        gate = trail.gate("mx")
        assert gate["override"] is False
        assert gate["allowed"] is False, "expired override must re-block"


def test_override_until_closed_stays_open():
    """M7: until_closed keeps the gate open with no expiry until cleared."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        _paused_session(trail, "mu")
        trail.set_mode("ask")
        res = trail.override("mu", until_closed=True)
        assert res["until_closed"] is True
        assert res["override_minutes"] is None
        # No time-box: the gate stays open regardless of elapsed time.
        assert trail.gate("mu")["allowed"] is True
        assert trail.gate("mu")["override"] is True
        # Owner closes it early → gate returns to the paused verdict.
        trail.clear_override("mu")
        gate = trail.gate("mu")
        assert gate["allowed"] is False, "cleared until_closed override must re-block"
        # resume() also closes it and clears the verdict.
        trail.override("mu", until_closed=True)
        trail.resume("mu")
        assert trail.gate("mu")["allowed"] is True
        assert trail.gate("mu")["override"] is False


def test_override_in_enforce_mode_also_opens_gate():
    """M5: the 5-min override works in enforce mode too (escape hatch)."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        _paused_session(trail, "me")
        assert trail.gate("me")["allowed"] is False
        trail.override("me", minutes=5)
        assert trail.gate("me")["allowed"] is True
        # The session is still paused until the owner really resumes.
        trail.clear_override("me")
        assert trail.gate("me")["allowed"] is False
        trail.resume("me")
        assert trail.gate("me")["allowed"] is True
        assert trail.status("me")["status"] == "running"


def test_set_mode_rejects_invalid_values():
    """M6: only enforce/watch/ask are accepted."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        _, trail, _ = _env(cfg, tmp)
        try:
            trail.set_mode("party")
            raise AssertionError("invalid mode must raise")
        except ValueError:
            pass
        trail.set_mode("watch")
        assert trail.mode == "watch"


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            print(f"FAIL {fn.__name__}: {e}")
            traceback.print_exc()
