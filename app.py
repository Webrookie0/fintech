"""Guardian — FastAPI app.

Serves the dashboard (plain static files, waku-style) AND the Guardian APIs on
one port. Host-agnostic: reads PORT and binds 0.0.0.0, so it runs on Render,
any PaaS, or locally.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import yaml
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from guardian.core import Guardian
from guardian.events import EventLog
from wallet.wallet import Wallet

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("GUARDIAN_DATA_DIR", str(BASE_DIR / "data")))
DASHBOARD_DIR = BASE_DIR / "dashboard"
EVENTS_PATH = DATA_DIR / "events.jsonl"
WALLET_PATH = DATA_DIR / "wallet.json"
CONFIG_PATH = Path(os.getenv("GUARDIAN_CONFIG", str(BASE_DIR / "config.yaml")))

with CONFIG_PATH.open(encoding="utf-8") as fh:
    CONFIG = yaml.safe_load(fh)

LOG = EventLog(EVENTS_PATH)
WALLET = Wallet(CONFIG["policy"], WALLET_PATH, starting_balance=CONFIG["wallet"]["starting_balance_usd"])
GUARDIAN = Guardian(CONFIG, LOG, WALLET)
BEAT_LOCK = threading.Lock()

app = FastAPI(title="Guardian", version="0.1.0")


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
        "beats": ["approve", "reject", "bypass", "loop", "kill"],
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
    return GUARDIAN.submit_request(payload.get("intent", payload), actor="agent")


@app.post("/api/snapshot")
def api_snapshot(payload: dict):
    return GUARDIAN.post_snapshot(payload.get("snapshot", payload))


@app.post("/api/retry")
def api_retry():
    return GUARDIAN.record_retry()


# --- admin controls ----------------------------------------------------------
@app.post("/api/reset")
def api_reset(x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    LOG.clear()
    GUARDIAN.reset()
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
