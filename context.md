# Context

## Goal

Enable Gemini as Guardian's small-judge LLM and deploy Guardian so it can
supervise opencode working in any project (not just this repo).

## Plan (current work: per-user accounts)

1. ✅ Add GUARDIAN_MODE (enforce | watch | ask) to the server gate + endpoints
2. ✅ Add POST /api/reasoning/override (Allow for 5 min) + POST /api/reasoning/mode
3. ✅ Plugin: GUARDIAN_BYPASS=1 no-op escape hatch on next start
4. ✅ Plugin: mode-aware blocking (watch=toast only, ask=block+allow hint)
5. ✅ Plugin: fresh-session lockout fix (arm after first tool use, reset on tool
   event, block only at 4+ idle turns, only in enforce mode)
6. ✅ Dashboard: User Mode selector + Allow session (5 min) buttons
7. ✅ Re-deploy to Render + fix empty dashboard center
8. 🔜 Per-user accounts: login-gated dashboard, device tokens, per-user
   devices/sessions isolation, Connect tab with install commands

## Progress

The four "blunt gate" fixes are DONE, unit-tested (7/7 pass), and the server is
restarted with them live:

1. Plugin scopes the block: paused blocks only spendy tools (bash, edit, write,
   webfetch, task); read/grep/glob/list/skill/question stay open so the agent
   can always inspect and self-correct. guardian.ts also now checks the live
   gate synchronously on the money path (resumes take effect immediately) and
   correctly exempts context.md writes (was reading args from the wrong object).
2. Escalation ladder in reasoning.py: the first fired signal is a WARNING that
   keeps the session running; only a repeated signal pauses; terminate still
   capped at max_pauses_before_terminate (3).
3. Judge-confirmed pauses: ReasoningJudge.confirm() was dead code — never
   called. Now a contradiction signal goes through the judge first; if the
   judge's confidence < 0.5 or recommendation != "paused", the contradiction
   is overruled and no pause happens.
4. Self-heal: context.md writes are always allowed, even when paused.

BLOCKER (unchanged): BOTH Gemini keys were flagged as leaked (403) because they
were pasted into the chat. The judge still falls back to heuristics offline.
A valid, never-shared key is needed before the judge can call Gemini.

## Next step

Restart the server + verify the auth flow, then deploy to Render and point
GUARDIAN_URL at the deployed server. Register the first (admin) account on the
deployed dashboard, then set GUARDIAN_DEVICE_TOKEN in this repo's env.
