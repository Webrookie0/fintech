/**
 * Guardian — checkpoint enforcement + loop break for opencode.
 *
 * Makes the checkpoint protocol STRUCTURAL, not asked:
 *   - after every thinking phase the big model must publish context.md
 *   - every context.md edit is POSTed to the Guardian service, which stores
 *     it with an embedding and runs the objective reasoning signals
 *   - if Guardian says paused/terminated, ALL tools are blocked (throw) —
 *     the big model cannot spend, cannot act, cannot continue
 *   - if the session goes idle N turns without a fresh checkpoint, tools are
 *     blocked with a "publish your state first" message
 *   - a terminated verdict hard-aborts the session via client.session.abort
 *
 * The plugin is the messenger. The wallet refuses because Guardian's state
 * says so — never because this plugin (which runs inside the agent's IDE)
 * was trusted to decide on its own.
 */

import type { Plugin } from "@opencode-ai/plugin"

const GUARDIAN_URL = process.env.GUARDIAN_URL || "http://localhost:8000"
const GUARDIAN_TOKEN = process.env.GUARDIAN_ADMIN_TOKEN || "demo"
const CONTEXT_FILE = process.env.GUARDIAN_CONTEXT_FILE || "context.md"
const MAX_IDLE_TURNS = 2 // idles without a fresh checkpoint before we block
const HEARTBEAT_MS = 30_000

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

type Verdict = { status: string; reason?: string }

const sessions = new Map<string, {
  verdict: Verdict
  idleTurns: number
  lastCheckpoint: number
  toolTrail: any[]
  dirty: boolean
}>()

function sess(id: string) {
  let s = sessions.get(id)
  if (!s) {
    s = { verdict: { status: "running" }, idleTurns: 0, lastCheckpoint: 0, toolTrail: [], dirty: false }
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
    console.warn(`[guardian] heartbeat failed (Guardian offline?): ${(e as Error).message}`)
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
    console.log(`[guardian] checkpoint → ${s.verdict.status}: ${s.verdict.reason || ""}`)
  } catch (e) {
    // Guardian offline: do not block the developer's work. Log and continue.
    console.warn(`[guardian] checkpoint POST failed: ${(e as Error).message}`)
  }
}

// Is this path the checkpoint file? Case-insensitive on the basename, because
// the file may be written as CONTEXT.md while the env/fallback name is
// "context.md" (macOS is a case-insensitive filesystem).
function isContextPath(p: string): boolean {
  return typeof p === "string" && p.split("/").pop()!.toLowerCase() === CONTEXT_FILE.toLowerCase()
}

export const GuardianPlugin: Plugin = async ({ client, directory, project }) => {
  async function refreshVerdict(sessionID: string) {
    const s = sess(sessionID)
    try {
      const res = await fetch(`${GUARDIAN_URL}/api/reasoning/gate?session_id=${encodeURIComponent(sessionID)}`)
      if (res.ok) {
        const data = await res.json()
        s.verdict = { status: data.status, reason: data.reason }
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
        if (Date.now() - s.lastCheckpoint > 30_000) {
          s.idleTurns += 1
          if (s.idleTurns >= MAX_IDLE_TURNS) {
            blockReasons.set(sessionID, `context.md stale — publish your current state (${MAX_IDLE_TURNS} turns without a checkpoint)`)
            try {
              await client.tui.showToast({ body: { message: "Guardian: checkpoint required before continuing", variant: "warning" } })
            } catch {}
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
      const isContextWrite =
        ["write", "edit", "patch"].includes(input.tool) &&
        isContextPath((input as any).args?.filePath)
      if (isContextWrite) return

      // Read-only tools are never blocked — paused sessions must stay able to
      // inspect the workspace and self-correct. Only spendy tools are gated.
      if (READ_ONLY_TOOLS.has(input.tool)) return

      const reason = blockReasons.get(input.sessionID)
      if (reason) throw new Error(`[guardian] ${reason}`)

      // Fetch the live verdict for spendy tools. Reading files is free; a shell
      // command or an edit is not, so it's worth one round-trip on the money path.
      try {
        const res = await fetch(`${GUARDIAN_URL}/api/reasoning/gate?session_id=${encodeURIComponent(input.sessionID)}`)
        if (res.ok) {
          const data = await res.json()
          s.verdict = { status: data.status, reason: data.reason }
        }
      } catch {}

      if (s.verdict.status !== "running") {
        const why = s.verdict.reason || `session ${s.verdict.status}`
        throw new Error(`[guardian] session ${s.verdict.status}: ${why}`)
      }
    },
  }
}

export default GuardianPlugin
