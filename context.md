# Guardian — Project Context (read this first)

> **Last updated: Aug 1 2026**

A self-contained briefing. Read top to bottom; the glossary at the end covers
all the AI jargon. Feel free to paste this whole file into any AI assistant
and ask for help.

---

## 1. What this project is (one paragraph)

Guardian is a **security layer that sits between an AI agent and the money it
can spend**. AI agents (chatbots that write code, browse the web, buy APIs)
are powerful but unreliable — they hallucinate, go off-task, and get stuck in
expensive loops. Guardian is the "enforcer": the agent proposes every purchase,
Guardian checks it against hard rules, and a mock wallet executes only if it
independently re-verifies everything. Even a hacked agent cannot spend outside
the rules.

> Core principle: **Never trust the autonomous agent. Trust the enforcement
> layer.** Prompting an AI to "behave" is not security.

## 2. What it does right now (built + tested)

- [x] Deterministic policy engine: $20/day budget, $10 max per transaction,
      allowlisted recipients (OpenAI, Supabase, Anthropic, Tavily), max retries,
      max token estimate
- [x] Small-model "Judge" that reviews each request and recommends
      approve/reject — advisory only, policy has the final word
- [x] Mock wallet that re-checks every rule itself (even a forged approval is
      refused)
- [x] Loop detector: if the agent repeats the same action with no progress, the
      workflow auto-pauses/terminates
- [x] Kill switch: one button freezes all spending instantly
- [x] Append-only audit trail (events.jsonl) rendered live on a web dashboard
- [x] 5 scripted demo beats, all verified working
- [x] Dashboard tabs: Overview, Requests, Snapshots, Wallet, Policy, Audit +
      Demo Console
- [x] Render-deploy ready (Dockerfile + render.yaml)

## 3. How it fits together (architecture)

[Agent (untrusted)] → intent → [Guardian: Judge (advice) + Policy (hard gate)]
→ approved? → [Wallet re-checks] → ledger / refusal.

The agent's only money path is through Guardian. If it calls the wallet
directly with a forged token, the wallet still refuses. (See
docs/ARCHITECTURE.md + docs/architecture-flowchart.png for diagrams.)

## 4. Project layout

    app.py            FastAPI — dashboard + APIs on one port
    config.yaml       the policy (budget, allowlist, limits)
    guardian/         policy · judge · llm client · loop detector · memory · audit log
    wallet/           untrusting mock wallet + kill switch
    agent/            the (untrusted) agent harness
    dashboard/        plain static frontend (no build step)
    demo/             scripted beats + seed reset
    docs/             architecture doc + flowchart images
    Dockerfile, render.yaml, requirements.txt

## 5. How to run it locally

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m demo.seed        # clean demo state
    .venv/bin/uvicorn app:app --port 8000
    # open http://localhost:8000 → click "Run full demo"

Optional: set GEMINI_API_KEY / OPENAI_API_KEY / GROQ_API_KEY (free tiers) to
use a real Judge model; otherwise it uses a deterministic offline fallback.

## 6. What's next (v2 plan — reasoning supervision)

Token count alone isn't a good stop-signal. v2 catches *hallucinations* and
lets the owner review the agent's thinking:

- **Checkpoint protocol** — the big model must write context.md after each
  thinking phase; the opencode plugin blocks tools if it's stale (enforced,
  not asked)
- **Reasoning trail in a DB** — every checkpoint stored with an embedding →
  dynamic RAG over the agent's own past reasoning
- **Objective hallucination detection** — contradiction (checkpoint claims
  "tests pass" but tool log shows failure), stall (embeddings too similar),
  churn (plan rewritten), drift (off-goal), repeat-strategy (same failed idea)
- **Reasoning judge (new small model)** — reviews signals, returns verdict +
  evidence, can pause. Never sets budget.
- **Owner-in-the-loop** — dashboard shows the reasoning trail; owner can top
  up wallet, redirect, resume, or terminate
- **OpenCode plugin** — enforces the checkpoint protocol live in the IDE

## 7. Build order (what we're doing)

- Phase A (Guardian-side): checkpoints DB + embeddings + reasoning signals +
  reasoning judge + owner top-up flow + Reasoning dashboard tab
- Phase B (IDE): opencode plugin for checkpoint enforcement + live snapshots

## 8. Demo story (for judges)

1. Real agent plans in context.md, buys OpenAI credits → approved live
2. Agent claims a success the tool log contradicts → judge catches it with
   evidence → workflow paused
3. Dashboard shows the contradiction; owner reviews the approach trail
4. Owner tops up, redirects, resumes → agent continues
5. Kill switch hard-freezes everything

## 9. Honest limitations

- Mock wallet (no real money) — safe for demo
- Judge is advisory by design; security is the deterministic layer
- Demo agent is scripted; the product is the enforcement layer
- Future: real wallets, multisig, cloud/API budget governance

## 10. Team roles (what you can help with)

- Owner: solo-building the backend
- Non-AI teammates: presentation narrative, README polish, demo QA,
  backup screen recording, testing this briefing
- Anyone: paste this file into any AI and ask it to explain / improve /
  build parts

## 11. Glossary (jargon decoded)

- LLM — a large language model; the "AI brain"
- Agent — a program that uses an LLM to take actions (write code, call APIs)
- Prompt — the text instruction given to an LLM
- Token — the unit of text/models and billing; more tokens = more cost
- Hallucination — an AI confidently stating something false
- Loop / retry loop — an agent repeating a failing action endlessly
- Embedding — a vector of numbers capturing text meaning; used to compare
  "how similar" two texts are
- RAG — retrieve-and-generate: look up relevant stored context before
  answering
- Kill switch — a manual freeze that stops all spending
- Mock wallet — a fake wallet that behaves like a real one but moves no money
