/**
 * Guardian — checkpoint enforcement + loop break for opencode.
 *
 * Makes the checkpoint protocol STRUCTURAL, not asked:
 *   - after every thinking phase the big model must publish context.md
 *   - every context.md edit is POSTed to the Guardian service, which stores
 *     it with an embedding and runs the objective reasoning signals
 *   - if Guardian says paused/terminated, spendy tools are blocked (throw);
 *     read-only tools (read/grep/glob/list/skill/question) are NEVER blocked
 *     so the agent can always inspect the workspace and self-correct
 *   - writing context.md is ALWAYS allowed (checked first) so the agent can
 *     publish a checkpoint even while paused — the permanent escape hatch
 *   - if the session goes idle N turns without a fresh checkpoint, spendy
 *     tools are blocked with a "publish your state first" message
 *   - a terminated verdict hard-aborts the session via client.session.abort
 *
 * Modes (GUARDIAN_MODE, mirrors the server's /api/reasoning/mode):
 *   enforce — full blocking (default). Stale-guard + paused blocks apply.
 *   watch   — NEVER blocks. Paused verdicts only toast a warning.
 *   ask     — paused verdicts block, but the block message points at the
 *             dashboard's "Allow for 5 min" (POST /api/reasoning/override).
 *
 * Bulletproof escape hatches:
 *   GUARDIAN_BYPASS=1 — the plugin is a complete no-op on the NEXT start.
 *                       If you ever feel locked out: set it, restart once.
 *   context.md writes are always allowed and always lift the stale guard.
 *
 * The plugin is the messenger. The wallet refuses because Guardian's state
 * says so — never because this plugin (which runs inside the agent's IDE)
 * was trusted to decide on its own.
 */

import type { Plugin } from "@opencode-ai/plugin"

const GUARDIAN_URL = process.env.GUARDIAN_URL || "http://localhost:8000"
// Plugin log file. opencode's own log only records permission checks — plugin
// console output goes to the (invisible) sidecar stdout. Write a real file so
// the plugin's activity is tailable:  tail -f /tmp/guardian-plugin.log
const PLUGIN_LOG = process.env.GUARDIAN_PLUGIN_LOG || "/tmp/guardian-plugin.log"

function log(line: string) {
  const ts = new Date().toISOString()
  console.log(`[guardian] ${line}`)
  try {
    const { Bun } = globalThis as any
    if (Bun?.write) void Bun.write(PLUGIN_LOG, `${ts} ${line}\n`, { append: true })
  } catch {}
}
// The plugin identifies itself to Guardian with a DEVICE TOKEN. Every account
// gets one (shown on the dashboard's Connect tab). Teammates set
//   export GUARDIAN_DEVICE_TOKEN="..."
// so their instances + reasoning sessions are attributed to THEIR account and
// appear only in their dashboard. Falls back to GUARDIAN_ADMIN_TOKEN / demo
// for local dev and legacy setups (those instances show up unowned — visible
// to everyone).
const GUARDIAN_TOKEN =
  process.env.GUARDIAN_DEVICE_TOKEN ||
  process.env.GUARDIAN_ADMIN_TOKEN ||
  "demo"
const CONTEXT_FILE = process.env.GUARDIAN_CONTEXT_FILE || "context.md"
// Stale-guard: block only after 4 idle turns without a fresh checkpoint
// (warning toast at 2). This is intentionally generous so a brand-new session
// that has not yet published its first checkpoint is never locked out.
const MAX_IDLE_TURNS = 4
const WARN_IDLE_TURNS = 2
const HEARTBEAT_MS = 30_000

// GUARDIAN_MODE is read at module load (matches server config). The live gate
// response from /api/reasoning/gate carries the server's authoritative mode,
// so a runtime mode switch on the server takes effect immediately.
//
// IMPORTANT: the plugin FAILS OPEN. The starting default is "watch" — the
// plugin never blocks until the SERVER confirms enforce/ask mode via the gate.
// If Guardian is offline or unreachable, the plugin stays watch (never blocks),
// so a dead server can never lock the developer out. An explicit env override
// only matters when the server is unreachable; the server's gate wins when
// present.
function envMode(): string {
  const m = (process.env.GUARDIAN_MODE || "watch").trim().toLowerCase()
  return ["enforce", "watch", "ask"].includes(m) ? m : "watch"
}

// GUARDIAN_BYPASS=1: bulletproof escape hatch. The plugin does nothing on the
// next start — no gate, no stale guard, no heartbeat, no checkpoints. Reads
// nothing, blocks nothing. Set it in the environment and restart once.
const BYPASS = process.env.GUARDIAN_BYPASS === "1"

// Read-only tools NEVER get blocked — even when a session is paused or stale,
// the model can always inspect the workspace to diagnose and self-correct.
// Only money-moving tools (shell, edits, web, spawning agents) are gated.
const READ_ONLY_TOOLS = new Set([
  "read",
  "grep",
  "glob",
  "list",
  "ls",
  "skill",
  "question",
  "todowrite",
  "getconfig",
  "config",
  "context",
  "server.session.info",
])

type Verdict = { status: string; reason?: string; mode?: string; override?: boolean }

const sessions = new Map<string, {
  verdict: Verdict
  idleTurns: number
  lastCheckpoint: number
  lastToolAt: number
  armed: boolean
  toolTrail: any[]
  dirty: boolean
}>()

function sess(id: string) {
  let s = sessions.get(id)
  if (!s) {
    s = { verdict: { status: "running" }, idleTurns: 0, lastCheckpoint: 0, lastToolAt: 0, armed: false, toolTrail: [], dirty: false }
    sessions.set(id, s)
  }
  return s
}

// Sticky per-session blocks (stale-checkpoint guard). Module-scoped so both the
// tool.execute.before hook (reads) and postCheckpoint (clears) can touch it.
const blockReasons = new Map<string, string>()

// A stable per-instance id so Guardian's registry shows THIS opencode process
// (not per-session) and keeps it even as sessions change.
const instanceID = (() => {
  try {
    const { Bun } = globalThis as any
    return Bun.nanoid ? Bun.nanoid(16) : `inst-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  } catch {
    return `inst-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  }
})()

async function heartbeat(input: {
  client: any
  project: { name?: string; id?: string }
  directory: string
  sessionID?: string
}) {
  try {
    const res = await fetch(`${GUARDIAN_URL}/api/plugin/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": GUARDIAN_TOKEN },
      body: JSON.stringify({
        instance_id: instanceID,
        hostname: (globalThis as any).process?.env?.HOSTNAME || "",
        project: input.project?.name || input.project?.id || "",
        directory: input.directory,
        opencode_version: (globalThis as any).OPENCODE_VERSION || "",
        model: input.client?.config?.get?.().model ?? "",
        session_id: input.sessionID || "",
      }),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return true
  } catch (e) {
    log(`heartbeat failed (Guardian offline?): ${(e as Error).message}`)
    return false
  }
}

async function postCheckpoint(sessionID: string, contextPath: string) {
  const s = sess(sessionID)
  let contextMd = ""
  try {
    const { Bun } = globalThis as any
    contextMd = await Bun.file(contextPath).text()
  } catch {
    contextMd = ""
  }
  const body = {
    session_id: sessionID,
    context_md: contextMd,
    tool_trail: s.toolTrail,
  }
  try {
    const res = await fetch(`${GUARDIAN_URL}/api/checkpoint`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": GUARDIAN_TOKEN },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    s.verdict = { status: data.state || "running", reason: data.reason }
    s.lastCheckpoint = Date.now()
    s.idleTurns = 0
    s.toolTrail = []
    // A fresh checkpoint satisfies the stale-guard — lift any sticky block.
    blockReasons.delete(sessionID)
    log(`checkpoint → ${s.verdict.status}: ${s.verdict.reason || ""}`)
  } catch (e) {
    // Guardian offline: do not block the developer's work. Log and continue.
    log(`checkpoint POST failed: ${(e as Error).message}`)
  }
}

// Is this path the checkpoint file? Case-insensitive on the basename, because
// the file may be written as CONTEXT.md while the env/fallback name is
// "context.md" (macOS is a case-insensitive filesystem). Split on BOTH path
// separators: Windows opencode hands us backslash paths (C:\...\context.md),
// so a single split("/") would never match and no checkpoint would ever post.
function isContextPath(p: string): boolean {
  return typeof p === "string" && p.replace(/\\/g, "/").split("/").pop()!.toLowerCase() === CONTEXT_FILE.toLowerCase()
}

async function toast(client: any, message: string, variant: "info" | "success" | "warning" | "error") {
  try {
    await client.tui.showToast({ body: { message, variant } })
  } catch {}
}

export const GuardianPlugin: Plugin = async ({ client, directory, project }) => {
  // GUARDIAN_BYPASS=1: no-op. Nothing registers, nothing blocks, nothing toasts.
  if (BYPASS) {
    log("GUARDIAN_BYPASS=1 — plugin disabled (no-op escape hatch)")
    return {}
  }

  let mode = envMode()

  async function refreshVerdict(sessionID: string) {
    const s = sess(sessionID)
    try {
      const res = await fetch(`${GUARDIAN_URL}/api/reasoning/gate?session_id=${encodeURIComponent(sessionID)}`)
      if (res.ok) {
        const data = await res.json()
        s.verdict = { status: data.status, reason: data.reason, mode: data.mode, override: !!data.override }
        if (data.mode && ["enforce", "watch", "ask"].includes(data.mode)) mode = data.mode
      }
    } catch {}
  }

  const contextPath = `${directory}/${CONTEXT_FILE}`

  // Register with Guardian and keep the heartbeat alive so the dashboard can
  // show this instance as connected. Fails open if Guardian is offline.
  void heartbeat({ client, project, directory, sessionID: "default" })
  const hb = setInterval(() => void heartbeat({ client, project, directory }), HEARTBEAT_MS)
  hb.unref?.()

  return {
    // --- post a checkpoint whenever context.md is edited -------------------
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        const sessionID = (event as any).properties?.sessionID || "default"
        const s = sess(sessionID)
        // FRESH-SESSION FIX: the stale guard only arms AFTER the session has
        // used a tool at least once. A brand-new session that simply hasn't
        // published a checkpoint yet is NOT idle — it is starting up. Never
        // block before the first tool use.
        if (!s.armed) return
        // The guard resets whenever any tool ran recently — idle is only idle
        // when nothing has happened at all.
        if (Date.now() - s.lastToolAt < 60_000) {
          s.idleTurns = 0
          return
        }
        if (Date.now() - s.lastCheckpoint > 30_000) {
          s.idleTurns += 1
          if (s.idleTurns === WARN_IDLE_TURNS) {
            void toast(client, `Guardian: ${MAX_IDLE_TURNS} idle turns without a checkpoint — publish context.md soon`, "warning")
          }
          // Only enforce mode hard-blocks on staleness. watch never blocks;
          // ask toasts too but leaves the door open for the 5-min override.
          if (s.idleTurns >= MAX_IDLE_TURNS && mode === "enforce") {
            blockReasons.set(sessionID, `context.md stale — publish your current state (${MAX_IDLE_TURNS} turns without a checkpoint)`)
            void toast(client, "Guardian: checkpoint required before continuing", "warning")
          }
        } else {
          s.idleTurns = 0
        }
      }
    },

    // --- capture the real tool trail AND detect context.md publishes -------
    // We trigger on tool.execute.after (not file.edited) because it carries
    // the sessionID, which the file event does not.
    // NOTE: the hook signature is (input, output) — `input` carries only
    // {tool, sessionID, callID, args}; the real result (status + output) is
    // the SECOND parameter. Reading input.output was silently undefined, which
    // recorded every tool as "completed" with empty output and blinded the
    // contradiction signal to real failures.
    "tool.execute.after": async (input, output) => {
      const s = sess(input.sessionID)
      const args = (input as any).args ?? {}
      const metadata = (output as any)?.metadata ?? {}
      const status = metadata.status || "completed"
      const isCtx = ["write", "edit", "patch"].includes(input.tool) &&
        isContextPath(typeof args?.filePath === "string" ? args.filePath : "")
      // ANY tool use (even a read) proves the session is alive: it re-arms the
      // guard (if not armed yet) and resets the idle counter.
      s.armed = true
      s.lastToolAt = Date.now()
      s.idleTurns = 0
      // The checkpoint-file publish is the REPORTING mechanism, not a tool
      // action — don't put it in the trail it triggers. Counting it would
      // trip the repeat-strategy signal on the 3rd identical context.md write
      // (pause at 3) before the stall signal (4 identical checkpoints) fires.
      if (!isCtx) {
        s.toolTrail.push({
          tool: input.tool,
          input: args,
          status,
          output: (output as any)?.output ?? "",
        })
        if (s.toolTrail.length > 60) s.toolTrail.shift()
      }
      if (isCtx) {
        void postCheckpoint(input.sessionID, contextPath)
      }
    },

    // --- the enforcement point: block tools when Guardian says so ----------
    "tool.execute.before": async (input, output) => {
      const s = sess(input.sessionID)
      // Always allow the context.md publish itself (reads too) so the model
      // can always write its state — EVEN while the stale-checkpoint guard is
      // active. Checked FIRST so the agent can always escape the block.
      // NOTE: in tool.execute.before the tool ARGS arrive in the `output`
      // parameter (input carries only {tool, sessionID, callID}), so reading
      // input.args was always undefined and the escape hatch never fired.
      const isContextWrite =
        ["write", "edit", "patch"].includes(input.tool) &&
        isContextPath((output as any)?.args?.filePath)
      if (isContextWrite) return

      // Read-only tools are never blocked — paused sessions must stay able to
      // inspect the workspace and self-correct. Only spendy tools are gated.
      if (READ_ONLY_TOOLS.has(input.tool)) return

      // A tool is about to run — the session is demonstrably active.
      s.armed = true
      s.lastToolAt = Date.now()
      s.idleTurns = 0

      // Fetch the live verdict + authoritative mode for spendy tools. Reading
      // files is free; a shell command or an edit is not, so it's worth one
      // round-trip on the money path. Done BEFORE any block check so:
      //   - the server's mode (enforce/watch/ask) is always adopted
      //   - an owner override (Allow session) can lift even the stale block
      //   - if Guardian is offline the mode stays "watch" → never block
      await refreshVerdict(input.sessionID)

      // watch mode never blocks: advisory only. The SERVER decides this — a
      // runtime switch on the dashboard takes effect on the next spendy tool.
      if (mode === "watch") return

      // An owner override (Allow session / until closed) opens the gate
      // regardless of the verdict or the stale guard — that is the escape hatch.
      if (s.verdict.override) {
        blockReasons.delete(input.sessionID)
        log(`override active — session ${input.sessionID} allowed through gate`)
        return
      }

      const reason = blockReasons.get(input.sessionID)
      if (reason) {
        if (mode === "ask") {
          void toast(client, `Guardian: ${reason} — Allow for 5 min on the dashboard`, "warning")
        }
        log(`BLOCKED ${input.tool}: ${reason} (mode=${mode})`)
        throw new Error(`[guardian] ${reason}`)
      }

      if (s.verdict.status !== "running") {
        const why = s.verdict.reason || `session ${s.verdict.status}`
        if (mode === "ask") {
          void toast(client, `Guardian: session ${s.verdict.status} — Allow for 5 min on the dashboard to continue`, "warning")
        }
        log(`BLOCKED ${input.tool}: session ${s.verdict.status} — ${why} (mode=${mode})`)
        throw new Error(`[guardian] session ${s.verdict.status}: ${why}`)
      }
    },
  }
}

export default GuardianPlugin
