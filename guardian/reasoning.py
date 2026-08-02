"""Reasoning supervision — checkpoint trail, embeddings, and objective signals.

v2 of Guardian. The big model publishes its thinking to a markdown checkpoint
(context.md) after every phase; this module stores each version with an
embedding, then runs five objective signals against the REAL tool trail:

  contradiction     - checkpoint claims something the tool log contradicts
  stall             - consecutive checkpoints are near-identical (no progress)
  churn             - the plan section is rewritten wholesale each checkpoint
  drift             - the goal statement drifts from the original task
  repeat-strategy   - the same failing tool+args reappears after an error

A small judge LLM confirms ambiguous contradictions; the deterministic
signals always run. This is the "is the big model hallucinating / stuck"
answer that token count alone cannot give. The verdict can pause or terminate
a session, but only the owner can top up the wallet and resume.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

from .llm import LLMClient, parse_json

SIMILAR = "similar"
REASONING_PROMPT = """You are the Reasoning Judge in "Guardian", a policy-enforced control plane.
A big model is working on a task and publishing its thinking as a checkpoint file.
Your ONLY job: review the objective signal report and confirm or correct its verdict.
You are ADVISORY about the verdict, but you must anchor every statement in evidence.
A separate deterministic signal engine already ran; you are confirming edge cases.

Signal report:
{report}

Task:
{task}

Respond with ONLY JSON:
{{"confirms": "paused"|"running"|"terminated", "confidence": 0.0-1.0, "reason": "one sentence", "evidence": "what concretely supports this"}}
"""

CLAIM_WORDS = (
    "tests pass", "all pass", "passing", "success", "succeeded", "successful",
    "fixed", "resolved", "verified", "ready to", "deployed", "connected",
    "green", "is working", "works now", "working correctly",
)
FAIL_WORDS = (
    "failed", "failing", "error", "traceback", "fatal", "exception",
    "exit code", "command not found", "assertionerror", "refused",
)

_HEADING = re.compile(r"^\s{0,3}(#{1,6}|[-=])\s*(.*)$", re.MULTILINE)


def _text_sim(a: str, b: str) -> float:
    """Deterministic text similarity (0..1) via diff ratios — no embeddings."""
    from difflib import SequenceMatcher

    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip(), b.strip()).ratio()


def _today_ts() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Embeddings — real API when available, deterministic n-gram fallback offline #
# --------------------------------------------------------------------------- #
def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class Embedder:
    """Gemini embeddings when a key exists; a hashed char-ngram vector otherwise.

    Both return dense vectors of `dim` floats, so the pipeline (and its
    thresholds) behave the same online and offline.
    """

    def __init__(self, client: LLMClient, dim: int = 384, buckets: int = 2048, n: int = 3):
        self.client = client
        self.dim = dim
        self.buckets = buckets
        self.n = n

    def embed(self, text: str) -> list[float]:
        text = (text or "").strip()
        if not text:
            return [0.0] * self.dim
        if self.client.provider == "gemini":
            try:
                vec = self._gemini(text)
                if vec:
                    return vec
            except Exception:
                pass
        return self._ngram(text)

    def _gemini(self, text: str) -> list[float] | None:
        from google import genai

        client = genai.Client(api_key=__import__("os").environ["GEMINI_API_KEY"])
        resp = client.models.embed_content(model="gemini-embedding-001", contents=text)
        values = resp.embeddings[0].values
        if values:
            return [float(v) for v in values][: self.dim]

    def _ngram(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        norm = text.lower()
        for i in range(0, max(1, len(norm) - self.n + 1)):
            gram = norm[i : i + self.n]
            h = int(hashlib.sha1(gram.encode("utf-8")).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 40) & 1 else -1.0
            vec[idx] += sign
        mag = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / mag for v in vec]


# --------------------------------------------------------------------------- #
# Checkpoint store — SQLite. Append-only per session.                         #
# --------------------------------------------------------------------------- #
class CheckpointDB:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A connection that ALWAYS closes. sqlite3's `with conn` only commits
        or rolls back; it does not close the underlying file descriptor, so a
        naive `with self._conn() as conn:` leaks one FD per call."""
        conn = self._conn()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    ts TEXT NOT NULL,
                    digest TEXT NOT NULL,
                    context_md TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    todos TEXT NOT NULL,
                    tool_trail TEXT NOT NULL,
                    signals TEXT NOT NULL DEFAULT '{}',
                    verdict TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cp_session ON checkpoints (session_id, id)"
            )
            # Migration for DBs created before the owner column existed.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(checkpoints)")}
            if "owner" not in cols:
                conn.execute("ALTER TABLE checkpoints ADD COLUMN owner TEXT NOT NULL DEFAULT ''")

    def add(self, session_id: str, context_md: str, embedding: list[float], todos, tool_trail, owner: str = "") -> int:
        rec = (
            session_id,
            owner or "",
            _today_ts(),
            _digest(context_md),
            context_md,
            json.dumps(embedding),
            json.dumps(todos or [], default=str),
            json.dumps(tool_trail or [], default=str),
        )
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO checkpoints (session_id, owner, ts, digest, context_md, embedding, todos, tool_trail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rec,
            )
            return int(cur.lastrowid)

    def update_verdict(self, cpid: int, signals: dict, verdict: dict) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE checkpoints SET signals = ?, verdict = ? WHERE id = ?",
                (json.dumps(signals, default=str), json.dumps(verdict, default=str), cpid),
            )

    def recent(self, session_id: str, n: int = 40) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, n),
            ).fetchall()
        return [dict(r) for r in rows][::-1]

    def all_sessions(self, n: int = 50) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, MAX(id) as last_id, COUNT(*) as n, "
                "MAX(owner) as owner FROM checkpoints "
                "GROUP BY session_id ORDER BY last_id DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]

    def reset(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM checkpoints")


# --------------------------------------------------------------------------- #
# Signal extraction — pure functions, unit-testable, no network.              #
# --------------------------------------------------------------------------- #
def extract_claims(text: str) -> list[str]:
    """Lines that assert a success/failure state about the work."""
    claims = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "```")):
            continue
        low = line.lower()
        if any(w in low for w in CLAIM_WORDS):
            claims.append(line[:220])
    return claims
def _failed(entry: dict) -> tuple[bool, str]:
    status = str(entry.get("status", "completed")).lower()
    output = str(entry.get("output") or entry.get("error") or "")
    if status in ("error", "failed"):
        return True, output
    if any(w in output.lower() for w in FAIL_WORDS):
        return True, output
    return False, output


def _fingerprint(entry: dict) -> str:
    tool = (entry.get("tool") or "").strip().lower()
    args = entry.get("input") or entry.get("args") or entry.get("command") or ""
    raw = f"{tool}|{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def detect_signals(context_md: str, prev_context: str | None, prev_prev_context: str | None,
                   prev_embedding, embedding,
                   tool_trail: list[dict], history: list[dict] | None,
                   goal: str, task: str, anchor_goal: str = "",
                   embedder: Embedder = None,
                   stall_streak: int = 0, min_identical: int = 4) -> dict:
    signals: dict = {"contradiction": [], "stall": False, "churn": False,
                     "drift": False, "repeat_strategy": []}
    claims = extract_claims(context_md)

    # --- contradiction: success claim vs real failure since the last checkpoint
    failures = []
    for entry in tool_trail or []:
        is_fail, output = _failed(entry)
        if is_fail:
            failures.append({"tool": entry.get("tool"), "output": output[:160]})
    if failures and claims:
        signals["contradiction"] = [
            {"claim": c, "tool": f["tool"], "output": f["output"]}
            for c in claims[:4] for f in failures[:2]
        ][:4]

    # --- stall: near-identical consecutive checkpoints -----------------------
    # A single similar pair is NOT a stall. The session must repeat the same
    # checkpoint `min_identical` times in a row (configurable, default 4)
    # before the stall signal fires. The streak is counted in record().
    if prev_embedding is not None and embedding is not None:
        sim = cosine(prev_embedding, embedding)
        signals["stall"] = stall_streak >= max(1, int(min_identical) - 1)
        signals["_similarity"] = round(sim, 4)

    # --- churn: the plan is rewritten wholesale EVERY checkpoint --------------
    # Deterministic diff ratio (not embeddings): short plan sections make
    # vectors unreliable, and a wholesale rewrite is a text-similarity fact.
    plan = _section(context_md, "plan")
    prev_plan = _section(prev_context or "", "plan")
    prev2_plan = _section(prev_prev_context or "", "plan")
    if plan and prev_plan:
        sim_now = _text_sim(plan, prev_plan)
        changed_from_prev = sim_now < 0.45
        # Churn = plan changed vs the previous checkpoint AND vs the one before
        # that (two full rewrites in a row). A single progression is progress.
        rewritten_twice = True
        if prev2_plan:
            rewritten_twice = _text_sim(prev_plan, prev2_plan) < 0.45
        signals["churn"] = bool(changed_from_prev and rewritten_twice)
        signals["_plan_similarity"] = round(sim_now, 4)

    # --- drift: the goal statement has left the session's anchored task -----
    goal_text = _section(context_md, "goal")
    if anchor_goal and goal_text:
        # Compare against the session's own first-stated goal, not the config
        # goal. Keeping the goal text identical across checkpoints is FINE; it
        # must only stay close to where the session itself said it was going.
        sim_task = cosine(embedder.embed(goal_text), embedder.embed(anchor_goal))
        signals["drift"] = bool(sim_task < 0.5)
        signals["_goal_similarity"] = round(sim_task, 4)
    elif not anchor_goal and goal_text:
        # No baseline yet (first real goal published) — cannot be "off anchor".
        signals["drift"] = False

    # --- repeat-strategy: same failing approach retried -----------------------
    # Uses the full session history (a failure from an earlier checkpoint that
    # was then retried) — not just the current window.
    seen_failed: dict[str, str] = {}
    for entry in (history or []):
        fp = _fingerprint(entry)
        is_fail, _ = _failed(entry)
        if is_fail:
            seen_failed[fp] = str(entry.get("tool"))
        elif fp in seen_failed:
            signals["repeat_strategy"].append(
                {"tool": entry.get("tool"), "earlier_failure": seen_failed[fp]}
            )

    # P3: pure repetition — the same tool+args fingerprint appearing >=3 times
    # in session history, regardless of success/failure. Catches "same edit /
    # rebuild over and over" even when the agent keeps context.md fresh.
    from collections import Counter
    fp_counts = Counter(_fingerprint(e) for e in (history or []))
    for entry in (history or []):
        fp = _fingerprint(entry)
        if fp_counts[fp] >= 3 and not any(r.get("tool") == entry.get("tool") for r in signals["repeat_strategy"]):
            signals["repeat_strategy"].append(
                {"tool": entry.get("tool"), "count": fp_counts[fp]}
            )

    signals["repeat_strategy"] = signals["repeat_strategy"][:4]

    for k in ("_similarity", "_plan_similarity", "_goal_similarity"):
        signals.pop(k, None)
    return signals


def _section(text: str, name: str) -> str:
    """Return the body of the first heading whose title contains `name`."""
    if not text:
        return ""
    lines = text.splitlines()
    capture: list[str] = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m:
            title = m.group(2).strip().lower()
            if name in title:
                for nxt in lines[i + 1 :]:
                    if _HEADING.match(nxt):
                        return "\n".join(capture).strip()
                    capture.append(nxt)
                return "\n".join(capture).strip()
    return ""


# --------------------------------------------------------------------------- #
# Reasoning judge — deterministic signals, LLM confirmation on ambiguity.     #
# --------------------------------------------------------------------------- #
class ReasoningJudge:
    def __init__(self, client: LLMClient, goal: str = "", policy: dict | None = None):
        self.client = client
        self.goal = goal
        self.policy = policy or {}

    def confirm(self, signals: dict, context_md: str, task: str) -> dict | None:
        """Ask the small model to confirm only ambiguous contradictions."""
        if not self.client.available or not signals.get("contradiction"):
            return None
        prompt = REASONING_PROMPT.format(
            report=json.dumps(signals, indent=2)[:2500],
            task=(task or self.goal or "the task")[:400],
        )
        try:
            data = parse_json(self.client.complete(prompt)) or {}
        except Exception:
            return None
        return {
            "recommendation": data.get("confirms", "paused"),
            "confidence": float(data.get("confidence", 0.5)),
            "reason": str(data.get("reason", "")),
            "evidence": str(data.get("evidence", "")),
        }


# --------------------------------------------------------------------------- #
# ReasoningTrail — orchestration + per-session state machine.                 #
# --------------------------------------------------------------------------- #
VALID_MODES = ("enforce", "watch", "ask")


class ReasoningTrail:
    """Records checkpoints and produces a verdict: running | paused | terminated.

    The verdict is what the opencode plugin enforces: a paused/terminated
    session gets its tools blocked and its wallet access revoked. Only the
    owner (top-up / resume / terminate / override endpoints) changes the state.

    Modes (GUARDIAN_MODE env or config, changable at runtime):
      enforce — paused/terminated sessions block tools (strict)
      watch   — never block; the gate always allows, status is advisory only
      ask     — block tools on a paused session, but the owner can temporarily
                override the gate for N minutes via POST /api/reasoning/override
    """

    def __init__(self, cfg: dict, db: CheckpointDB, client: LLMClient, log=None, wallet=None,
                 disabled: bool = False, mode: str | None = None):
        self.cfg = cfg
        self.db = db
        self.log = log
        self.wallet = wallet
        self.goal = cfg.get("agent", {}).get("goal", "")
        self.policy = cfg.get("policy", {})
        self.disabled = disabled
        self.embedder = Embedder(client)
        self.judge = ReasoningJudge(client, goal=self.goal, policy=self.policy)
        self.mode = (mode or cfg.get("supervision", {}).get("mode") or "enforce").strip().lower()
        if self.mode not in VALID_MODES:
            self.mode = "enforce"
        self._sessions: dict[str, dict] = {}
        self._lock = threading.RLock()

    # --- session state --------------------------------------------------------
    def _state(self, session_id: str) -> dict:
        with self._lock:
            return self._sessions.setdefault(
        session_id,
        {"status": "running", "reason": "", "paused_count": 0, "violations": 0,
         "warnings": 0, "trail": [], "anchor_goal": "", "stall_streak": 0,
         "override_until": 0.0, "owner": ""},
            )

    def owner(self, session_id: str) -> str:
        """The user (id) that owns this session's checkpoints, or ''."""
        with self._lock:
            st = self._sessions.get(session_id)
            return st.get("owner", "") if st else ""

    def owns(self, session_id: str, user_id: int | str | None) -> bool:
        """True if `user_id` owns this session (or the session has no owner)."""
        owner = self.owner(session_id)
        if not owner or not user_id:
            return not owner
        return owner == str(user_id)

    def status(self, session_id: str) -> dict:
        st = self._state(session_id)
        return {
            "session_id": session_id,
            "status": st["status"],
            "reason": st["reason"],
            "paused_count": st["paused_count"],
            "violations": st["violations"],
            "warnings": st["warnings"],
            "mode": self.mode,
        }

    # --- the money-path gate: plugin asks before every tool -------------------
    def gate(self, session_id: str) -> dict:
        """Fast, synchronous, no LLM — what the plugin calls in tool.execute.before.

        Returns {status, reason, mode, override, allowed}. `override` is true
        while the owner's allow-for-N-minutes is still in effect, which forces
        the gate open regardless of the session verdict.
        """
        import time as _time

        st = self._state(session_id)
        now = _time.time()
        until = st.get("override_until", 0.0)
        # override_until = float("inf") means "until the owner closes it" —
        # the gate stays open until clear_override()/resume()/reset().
        override_active = bool(until) and (until == float("inf") or now < until)
        if self.disabled:
            return {
                "session_id": session_id,
                "allowed": True,
                "status": "running",
                "reason": "reasoning supervision disabled (maintenance mode)",
                "mode": self.mode,
                "override": False,
            }
        # watch mode is advisory only — never block, even if the session is
        # paused/terminated. The dashboard still shows the true verdict via
        # status()/sessions(); the gate reports "running" so even a plugin that
        # only reads status (not `allowed`) can never be blocked in watch mode.
        if self.mode == "watch":
            return {
                "session_id": session_id,
                "allowed": True,
                "status": "running",
                "reason": st["reason"] or f"watch mode — session {st['status']} (not blocking)",
                "mode": self.mode,
                "override": False,
            }
        allowed = st["status"] == "running" or override_active
        return {
            "session_id": session_id,
            "allowed": allowed,
            "status": st["status"],
            "reason": st["reason"],
            "mode": self.mode,
            "override": override_active,
        }

    # --- owner-in-the-loop: temporary allow -----------------------------------
    def override(self, session_id: str, minutes: float = 5.0, until_closed: bool = False) -> dict:
        """Open the gate for this session. Does NOT change the verdict — status
        stays what it was — but the gate allows tools while the override is
        active. In ask mode this is the intended way to unblock; in enforce mode
        the session is still paused on the dashboard until the owner resumes.

        Two shapes:
          minutes > 0       → time-boxed window (default 5).
          until_closed=True → stays open until the owner closes it via
                              clear_override()/resume()/reset(). No expiry."""
        import time as _time

        st = self._state(session_id)
        with self._lock:
            if until_closed:
                st["override_until"] = float("inf")
            else:
                minutes = max(0.5, min(float(minutes or 5.0), 120.0))
                st["override_until"] = _time.time() + minutes * 60.0
        if self.log:
            self.log.append("system", event="reasoning_override", session_id=session_id,
                            minutes=minutes if not until_closed else 0,
                            until_closed=until_closed, status=st["status"])
        return {
            "session_id": session_id,
            "mode": self.mode,
            "override": True,
            "override_minutes": minutes if not until_closed else None,
            "until_closed": until_closed,
            "status": st["status"],
            "reason": st["reason"],
        }

    def clear_override(self, session_id: str) -> dict:
        with self._lock:
            st = self._state(session_id)
            st["override_until"] = 0.0
        return self.gate(session_id)

    def set_mode(self, mode: str) -> dict:
        mode = (mode or "").strip().lower()
        if mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {', '.join(VALID_MODES)}")
        with self._lock:
            self.mode = mode
        if self.log:
            self.log.append("system", event="reasoning_mode", mode=mode)
        return {"mode": self.mode}

    # --- record a checkpoint + decide -----------------------------------------
    def record(self, session_id: str, context_md: str, todos=None, tool_trail=None, owner: str = "") -> dict:
        session_id = session_id or "default"
        todos = todos or []
        tool_trail = tool_trail or []
        st = self._state(session_id)
        if owner:
            with self._lock:
                st["owner"] = str(owner)

        with self._lock:
            st["trail"] = (st["trail"] + tool_trail)[-120:]

        recent = self.db.recent(session_id, 2)
        prev = recent[-1] if recent else None
        prev_prev = recent[-2] if len(recent) > 1 else None
        prev_embedding = json.loads(prev["embedding"]) if prev else None
        prev_context = prev["context_md"] if prev else None
        prev_prev_context = prev_prev["context_md"] if prev_prev else None

        embedding = self.embedder.embed(context_md)
        cpid = self.db.add(session_id, context_md, embedding, todos, tool_trail, owner=st.get("owner", ""))

        # P5: consecutive-identical streak for the stall signal. The streak is
        # incremented whenever a checkpoint is near-identical to the previous
        # one and reset on any change — so the stall signal fires only after
        # `min_identical` identical checkpoints in a row, not on the 2nd.
        min_identical = int(self.policy.get("min_identical_checkpoints_for_stall", 4))
        sim = cosine(prev_embedding, embedding) if prev_embedding is not None else 0.0
        with self._lock:
            st["stall_streak"] = st["stall_streak"] + 1 if sim >= 0.985 else 0
            stall_streak = st["stall_streak"]

        # P2: session-anchored goal. The FIRST real goal the session publishes
        # becomes the anchor; drift is measured against that anchor, NOT against
        # the static config goal (which may legitimately differ from the actual
        # task and caused a false pause on an empty first checkpoint).
        goal_text = _section(context_md, "goal")
        with self._lock:
            if not st["anchor_goal"] and goal_text:
                st["anchor_goal"] = goal_text

        signals = detect_signals(
            context_md=context_md,
            prev_context=prev_context,
            prev_prev_context=prev_prev_context,
            prev_embedding=prev_embedding,
            embedding=embedding,
            tool_trail=tool_trail,
            history=st["trail"],
            goal=self.goal,
            task=self.goal,
            anchor_goal=st["anchor_goal"],
            embedder=self.embedder,
            stall_streak=stall_streak,
            min_identical=min_identical,
        )

        # Judge-confirmed pauses: a contradiction signal is ambiguous on its
        # own (prose in context.md vs a failed tool). Before it can escalate to
        # a pause, the small judge LLM must confirm the verdict. Deterministic
        # signals still warn, but only a confirmed contradiction pauses.
        judge = None
        if signals.get("contradiction"):
            judge = self.judge.confirm(signals, context_md, task=self.goal)
            if judge:
                confirmed = judge.get("recommendation") == "paused" and float(judge.get("confidence", 0) or 0) >= 0.5
                if not confirmed:
                    # Judge says the contradiction is a false positive — drop it.
                    signals["contradiction"] = []
                    if self.log:
                        self.log.append(
                            "reasoning_judge",
                            session_id=session_id,
                            recommendation=judge.get("recommendation"),
                            confidence=judge.get("confidence"),
                            reason=judge.get("reason"),
                            outcome="contradiction overruled — no pause",
                        )
                else:
                    if self.log:
                        self.log.append(
                            "reasoning_judge",
                            session_id=session_id,
                            recommendation=judge.get("recommendation"),
                            confidence=judge.get("confidence"),
                            reason=judge.get("reason"),
                            outcome="contradiction confirmed",
                        )
        verdict = self._decide(session_id, signals, judge=judge)

        # P4: attach a measurable "waste receipt" whenever the session is
        # paused/terminated, so the owner has concrete evidence (not just a
        # flag) of what the pause is protecting against.
        if verdict.get("state") in ("paused", "terminated"):
            receipt = self._waste_receipt(session_id, signals, tool_trail)
            verdict = {**verdict, "waste": receipt}
        self.db.update_verdict(cpid, signals, verdict)

        if self.log:
            # Log the individual tool steps the agent actually took since the
            # last checkpoint, so the audit trail shows the real actions
            # (e.g. the hallucinated edits), not just the verdict.
            for step in tool_trail:
                self.log.append(
                    "reasoning_tool",
                    session_id=session_id,
                    checkpoint_id=cpid,
                    tool=step.get("tool", ""),
                    status="error" if step.get("status") in ("error", "failed") else "ok",
                    output=str(step.get("output") or "")[:200],
                    detail=str(step.get("input") or "")[:200],
                )
            self.log.append(
                "reasoning_checkpoint",
                session_id=session_id,
                checkpoint_id=cpid,
                **{k: v for k, v in signals.items() if not k.startswith("_")},
                **{"verdict_state": verdict.get("state"), "reason": verdict.get("reason")},
            )
        return {**verdict, "checkpoint_id": cpid, "signals": signals}

    def _waste_receipt(self, session_id: str, signals: dict, tool_trail: list[dict]) -> dict:
        """Evidence packet for a pause: how long, how many tokens, how many
        repeats, and the concrete evidence line behind each fired signal."""
        import time as _time

        now = _time.time()
        try:
            last_ts = self.db.recent(session_id, 1)[-1].get("ts") if self.db.recent(session_id, 1) else None
            first_ts = self.db.recent(session_id, 20)[0].get("ts") if self.db.recent(session_id, 20) else None
        except Exception:
            last_ts = first_ts = None

        def _secs(iso: str | None) -> int:
            if not iso:
                return 0
            try:
                dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                return max(0, int(now - dt.timestamp()))
            except Exception:
                return 0

        elapsed = _secs(first_ts)
        tokens = int(sum(len(str(s.get("output") or "")) / 4 for s in (tool_trail or [])))

        repeats = 0
        evidence = []
        if signals.get("repeat_strategy"):
            for r in signals["repeat_strategy"][:3]:
                if r.get("count"):
                    repeats = max(repeats, int(r.get("count", 0)))
                    evidence.append(f"same '{r.get('tool')}' repeated {r.get('count')}x")
                elif r.get("earlier_failure"):
                    evidence.append(f"'{r.get('tool')}' retried after earlier failure ({r.get('earlier_failure')})")
        if signals.get("contradiction"):
            for c in signals["contradiction"][:2]:
                evidence.append(f"claim '{c.get('claim', '')[:60]}' vs tool {c.get('tool')} failure")
        if signals.get("stall"):
            evidence.append(f"checkpoint {signals.get('_similarity') or '?'} similar to previous — no progress")
        if signals.get("drift"):
            evidence.append(f"goal similarity to anchor {signals.get('_goal_similarity') or '?'} — off task")
        if signals.get("churn"):
            evidence.append("plan rewritten wholesale across checkpoints")

        return {
            "elapsed_s": elapsed,
            "tokens_burned_est": tokens,
            "repeats": repeats,
            "evidence": evidence[:6],
            "signals": {k: v for k, v in signals.items() if not k.startswith("_")},
        }

    def _freeze_wallet(self, session_id: str) -> None:
        if self.wallet is not None:
            try:
                self.wallet.freeze_session(session_id)
            except Exception:
                pass

    def _decide(self, session_id: str, signals: dict, judge: dict | None = None) -> dict:
        st = self._state(session_id)
        if self.disabled:
            return {"state": "running", "reason": "reasoning supervision disabled (maintenance mode)", "signals": signals}
        if st["status"] == "terminated":
            return {"state": "terminated", "reason": st["reason"], "signals": signals}

        # STICKY PAUSE: once a session is paused, a passing checkpoint cannot
        # silently flip it back to running. Only the owner (resume/top-up) does.
        # The agent's own context.md cannot un-pause itself.
        if st["status"] == "paused":
            return {
                "state": "paused",
                "reason": "awaiting owner review — checkpoint accepted, session remains paused",
                "signals": signals,
            }

        strong = []
        if signals.get("contradiction"):
            strong.append("contradiction: checkpoint claims contradict the tool log")
        if signals.get("repeat_strategy"):
            strong.append("repeat-strategy: same failing approach retried")
        if signals.get("drift"):
            strong.append("drift: goal statement has left the original task")
        if signals.get("churn"):
            strong.append("churn: plan rewritten wholesale")

        triggered = bool(strong) or signals.get("stall")

        if not triggered:
            with self._lock:
                st["warnings"] = 0
                st["paused_count"] = 0
            return {"state": "running", "reason": "on track", "signals": signals}

        # ESCALATION LADDER: the first time a signal fires it is a WARNING, not
        # a pause. The session keeps running and can still self-correct. Only a
        # REPEATED signal on a later checkpoint escalates to a pause — a single
        # flaky checkpoint must never block the agent's tools.
        if st["warnings"] < 1:
            with self._lock:
                st["warnings"] += 1
                st["violations"] += 1
            reason = "; ".join(strong) if strong else "no progress (stall)"
            if self.log:
                self.log.append(
                    "system", event="reasoning_warning", session_id=session_id,
                    reason=reason, warnings=st["warnings"],
                )
            return {
                "state": "running",
                "reason": f"WARNING ({st['warnings']}/2): {reason} — will pause if repeated",
                "signals": signals,
            }

        max_pauses = int(self.policy.get("max_pauses_before_terminate", 3))
        with self._lock:
            st["paused_count"] += 1
            st["violations"] += 1
            st["status"] = "paused"
            st["reason"] = "; ".join(strong) if strong else "no progress (stall)"
            if st["paused_count"] >= max_pauses:
                st["status"] = "terminated"
                st["reason"] = (
                    f"persisted across {st['paused_count']} checkpoints: "
                    f"{st['reason']}"
                )
                self._freeze_wallet(session_id)
                if self.log:
                    self.log.append(
                        "system", event="reasoning_terminated", session_id=session_id,
                        reason=st["reason"], spend_saved=self.cfg.get("wallet", {}).get("starting_balance_usd"),
                    )
                return {"state": "terminated", "reason": st["reason"], "signals": signals}
            self._freeze_wallet(session_id)

        if self.log:
            self.log.append(
                "system", event="reasoning_paused", session_id=session_id,
                reason=st["reason"],
            )
        return {"state": "paused", "reason": st["reason"], "signals": signals}

    # --- owner controls --------------------------------------------------------
    def resume(self, session_id: str) -> dict:
        with self._lock:
            st = self._sessions.get(session_id)
            if st is None:
                return {"status": "running", "reason": "no session recorded"}
            if st["status"] == "terminated":
                return {"status": "terminated", "reason": "terminated sessions are final — reset to restart"}
            st["status"] = "running"
            st["reason"] = "resumed by owner"
            st["paused_count"] = 0
            st["stall_streak"] = 0
            st["override_until"] = 0.0
        if self.wallet is not None:
            try:
                self.wallet.unfreeze_session(session_id)
            except Exception:
                pass
        if self.log:
            self.log.append("system", event="reasoning_resumed", session_id=session_id)
        return {"status": "running", "reason": "resumed by owner"}

    def terminate(self, session_id: str, reason: str = "terminated by owner") -> dict:
        with self._lock:
            st = self._sessions.setdefault(
                session_id, {"status": "running", "reason": "", "paused_count": 0, "violations": 0, "stall_streak": 0}
            )
            st["status"] = "terminated"
            st["reason"] = reason
        self._freeze_wallet(session_id)
        if self.log:
            self.log.append("system", event="reasoning_terminated", session_id=session_id, reason=reason)
        return {"status": "terminated", "reason": reason}

    def trail(self, session_id: str, n: int = 40) -> dict:
        return {
            "session_id": session_id,
            "status": self.status(session_id),
            "goal": self.goal,
            "checkpoints": self.db.recent(session_id, n),
        }

    def sessions(self, owner: str | None = None) -> list[dict]:
        import time as _time

        sessions = self.db.all_sessions()
        now = _time.time()
        out = []
        for s in sessions:
            st = self._state(s["session_id"])
            # Owner persists in the DB across restarts; backfill the in-memory
            # state so subsequent checkpoints keep the same attribution.
            if s.get("owner") and not st.get("owner"):
                with self._lock:
                    st["owner"] = s["owner"]
            sess_owner = st.get("owner", "")
            # owner filter: only that user's sessions (unowned sessions are
            # visible to everyone until a device token claims them).
            if owner and sess_owner and sess_owner != str(owner):
                continue
            s["owner"] = sess_owner
            s["status"] = st["status"]
            s["reason"] = st["reason"]
            s["mode"] = self.mode
            until = st.get("override_until", 0.0)
            active = bool(until) and (until == float("inf") or now < until)
            s["override"] = active
            s["override_until_closed"] = bool(until == float("inf") and active)
            out.append(s)
        return out

    def reset(self) -> None:
        with self._lock:
            self._sessions.clear()
            self.db.reset()
