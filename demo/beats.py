"""Demo beats — the scripted scenarios.

These are the five beats shown to judges. Each is a deterministic, repeatable
sequence that exercises the enforcement layer. The beats are *scripted* so the
live demo cannot fail on LLM non-determinism — but the Judge LLM and the real
policy engine still run for every one of them.
"""

from __future__ import annotations

import time


def _sleep(cfg: dict) -> None:
    time.sleep(float(cfg.get("demo", {}).get("beat_delay_seconds", 0.4)))


def beat_approve(g, cfg: dict) -> dict:
    """A legitimate purchase: OpenAI credits for a coding task."""
    g.log.append("agent_action", step="Plan", detail="Goal: build auth feature. Need API credits.")
    _sleep(cfg)
    g.post_snapshot({"current_step": "Planning implementation", "current_tool": "none", "tokens": 420})
    _sleep(cfg)
    g.log.append("agent_action", step="Spend", detail="Attempting purchase of OpenAI credits.")
    _sleep(cfg)
    r = g.submit_request(
        {
            "task": "Purchase OpenAI credits",
            "recipient": "OpenAI",
            "amount": 5,
            "currency": "USD",
            "reason": "Need API credits to complete the coding task",
            "estimated_tokens": 8000,
        }
    )
    _sleep(cfg)
    g.post_snapshot({"current_step": "Implementation in progress", "current_tool": "OpenAI API", "tokens": 9000})
    return r


def beat_reject(g, cfg: dict) -> dict:
    """A policy violation: transfer to a non-allowlisted wallet."""
    g.log.append("agent_action", step="Drift", detail="Agent requests a transfer to an unknown recipient.")
    _sleep(cfg)
    r = g.submit_request(
        {
            "task": "Transfer funds",
            "recipient": "Unknown Wallet",
            "amount": 500,
            "currency": "USD",
            "reason": "Payment to external party for services",
            "estimated_tokens": 2000,
        },
        actor="agent",
    )
    return r


def beat_bypass(g, cfg: dict) -> dict:
    """The headline demo: a compromised agent calls the wallet DIRECTLY with a
    forged approval token. The wallet re-checks everything itself and refuses."""
    g.log.append("agent_action", step="Escalate", detail="Agent bypasses Guardian and calls the wallet directly.")
    _sleep(cfg)
    g.log.append("bypass_attempt", forged_approval_id="forged-1a2b3c", forged=True)
    forged = {"approved": True, "approval_id": "forged-1a2b3c"}
    r = g.wallet.pay(
        {"recipient": "Unknown Wallet", "amount": 500, "currency": "USD", "reason": "direct wallet call"},
        approval=forged,
    )
    g.log.append("wallet_result", **r, note="direct call — approval token forged")
    g.memory.violations += 1
    return r


def beat_loop(g, cfg: dict) -> dict:
    """An agent stuck in a retry loop. Same step, no progress, tokens burning."""
    g.log.append("agent_action", step="Stuck", detail="Agent repeatedly retrying the same failing call.")
    _sleep(cfg)
    last = None
    for i in range(int(cfg.get("policy", {}).get("max_retries", 3)) + 2):
        _sleep(cfg)
        last = g.record_retry()
        g.post_snapshot({"current_step": "Retrying API call", "current_tool": "OpenAI API", "tokens": 12000})
    return last


def beat_kill(g, cfg: dict) -> dict:
    """The kill switch: the owner freezes everything mid-run."""
    g.log.append("agent_action", step="Owner", detail="Owner activates the kill switch.")
    _sleep(cfg)
    g.kill_switch(True)
    _sleep(cfg)
    r = g.submit_request(
        {
            "task": "Purchase OpenAI credits",
            "recipient": "OpenAI",
            "amount": 5,
            "currency": "USD",
            "reason": "Continue work",
            "estimated_tokens": 5000,
        }
    )
    return r


def beat_hallucinate(g, cfg: dict) -> dict:
    """v2: the big model publishes a context.md that LIES about the tool log.
    The reasoning layer catches the contradiction and pauses the session."""
    g.log.append("agent_action", step="Checkpoint", detail="Agent publishes context.md after its thinking phase.")
    _sleep(cfg)
    r1 = g.reasoning.record(
        "demo",
        "## Goal\nBuild a working authentication feature\n\n## Plan\n1. Scaffold the auth module\n2. Write the login endpoint",
        tool_trail=[{"tool": "edit", "input": {"filePath": "auth.py"}, "status": "completed", "output": "created auth.py"}],
    )
    _sleep(cfg)
    g.log.append("agent_action", step="Run tests", detail="Agent runs the test suite…")
    _sleep(cfg)
    g.log.append("reasoning_tool", tool="bash", status="error", output="FAILED: 3 tests failed")
    r2 = g.reasoning.record(
        "demo",
        "## Goal\nBuild a working authentication feature\n\n## Plan\nRun the tests — all tests pass now\nShip it",
        tool_trail=[{"tool": "bash", "input": {"command": "pytest"}, "status": "error", "output": "FAILED: 3 tests failed"}],
    )
    _sleep(cfg)
    g.log.append(
        "reasoning_verdict",
        session_id="demo",
        state=r2["state"],
        contradiction=bool(r2["signals"].get("contradiction")),
        reason=r2["reason"],
    )
    # v3: even if the paused agent now tries to spend (bypassing the plugin and
    # hitting the API / wallet directly), the session is frozen — the money
    # does not move. The wallet refuses on its own.
    _sleep(cfg)
    attempt = g.submit_request(
        {
            "task": "Purchase OpenAI credits",
            "recipient": "OpenAI",
            "amount": 5,
            "currency": "USD",
            "reason": "Continue the (hallucinated) work",
            "estimated_tokens": 8000,
        },
        session_id="demo",
    )
    g.log.append("agent_action", step="Bypass attempt", detail="Paused agent tries to spend anyway — wallet refuses.")
    return {**r2, "paused_spend_attempt": attempt}


def beat_recover(g, cfg: dict) -> dict:
    """v2: the owner reviews the trail, tops up the wallet and resumes the
    paused session. The agent then makes a legitimate purchase."""
    g.log.append("agent_action", step="Owner", detail="Owner reviews the reasoning trail, tops up $10 and resumes.")
    _sleep(cfg)
    g.reasoning.resume("demo")
    # An owner re-authorization also re-arms a workflow that an earlier demo
    # beat (the v1 retry loop) had terminated. Only the owner can do this:
    # clears the termination and the retry debt.
    if g.memory.status != "running":
        g.memory.status = "running"
        g.memory.terminate_reason = ""
        g.memory.retries = 0
        g.loop.reset()
        g.log.append("system", event="workflow_resumed_by_owner")
    r = g.submit_request(
        {
            "task": "Purchase OpenAI credits",
            "recipient": "OpenAI",
            "amount": 5,
            "currency": "USD",
            "reason": "Resumed after owner review — credits needed to finish",
            "estimated_tokens": 8000,
        },
        session_id="demo",
    )
    return r


def run_beat(g, cfg: dict, name: str) -> dict:
    from . import beats

    fn = getattr(beats, f"beat_{name}", None)
    if fn is None:
        raise ValueError(f"unknown beat '{name}'")
    return fn(g, cfg)
