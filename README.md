# Guardian

> **New here? Read [context.md](context.md) first** — a self-contained briefing
> for teammates, judges, and AI assistants.

A **policy-enforced control plane for autonomous AI agents** — a trusted
execution layer that supervises every financial action an AI agent can take.

> **Never trust the autonomous agent. Trust the enforcement layer.**

LLMs are probabilistic: they hallucinate, drift from objectives and get stuck
in retry loops. Prompting them to "behave" is not enforcement. Guardian puts a
deterministic policy engine, an independent mock wallet and a small advisory
judge model **between** the agent and any money movement — and it all renders
live on a browser dashboard.

## How it works

```
        AGENT (untrusted)
              │  proposes a structured transaction intent
              ▼
        ┌──────────────┐
        │   GUARDIAN   │── advisory review ──▶ JUDGE (small LLM)
        │              │── hard gate        ──▶ POLICY (deterministic)
        └──────┬───────┘
               │ approve / reject (policy can veto the judge)
               ▼
        WALLET (mock)  — re-checks EVERYTHING itself
              │         allowlist · caps · daily budget · kill switch
              ▼
        ledger entry / refusal (audit trail)
```

Every step — the intent, the judge's recommendation, the policy rules that
fired, and the wallet's own verdict — is appended to an append-only
`events.jsonl` audit trail that the dashboard renders live.

## Run it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m demo.seed        # clean demo state
.venv/bin/uvicorn app:app --port 8000
# open http://localhost:8000
```

Optional LLM keys (Gemini / OpenAI / Groq free tiers). With no keys the judge
runs a deterministic heuristic — the demo works either way:

```bash
export GEMINI_API_KEY=...     # or OPENAI_API_KEY, or GROQ_API_KEY
```

## The demo (5 beats)

From the **Demo Console** on the dashboard, or via API:

| Beat | What happens | Result |
|---|---|---|
| Legit purchase | Agent buys OpenAI credits | approved, balance drops |
| Policy violation | `$500 → Unknown Wallet` | policy **blocks**, wallet refuses |
| Direct bypass | Compromised agent calls wallet with a forged token | wallet independently **refuses** |
| Retry loop | Agent stuck retrying the same call | loop detector **terminates** |
| Hallucinated claim | Agent's context.md claims tests pass, tool log shows failure | reasoning judge **pauses** with evidence |
| Owner top-up + resume | Owner reviews trail, tops up, resumes | agent continues |
| Kill switch | Owner freezes the system | all spending frozen instantly |

```bash
curl -X POST localhost:8000/api/beat/approve -H "X-Admin-Token: demo"
curl -X POST localhost:8000/api/reset    -H "X-Admin-Token: demo"
```

## API

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/request` | — (agent) | submit a transaction intent |
| `POST /api/snapshot` | — (agent) | post a context snapshot (loop detection) |
| `POST /api/retry` | — (agent) | record a retry |
| `POST /api/checkpoint` | — (opencode plugin) | post context.md + tool trail → reasoning verdict |
| `GET /api/reasoning` | — | checkpoint trail / sessions |
| `GET /api/reasoning/gate` | — | sync gate: is this session allowed to act? `{status, reason, mode, override}` |
| `GET /api/data` | — | full dashboard payload |
| `GET /api/events?cursor=N` | — | incremental audit events |
| `POST /api/reasoning/mode` `{mode}` | admin | switch enforce / watch / ask at runtime |
| `POST /api/reasoning/override` `{session_id, minutes?/until_closed?}` | admin | open the gate for a session (5 min or until closed) |
| `POST /api/reasoning/clear-override` `{session_id}` | admin | close the override early |
| `POST /api/beat/{name}` | admin | run a demo beat |
| `POST /api/kill` `{active}` | admin | kill switch on/off |
| `POST /api/topup` `{amount}` | admin | owner tops up wallet + resumes session |
| `POST /api/resume` | admin | owner resumes a paused session |
| `POST /api/terminate` | admin | owner hard-stops a session |
| `POST /api/reset` | admin | reset demo state |
| `GET /healthz` | — | health check |

Admin calls require the header `X-Admin-Token` (env `GUARDIAN_ADMIN_TOKEN`,
default `demo`). **Set a real token before deploying publicly.**

## Deploy to Render

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → pick the repo (uses `render.yaml`), or
   **New Web Service** → repo → Runtime *Docker*.
3. Set `GUARDIAN_ADMIN_TOKEN` in Environment to something non-public.
4. Open `https://<your-app>.onrender.com`.

The dashboard is the show: judges open the live link, click the demo beats,
and watch the enforcement layer block the bad stuff in real time.

## Project layout

```
app.py               FastAPI — dashboard + APIs on one port
config.yaml          the policy (limits, allowlist, thresholds)
guardian/            trusted layer: policy · judge · decision · loop detection · audit log
wallet/              untrusting mock wallet (independent checks, kill switch)
agent/               the untrusted agent harness (spend only via Guardian)
dashboard/           plain static frontend (no build step)
demo/                scripted beats + seed reset
```

The emphasis is on proving that autonomous agents can spend money safely while
constrained by an **independent** enforcement layer — not on building the
smartest agent.

## Reasoning supervision (v2)

Token count alone isn't a good stop-signal, so v2 watches the agent's
*reasoning*, not just its wallet:

- The big model publishes its thinking to `context.md` after each phase; an
  opencode plugin (`.opencode/plugins/guardian.ts`) posts it to Guardian.
- Guardian stores each version with an embedding and scores five **objective
  signals**: contradiction (checkpoint claims vs real tool log), stall, churn,
  drift, and repeat-strategy.
- Strong signals pause the session — the plugin then **blocks spendy tools**
  (bash, edit, write, webfetch, task) and revokes wallet access. **Read-only
  tools are never blocked**, and writing `context.md` is always allowed, so the
  agent can always inspect the workspace, self-correct, and publish a fresh
  checkpoint to lift the stale-guard itself.

### Supervision modes (`GUARDIAN_MODE`)

| Mode | Behavior |
|---|---|
| `watch` | **Never blocks** — advisory only, toasts warnings (safe default) |
| `enforce` | Paused/terminated sessions block spendy tools (strict) |
| `ask` | Blocks on pause, but the dashboard's **Allow session** button opens the gate **until you close it** (`until_closed`), or for 5 minutes |

Change it from the dashboard (Supervision mode dropdown) or
`POST /api/reasoning/mode {mode}` (admin).

### Escape hatches (you can never be locked out)

- **Allow session** — on the Reasoning tab, any session (even paused) gets an
  *Allow session* button that opens the gate until you close it, plus *Allow
 5m* and *Close*. It does **not** change the verdict — the dashboard still shows
  the true state.
- **`GUARDIAN_BYPASS=1`** — the bulletproof switch: the opencode plugin becomes
  a complete no-op on its **next start**. If you ever feel locked out, set it in
  the environment and restart opencode once. No gate, no stale-guard, nothing.
- **`context.md` always writes** — even a paused session can publish a
  checkpoint, which resets the stale-guard and posts a fresh verdict.
- **Stale-guard is generous** — it only arms after the session's first tool use,
  resets on any tool event, blocks at 4+ idle turns (warning toast at 2), and
  only in `enforce` mode.

Run a real session: start the server, then use opencode in this repo — the
plugin is auto-loaded from `.opencode/plugins/`.

## Documentation

- **[Architecture](docs/ARCHITECTURE.md)** — the full system design, the trust
  layers, and how Guardian acts as the layer between the AI agent and the wallet.
  Use this to walk judges through the project.
