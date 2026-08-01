# Guardian — AI Supervision Engine

Guardian is a core engine that supervises autonomous AI agents. It consumes a
checkpoint describing what an agent is currently doing and produces a
supervision verdict: **ALLOW**, **PAUSE**, or **TERMINATE** — with a reason.

This is a CLI-only prototype of the supervision *core*. There is no backend,
dashboard, or frontend; the engine is designed to be embedded into a real
autonomous agent loop later.

## Overview

Guardian inspects each agent checkpoint through a deterministic pipeline:

1. A **Rule Engine** flags hard violations (retry limits, exhausted budget,
   repeated tool usage).
2. An **Embedding Engine** converts the goal and the current step into dense
   vectors using a sentence-transformer model.
3. **Cosine similarity** measures semantic alignment between the goal and the
   current step.
4. If similarity is high and no rules fired, Guardian **ALLOWS** immediately
   (fast path — no LLM call, no cost).
5. Otherwise a **Judge LLM** (Gemini via its OpenAI-compatible endpoint)
   renders the final verdict. If the judge is unavailable, Guardian fails
   closed with PAUSE.

## Problem Statement

Autonomous agents can silently drift from their goal, burn budget in retry
loops, or misuse tools. LLM-only guardrails are slow, expensive, and
non-deterministic; rule-only guardrails miss semantic drift entirely. Guardian
combines cheap deterministic checks with semantic similarity and an LLM judge,
so that the LLM is consulted only when it actually adds value.

## Architecture Diagram

```
                 ┌──────────────────────────────────────────────┐
                 │                 Checkpoint                   │
                 │  goal, current_step, retry_count, budget,    │
                 │  tool_name                                   │
                 └──────────────────────┬───────────────────────┘
                                        │
                                        ▼
                 ┌──────────────────────────────────────────────┐
                 │              Rule Engine (free)              │
                 │  retry_limit_exceeded | budget_exhausted |   │
                 │  repeated_tool_usage                         │
                 └──────────────────────┬───────────────────────┘
                                        │
                                        ▼
                 ┌──────────────────────────────────────────────┐
                 │  Goal Embedding (cached per session)         │
                 │  Current Step Embedding                      │
                 │  Cosine Similarity                           │
                 └──────────────────────┬───────────────────────┘
                                        │
                                        ▼
                          similarity ≥ 0.45 AND no rules?
                        ┌───────────────┴───────────────┐
                      YES                               NO
                        │                               │
                        ▼                               ▼
                 ┌──────────────────┐        ┌──────────────────────────┐
                 │ Fast Path: ALLOW │        │  Judge LLM (Gemini)      │
                 │ (no LLM call)    │        │  or MockJudge (offline)  │
                 └──────────────────┘        └────────────┬─────────────┘
                                                        │
                                                        ▼
                                              ALLOW | PAUSE | TERMINATE
                                                      + reason
```

## Features

- **Rule Engine** — deterministic, zero-cost guardrails: retry limit, budget
  floor, repeated-tool detection (session placeholder).
- **Semantic drift detection** — `all-MiniLM-L6-v2` embeddings compared via
  cosine similarity; the goal embedding is cached per session.
- **Judge LLM** — Gemini via OpenAI-compatible endpoint
  (`/chat/completions`, Bearer auth), temperature 0, JSON-constrained output.
- **Fail-closed design** — if the judge is unreachable, Guardian returns PAUSE,
  never a silent ALLOW.
- **Deterministic MockJudge** — offline stand-in with the same interface as
  the real judge, so demos and tests run without an API key.
- **Shared rate limiter** — sliding-window throttle (wait/fail modes) applied
  to every LLM request, thread-safe, stdlib only.
- **Supervised agent loop** (`agent.py`) — a minimal one-step planner whose
  every action is checked by Guardian before the next action is generated.
- **Chaos mode** — injects subtle interference prompts so the agent may
  naturally drift; Guardian detects it and halts execution.
- **No frameworks** — no LangGraph, no FastAPI, no database; stdlib +
  sentence-transformers only.

## Folder Structure

```
innovaHack2/
├── .gitignore
├── README.md
├── requirements.txt
├── agent.py                  # minimal Guardian-supervised agent loop
├── demo.py                   # scripted validation scenarios
└── guardian/
    ├── __init__.py
    ├── models.py             # Checkpoint, JudgeDecision
    ├── rule_engine.py        # retry / budget / repeated-tool rules
    ├── embeddings.py         # sentence-transformers wrapper (lazy load)
    ├── similarity.py         # cosine similarity
    ├── judge.py              # Gemini judge client (fail-closed)
    ├── mock_judge.py         # deterministic offline judge
    ├── rate_limiter.py       # shared sliding-window limiter
    ├── guardian.py           # pipeline orchestrator
    └── main.py               # CLI entry point
```

## Installation

Requires Python 3.10+.

```powershell
pip install -r requirements.txt
```

The first run downloads the embedding model (`all-MiniLM-L6-v2`, ~90 MB).

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Gemini API key (used for both planner and judge) |
| `OPENAI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` | OpenAI-compatible endpoint base URL |
| `GUARDIAN_AGENT_MODEL` | `gemini-3.1-flash-lite` | Planner model |
| `GUARDIAN_JUDGE_MODEL` | `gemini-3.1-flash-lite` | Judge model |
| `GUARDIAN_AGENT_API_KEY` | falls back to `OPENAI_API_KEY` | Planner-only key override |
| `LLM_RATE_LIMIT_RPM` | `15` | Max LLM requests per 60-second window |
| `LLM_RATE_LIMIT_MODE` | `wait` | `wait` (block) or `fail` (raise) |

## Running demo.py

`demo.py` validates the core with four scripted scenarios. It runs fully
offline: without a key, the deterministic MockJudge replaces the LLM judge.

```powershell
python demo.py
```

Expected summary:

```
1. Normal Execution    -> completed (5 steps allowed)
2. Goal Drift          -> halted (PAUSE) at step 3
3. Retry Loop          -> halted (PAUSE) at step 5
4. Budget Exhaustion   -> halted (PAUSE) at step 5
```

## Running agent.py

`agent.py` runs a real planning loop: the LLM generates one action at a time,
Guardian supervises each before the next is generated, and execution stops the
moment Guardian returns PAUSE or TERMINATE.

```powershell
set OPENAI_API_KEY=<gemini_key>
python agent.py --goal "Build a login page."
```

Guardian never executes tools — the agent only plans.

## Chaos Mode

```powershell
python agent.py --goal "Build a login page." --chaos --chaos-p 0.3
```

After every successful step there is a `--chaos-p` probability (default 0.2)
that the planner receives a subtle interference prompt (conflicting
information, uncertainty, an interesting side task, ...). The agent may
naturally drift from the goal; Guardian detects the drift and halts execution.

## Example Outputs

Per-step decision block (agent loop):

```
==================================================
Current Action:   Create React project
Similarity:       0.1828
Triggered Rules:  none
Decision Source:  judge (mock, offline)
Decision:         ALLOW
Reason:           No rule violations and similarity above the drift floor.
==================================================
```

Per-checkpoint report (CLI):

```
================================================================
GUARDIAN SUPERVISION REPORT
================================================================
[1/3] Rule engine
  Triggered rules:  none
[2/3] Embeddings + similarity
  Cosine similarity (goal vs current_step): 0.6290
[3/3] Decision
  Source:           rule-engine (fast path)
  Verdict:          ALLOW
  Reason:           Similarity is high and no rules were triggered.
================================================================
```

## Tech Stack

- Python 3.10+ (3.11 used in development)
- sentence-transformers (`all-MiniLM-L6-v2`)
- numpy
- stdlib only for networking and concurrency (`urllib`, `threading`,
  `collections.deque`)

## Future Work

- Session activity log for real repeated-tool and retry-trajectory detection
- Windowed drift detection (median similarity over the last N steps)
- Dual-threshold bands (definite ALLOW / ambiguous → judge / definite risk)
- Data-driven threshold calibration per domain
- Decision audit logging (JSONL)
- Live-loop integration with a real autonomous agent

## License

MIT License (placeholder — add your copyright line before publishing).
