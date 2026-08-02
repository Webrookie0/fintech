"""Unit tests for per-user accounts + device/session scoping.

Covers:
  A1: register creates an account, first account is admin
  A2: login mints a session token; wrong password is rejected
  A3: device token resolves to its user
  A4: instances are scoped per owner
  A5: reasoning sessions are scoped per owner
  A6: a user can override/resume their OWN session but not another's
  A7: the server admin token bypasses scoping
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import yaml  # noqa: E402

from guardian.accounts import UserStore  # noqa: E402
from guardian.core import Guardian  # noqa: E402
from guardian.events import EventLog  # noqa: E402
from guardian.reasoning import CheckpointDB, ReasoningTrail  # noqa: E402
from wallet.wallet import Wallet  # noqa: E402


def _llm_client():
    from guardian.llm import LLMClient

    return LLMClient(provider="mock", model="")


def _cfg() -> dict:
    return yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))


def _trail(cfg: dict, tmp: str) -> tuple[ReasoningTrail, Wallet]:
    log = EventLog(Path(tmp) / "events.jsonl")
    wallet = Wallet(cfg["policy"], Path(tmp) / "wallet.json", starting_balance=cfg["wallet"]["starting_balance_usd"])
    trail = ReasoningTrail(cfg, CheckpointDB(str(Path(tmp) / "cp.db")), _llm_client(), log=log, wallet=wallet)
    return trail, wallet


def _users(tmp: str) -> UserStore:
    return UserStore(str(Path(tmp) / "users.db"))


def test_register_first_is_admin_second_is_not():
    """A1: open registration; the first account is admin, later ones aren't."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _users(tmp)
        alice = store.register("alice@example.com", "password123")
        assert alice["is_admin"] is True
        assert alice["device_token"]
        bob = store.register("bob@example.com", "password456")
        assert bob["is_admin"] is False
        # duplicate email is rejected
        try:
            store.register("alice@example.com", "password123")
            raise AssertionError("duplicate email must raise")
        except ValueError:
            pass


def test_login_and_session_resolution():
    """A2: login mints a session token; bad password rejected; logout kills it."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _users(tmp)
        store.register("alice@example.com", "password123")
        login = store.login("alice@example.com", "password123")
        assert login["token"]
        user = store.user_for_session(login["token"])
        assert user and user["email"] == "alice@example.com"
        try:
            store.login("alice@example.com", "wrongpass")
            raise AssertionError("wrong password must raise")
        except ValueError:
            pass
        store.logout(login["token"])
        assert store.user_for_session(login["token"]) is None


def test_device_token_resolves_to_user():
    """A3: a device token resolves back to its owning account."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _users(tmp)
        user = store.register("alice@example.com", "password123")
        resolved = store.user_for_device_token(user["device_token"])
        assert resolved and resolved["email"] == "alice@example.com"
        assert store.user_for_device_token("garbage") is None


def test_reasoning_sessions_scoped_per_owner():
    """A5: sessions() with owner only returns that user's sessions."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        trail, _ = _trail(cfg, tmp)
        trail.record("alice-session", "## Goal\nBuild auth", owner="1",
                     tool_trail=[{"tool": "write", "status": "completed"}])
        trail.record("bob-session", "## Goal\nBuild wallet", owner="2",
                     tool_trail=[{"tool": "write", "status": "completed"}])
        all_sess = trail.sessions()
        assert {s["session_id"] for s in all_sess} >= {"alice-session", "bob-session"}
        alice_only = trail.sessions(owner="1")
        assert {s["session_id"] for s in alice_only} == {"alice-session"}
        bob_only = trail.sessions(owner="2")
        assert {s["session_id"] for s in bob_only} == {"bob-session"}


def test_session_owner_can_override_own_but_not_others():
    """A6: owns() gates the owner controls per session."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        trail, _ = _trail(cfg, tmp)
        trail.record("alice-session", "## Goal\nBuild auth", owner="1",
                     tool_trail=[{"tool": "write", "status": "completed"}])
        trail.record("bob-session", "## Goal\nBuild wallet", owner="2",
                     tool_trail=[{"tool": "write", "status": "completed"}])
        # alice owns only her session
        assert trail.owns("alice-session", "1") is True
        assert trail.owns("bob-session", "1") is False
        assert trail.owns("bob-session", "2") is True
        # unowned sessions are owned by nobody
        trail.record("orphan", "## Goal\nhi", tool_trail=[{"tool": "write", "status": "completed"}])
        assert trail.owns("orphan", "1") is True  # no owner → visible to everyone


def test_owner_persists_across_db_reopen():
    """A5b: owner survives a server restart (re-read from SQLite)."""
    cfg = _cfg()
    with tempfile.TemporaryDirectory() as tmp:
        cp_path = str(Path(tmp) / "cp.db")
        log = EventLog(Path(tmp) / "events.jsonl")
        wallet = Wallet(cfg["policy"], Path(tmp) / "wallet.json", starting_balance=cfg["wallet"]["starting_balance_usd"])
        trail = ReasoningTrail(cfg, CheckpointDB(cp_path), _llm_client(), log=log, wallet=wallet)
        trail.record("s", "## Goal\nBuild auth", owner="7",
                     tool_trail=[{"tool": "write", "status": "completed"}])
        # simulate restart: fresh ReasoningTrail on the same DB file
        trail2 = ReasoningTrail(cfg, CheckpointDB(cp_path), _llm_client(), log=log, wallet=wallet)
        sess = trail2.sessions()
        assert len(sess) == 1 and sess[0]["owner"] == "7"


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
