"""Guardian — FastAPI app.

Serves the dashboard (plain static files, waku-style) AND the Guardian APIs on
one port. Host-agnostic: reads PORT and binds 0.0.0.0, so it runs on Render,
any PaaS, or locally.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from dotenv import load_dotenv
import yaml

# Load .env (GEMINI_API_KEY, GUARDIAN_ADMIN_TOKEN, …) if present. Real env
# vars always win — load_dotenv does not override existing environment.
load_dotenv()
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from guardian.core import Guardian
from guardian.events import EventLog
from guardian.reasoning import CheckpointDB, ReasoningTrail
from wallet.wallet import Wallet

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("GUARDIAN_DATA_DIR", str(BASE_DIR / "data")))
DASHBOARD_DIR = BASE_DIR / "dashboard"
EVENTS_PATH = DATA_DIR / "events.jsonl"
WALLET_PATH = DATA_DIR / "wallet.json"
CHECKPOINTS_PATH = DATA_DIR / "checkpoints.db"
CONFIG_PATH = Path(os.getenv("GUARDIAN_CONFIG", str(BASE_DIR / "config.yaml")))

with CONFIG_PATH.open(encoding="utf-8") as fh:
    CONFIG = yaml.safe_load(fh)

LOG = EventLog(EVENTS_PATH)
WALLET = Wallet(CONFIG["policy"], WALLET_PATH, starting_balance=CONFIG["wallet"]["starting_balance_usd"])
GUARDIAN = Guardian(CONFIG, LOG, WALLET)
# Maintenance mode: GUARDIAN_REASONING_ENABLED=0 makes the reasoning layer
# never pause/terminate, so the opencode plugin never blocks tools. Useful
# while developing Guardian itself; keep unset (or =1) for real supervision.
REASONING_DISABLED = os.getenv("GUARDIAN_REASONING_ENABLED", "1").lower() in ("0", "false", "no", "off")
REASONING = ReasoningTrail(CONFIG, CheckpointDB(CHECKPOINTS_PATH), GUARDIAN.llm, log=LOG, wallet=WALLET,
                           disabled=REASONING_DISABLED)
GUARDIAN.reasoning = REASONING
BEAT_LOCK = threading.Lock()

# Registered opencode plugin instances (heartbeat registry).
# The plugin registers once at startup and re-beats every ~30s, so the
# dashboard can show exactly which opencode sessions are connected to Guardian.
INSTANCES: dict[str, dict] = {}
INSTANCES_LOCK = threading.RLock()
INSTANCE_TIMEOUT_S = 90


def _instances() -> list[dict]:
    import time

    now = time.time()
    with INSTANCES_LOCK:
        out = []
        for inst in INSTANCES.values():
            connected = (now - inst["last_seen"]) < INSTANCE_TIMEOUT_S
            out.append({**inst, "connected": connected})
        return sorted(out, key=lambda i: (not i["connected"], -i["last_seen"]))

app = FastAPI(title="Guardian", version="0.1.0")


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Never cache dashboard assets — stale JS caused phantom 403s."""
    response = await call_next(request)
    if request.url.path.startswith(("/static/", "/")):
        response.headers["Cache-Control"] = "no-store"
    return response


def _admin_token() -> str:
    return os.getenv("GUARDIAN_ADMIN_TOKEN", "demo")


def _require_admin(x_admin_token: str = Header(default="")) -> None:
    if x_admin_token != _admin_token():
        raise HTTPException(status_code=403, detail="invalid admin token")


# --- data -------------------------------------------------------------------
def _data_payload() -> dict:
    return {
        "generated_at": LOG.tail(1)[0]["ts"] if LOG.tail(1) else None,
        "status": GUARDIAN.status(),
        "events": LOG.tail(700),
        "cursor": LOG.cursor(),
        "admin_required": _admin_token() != "demo",
        "beats": ["approve", "reject", "bypass", "loop", "kill", "hallucinate", "recover"],
        "reasoning_sessions": REASONING.sessions(),
        "instances": _instances(),
    }


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/data")
def api_data():
    return _data_payload()


@app.get("/api/events")
def api_events(cursor: int = 0):
    return {"events": LOG.tail(700, after=cursor), "cursor": LOG.cursor()}


# --- agent-facing (no admin token) ------------------------------------------
@app.post("/api/request")
def api_request(payload: dict):
    return GUARDIAN.submit_request(
        payload.get("intent", payload),
        actor="agent",
        session_id=payload.get("session_id", ""),
    )


@app.post("/api/snapshot")
def api_snapshot(payload: dict):
    return GUARDIAN.post_snapshot(payload.get("snapshot", payload))


@app.post("/api/retry")
def api_retry():
    return GUARDIAN.record_retry()


# --- reasoning supervision (opencode plugin) ---------------------------------
@app.post("/api/checkpoint")
def api_checkpoint(payload: dict):
    return REASONING.record(
        session_id=(payload.get("session_id") or "default"),
        context_md=payload.get("context_md", "") or "",
        todos=payload.get("todos") or [],
        tool_trail=payload.get("tool_trail") or [],
    )


@app.get("/api/reasoning")
def api_reasoning(session_id: str | None = None):
    if session_id:
        return REASONING.trail(session_id)
    return {"sessions": REASONING.sessions()}


@app.get("/api/reasoning/gate")
def api_reasoning_gate(session_id: str = "default"):
    return REASONING.gate(session_id)


# --- opencode plugin registry (heartbeat) ------------------------------------
@app.post("/api/plugin/register")
def api_plugin_register(payload: dict):
    """The opencode plugin calls this once at startup and then re-beats every
    ~30s. Lets the dashboard show which instances are connected and enforces
    nothing by itself — registration is informational."""
    import time

    instance_id = (payload.get("instance_id") or payload.get("session_id") or "").strip() or "unknown"
    now = time.time()
    with INSTANCES_LOCK:
        prev = INSTANCES.get(instance_id)
        INSTANCES[instance_id] = {
            "instance_id": instance_id,
            "first_seen": (prev or {}).get("first_seen", now),
            "last_seen": now,
            "hostname": (payload.get("hostname") or "").strip(),
            "project": (payload.get("project") or "").strip(),
            "directory": (payload.get("directory") or "").strip(),
            "opencode_version": (payload.get("opencode_version") or "").strip(),
            "model": (payload.get("model") or "").strip(),
            "session_id": (payload.get("session_id") or "").strip(),
        }
    return {"registered": True, "instance_id": instance_id, "timeout_s": INSTANCE_TIMEOUT_S}


@app.get("/api/instances")
def api_instances():
    return {"instances": _instances()}


# --- admin controls ----------------------------------------------------------
@app.post("/api/reset")
def api_reset(x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    LOG.clear()
    GUARDIAN.reset()
    REASONING.reset()
    return GUARDIAN.status()


@app.post("/api/kill")
def api_kill(payload: dict | None = None, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    active = bool((payload or {}).get("active", True))
    return GUARDIAN.kill_switch(active)


@app.post("/api/beat/{name}")
def api_beat(name: str, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    with BEAT_LOCK:
        from demo.beats import run_beat

        try:
            result = run_beat(GUARDIAN, CONFIG, name)
            return {"beat": name, "result": result, "status": GUARDIAN.status()}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- owner-in-the-loop controls (reasoning supervision) ----------------------
@app.post("/api/topup")
def api_topup(payload: dict | None = None, x_admin_token: str = Header(default="")):
    """Owner tops up the wallet AND resumes the paused session. The judge's
    pause is never overridden — the owner explicitly re-authorizes spend."""
    _require_admin(x_admin_token)
    amount = float((payload or {}).get("amount", 10.0) or 0.0)
    session_id = (payload or {}).get("session_id", "default")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    topup = WALLET.top_up(amount)
    LOG.append("topup", amount=amount, balance=topup["balance"], session_id=session_id)
    resume = REASONING.resume(session_id)
    return {"topup": amount, "balance": topup["balance"], "resume": resume}


@app.post("/api/resume")
def api_resume(payload: dict | None = None, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    session_id = (payload or {}).get("session_id", "default")
    return REASONING.resume(session_id)


@app.post("/api/terminate")
def api_terminate(payload: dict | None = None, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    session_id = (payload or {}).get("session_id", "default")
    reason = (payload or {}).get("reason", "terminated by owner")
    return REASONING.terminate(session_id, reason)


# --- dashboard (static) ------------------------------------------------------
if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(DASHBOARD_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
