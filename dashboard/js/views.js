"use strict";
/* views.js — renderers for each tab. Pure functions of the latest /api/data payload. */

function statusBadge(st) {
  const map = { running: "running", paused: "paused", terminated: "terminated" };
  return `<span class="badge ${map[st] || "running"}">${esc(st).toUpperCase()}</span>`;
}

function ruleBadge(ok) {
  return `<span class="badge ${ok ? "approve" : "reject"}">${ok ? "PASS" : "BLOCK"}</span>`;
}

/* ---------- Overview ---------- */
function renderOverview(data) {
  const st = data.status.state;
  const w = data.status.wallet;
  const p = data.status.policy;
  const kill = !!p.kill_switch;
  const view = document.getElementById("view");
  view.innerHTML = "";

  const pills = el("div", "grid");
  pills.style.gridTemplateColumns = "repeat(auto-fill, minmax(150px,1fr))";
  pills.append(
    pill("Workflow", statusBadge(st.status), st.status),
    pill("Budget remaining", money(st.budget_remaining), "brand"),
    pill("Spent today", money(st.spent_today), "green"),
    pill("Wallet balance", money(w.balance), "brand"),
    pill("Approvals", num(st.approvals), "green"),
    pill("Rejections", num(st.rejections), "red"),
    pill("Policy violations", num(st.violations), "red"),
    pill("Retries", num(st.retries), "amber"),
    pill("Judge model", data.status.judge, "muted"),
    pill("Kill switch", kill ? "ENGAGED" : "off", kill ? "red" : "muted"),
  );
  view.append(pills);

  const arch = el("div", "card");
  arch.innerHTML = `<h3>Enforcement path</h3>`;
  const d = el("div", "diagram");
  const agentOk = st.status === "running" || st.status === "paused";
  const walletBad = kill;
  d.innerHTML = `
    <div class="dnode ${agentOk ? "ok" : "bad"}"><div class="t">AGENT</div><div class="d">untrusted · proposes actions</div></div>
    <div class="dedge">▼ propose intent</div>
    <div class="dnode ok"><div class="t">GUARDIAN</div><div class="d">structured transaction intent</div></div>
    <div class="dedge">◀ advisory ▸</div>
    <div style="display:flex;gap:14px">
      <div class="dnode ok"><div class="t">JUDGE</div><div class="d">small LLM · ${esc(data.status.judge)}</div></div>
      <div class="dnode ok"><div class="t">POLICY</div><div class="d">deterministic · can veto</div></div>
    </div>
    <div class="dedge">▼ approve / reject</div>
    <div class="dnode ${walletBad ? "bad" : "ok"}"><div class="t">WALLET</div><div class="d">${walletBad ? "FROZEN — kill switch" : "re-checks everything itself"}</div></div>`;
  arch.append(d);
  view.append(arch);

  const goal = el("div", "card");
  goal.innerHTML = `<h3>Current objective</h3><div class="big" style="font-size:18px">${esc(st.current_goal)}</div>
    <div style="margin-top:8px"><span class="muted">current step:</span> <span class="mono">${esc(st.current_step)}</span></div>
    <div style="margin-top:4px"><span class="muted">current tool:</span> <span class="mono">${esc(st.current_tool || "—")}</span></div>
    ${st.terminate_reason ? `<div style="margin-top:8px;color:var(--red)">⚠ ${esc(st.terminate_reason)}</div>` : ""}`;
  view.append(goal);

  const feed = el("div", "card");
  feed.innerHTML = `<h3>Live trail</h3>`;
  feed.append(renderFeed(data.events.slice(-16)));
  view.append(feed);
}

function pill(k, v, tone) {
  const p = el("div", "pill");
  p.innerHTML = `<div class="k">${esc(k)}</div><div class="v ${esc(tone)}">${v}</div>`;
  return p;
}

/* ---------- Auth (login / register) ---------- */
function renderAuth() {
  const box = $id("auth-screen");
  box.hidden = false;
  $id("statusbar").innerHTML = "";
  $id("view").innerHTML = "";
  $id("admin-row").hidden = true;
  box.innerHTML = `<div class="card" style="max-width:440px;margin:40px auto">
    <h3>Guardian — sign in</h3>
    <p class="muted" style="margin:0 0 12px">The dashboard is per-account: you only see the devices and
    sessions connected with your device token. The first account created becomes the admin.</p>
    <div id="auth-form">
      <input id="auth-email" placeholder="you@example.com" autocomplete="email" style="width:100%;background:var(--bg);border:1px solid var(--line);border-radius:6px;color:var(--text);padding:8px 10px;font-family:var(--mono);margin-bottom:8px">
      <input id="auth-password" type="password" placeholder="password (min 8 chars)" autocomplete="current-password" style="width:100%;background:var(--bg);border:1px solid var(--line);border-radius:6px;color:var(--text);padding:8px 10px;font-family:var(--mono);margin-bottom:12px">
      <div style="display:flex;gap:8px">
        <button class="btn ok" id="auth-login" style="flex:1;justify-content:center">Sign in</button>
        <button class="btn ghost" id="auth-register" style="flex:1;justify-content:center">Create account</button>
      </div>
      <div class="muted" id="auth-error" style="margin-top:10px;color:var(--red)"></div>
    </div>
  </div>`;
  const err = $id("auth-error");
  async function act(path) {
    err.textContent = "";
    const email = $id("auth-email").value.trim();
    const password = $id("auth-password").value;
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
      SESSION_TOKEN = data.token;
      localStorage.setItem("guardian_session", SESSION_TOKEN);
      location.hash = "#connect";
      refresh();
    } catch (e) {
      err.textContent = e.message;
    }
  }
  $id("auth-login").onclick = () => act("/api/auth/login");
  $id("auth-register").onclick = () => act("/api/auth/register");
  $id("auth-password").addEventListener("keydown", (ev) => { if (ev.key === "Enter") act("/api/auth/login"); });
}

/* ---------- Connect (per-user setup) ---------- */
function renderConnect(data) {
  const view = document.getElementById("view");
  view.innerHTML = "";
  const auth = data.auth || {};
  const origin = location.origin;

  const intro = el("div", "card");
  intro.innerHTML = `<h3>Connect your opencode to this Guardian</h3>
    <p class="muted" style="margin:0">Every account has a private <b>device token</b>. You paste it into your
    machine's environment, restart opencode, and Guardian attributes your instances and reasoning sessions to
    <b>${esc(auth.email || "this account")}</b> — nobody else sees them.</p>`;
  view.append(intro);

  const step = (n, title, body) => {
    const c = el("div", "card");
    c.innerHTML = `<h3><span class="muted">${n}.</span> ${esc(title)}</h3>`;
    c.append(body);
    return c;
  };

  // --- step 1: install the plugin (copy block) ---
  const installCmd = [
    `mkdir -p ~/.config/opencode/plugins`,
    `curl -fsSL ${origin}/plugin/guardian.ts -o ~/.config/opencode/plugins/guardian.ts`,
    `cd ~/.config/opencode && npm i @opencode-ai/plugin`,
  ].join("\n");
  view.append(step(1, "Install the guardian plugin (global, all projects)", copyBlock(installCmd, "install")));

  // --- step 2: set env with YOUR device token (copy block) ---
  const envCmd = [
    `# add to ~/.zshrc (or ~/.bashrc)`,
    `export GUARDIAN_URL="${origin}"`,
    `export GUARDIAN_DEVICE_TOKEN="${esc(auth.device_token || "")}"`,
  ].join("\n");
  view.append(step(2, "Set your environment — device token is private to you", copyBlock(envCmd, "env")));

  // --- step 3: restart opencode ---
  const restart = el("div");
  restart.innerHTML = `<p class="muted" style="margin:0">Restart opencode once. The plugin registers on startup and
    heartbeats every ~30s. Your device appears in <b>Reasoning → Connected opencode instances</b> below.</p>`;
  view.append(step(3, "Restart opencode", restart));

  // --- step 4: create a plugin (optional) ---
  const makePlugin = el("div");
  makePlugin.innerHTML = `<p class="muted" style="margin:0">Guardian is also a template for writing your own opencode
    plugins. Copy the plugin you just installed and edit it:</p>${copyBlock(
      `mkdir -p ~/.config/opencode/plugins`,
      `cp ~/.config/opencode/plugins/guardian.ts ~/.config/opencode/plugins/my-plugin.ts`,
      `# exports: export const MyPlugin: Plugin = async ({ client, directory, project }) => ({ ... })`,
      `# hooks: event, tool.execute.before, tool.execute.after, tool.execute.end...`,
    )}`;
  view.append(step(4, "Make your own plugin (optional)", makePlugin));

  // --- live status: this account's connected devices ---
  const devices = (data.instances || []).filter((i) => !i.owner || i.user_email === auth.email);
  const dcard = el("div", "card");
  dcard.innerHTML = `<h3>Your connected opencode instances</h3>
    <p class="muted" style="margin:0 0 10px">${devices.length ? "Registered on this account." : "None yet — follow steps 1–3, then wait for the first heartbeat (≤30s)."}</p>`;
  if (devices.length) {
    const t = el("table");
    t.innerHTML = `<thead><tr><th>instance</th><th>status</th><th>project</th><th>directory</th><th>last seen</th></tr></thead><tbody></tbody>`;
    const tb = t.querySelector("tbody");
    devices.forEach((inst) => {
      const tr = el("tr");
      const age = Math.max(0, Date.now() - inst.last_seen * 1000);
      tr.innerHTML = `
        <td class="plain mono">${esc(inst.instance_id)}</td>
        <td>${inst.connected ? `<span class="badge approve">CONNECTED</span>` : `<span class="badge reject">OFFLINE (${Math.round(age / 1000)}s ago)</span>`}</td>
        <td class="plain">${esc(inst.project || "—")}</td>
        <td class="plain">${esc(inst.directory || "—")}</td>
        <td class="plain">${tKey(new Date(inst.last_seen * 1000).toISOString())}</td>`;
      tb.append(tr);
    });
    dcard.append(t);
  }
  view.append(dcard);
}

function copyBlock(text, id) {
  const box = el("div");
  box.className = "copyblock";
  const code = el("pre");
  code.textContent = text;
  const btn = el("button", "btn ghost");
  btn.textContent = "copy";
  btn.style.cssText = "float:right;margin-bottom:6px;padding:4px 10px";
  btn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "copied ✓";
      setTimeout(() => { btn.textContent = "copy"; }, 1500);
    } catch {}
  };
  box.append(btn, code);
  return box;
}

/* ---------- Requests ---------- */
function renderRequests(data) {
  const view = document.getElementById("view");
  view.innerHTML = "";
  const card = el("div", "card");
  card.innerHTML = `<h3>Transaction requests — intent → judge → policy → wallet</h3>`;
  const rows = buildRequests(data.events);
  if (!rows.length) {
    card.append(el("div", "muted", "No transaction requests yet. Run the demo beats."));
  } else {
    const t = el("table");
    t.innerHTML = `<thead><tr><th>time</th><th>recipient</th><th>amount</th><th>judge</th><th>policy</th><th>decision</th><th>wallet</th><th>reason</th></tr></thead><tbody></tbody>`;
    const tb = t.querySelector("tbody");
    rows.forEach((r) => {
      const tr = el("tr");
      const dec = r.decision || "";
      const wRes = r.wallet;
      tr.innerHTML = `
        <td>${tKey(r.ts)}</td>
        <td class="plain">${esc(r.recipient)}</td>
        <td>${money(r.amount)}</td>
        <td>${r.judge ? `<span class="badge ${r.judge.recommendation === "approve" ? "approve" : "reject"}">${esc(r.judge.recommendation)}</span>` : `<span class="muted">—</span>`}</td>
        <td>${ruleBadge(r.policy_ok !== false)}</td>
        <td><span class="badge ${dec === "approve" ? "approve" : "reject"}">${esc(dec).toUpperCase()}</span></td>
        <td>${wRes ? `<span class="badge ${wRes.executed ? "approve" : "reject"}">${wRes.executed ? "EXECUTED" : "REFUSED"}</span>` : `<span class="muted">—</span>`}</td>
        <td class="plain muted">${esc(r.reason || "")}</td>`;
      tb.append(tr);
    });
    card.append(t);
  }
  view.append(card);
}

function buildRequests(events) {
  const rows = [];
  let cur = null;
  for (const ev of events) {
    if (ev.type === "intent_received") {
      cur = {
        ts: ev.ts,
        recipient: (ev.intent || {}).recipient,
        amount: (ev.intent || {}).amount,
        decision: null,
        reason: "",
        judge: null,
        policy_ok: null,
        wallet: null,
      };
      rows.push(cur);
    } else if (cur) {
      if (ev.type === "judge_review") cur.judge = ev;
      else if (ev.type === "policy_verdict") cur.policy_ok = ev.decision === "approve";
      else if (ev.type === "decision") { cur.decision = ev.decision; cur.reason = ev.reason || ""; }
      else if (ev.type === "wallet_result") cur.wallet = ev;
    }
  }
  return rows;
}

/* ---------- Snapshots ---------- */
function renderSnapshots(data) {
  const view = document.getElementById("view");
  view.innerHTML = "";
  const st = data.status.state;
  const card = el("div", "card");
  card.innerHTML = `<h3>Context snapshots — execution state, compact by design</h3>
    <div style="margin-bottom:10px"><span class="muted">stall count:</span> <b>${num(st.stall_count)}</b>
      ${st.terminate_reason ? ` <span style="color:var(--red)">⚠ ${esc(st.terminate_reason)}</span>` : ""}</div>`;
  const snaps = st.snapshots || [];
  if (!snaps.length) {
    card.append(el("div", "muted", "No snapshots yet."));
  } else {
    const t = el("table");
    t.innerHTML = `<thead><tr><th>time</th><th>current step</th><th>tool</th><th>tokens</th></tr></thead><tbody></tbody>`;
    const tb = t.querySelector("tbody");
    snaps.forEach((s) => {
      const tr = el("tr");
      tr.innerHTML = `
        <td>${tKey(s.ts)}</td>
        <td class="plain">${esc(s.current_step || "")}</td>
        <td class="plain">${esc(s.current_tool || "")}</td>
        <td>${num(s.tokens || 0)}</td>`;
      tb.append(tr);
    });
    card.append(t);
  }
  view.append(card);
}

/* ---------- Wallet ---------- */
function renderWallet(data) {
  const view = document.getElementById("view");
  view.innerHTML = "";
  const w = data.status.wallet;
  const p = data.status.policy;
  const kill = !!p.kill_switch;

  const pills = el("div", "grid");
  pills.style.gridTemplateColumns = "repeat(auto-fill, minmax(150px,1fr))";
  pills.append(
    pill("Balance", money(w.balance), "brand"),
    pill("Transactions", num(w.transaction_count), "green"),
    pill("Kill switch", kill ? "ENGAGED" : "off", kill ? "red" : "muted"),
  );
  view.append(pills);

  const card = el("div", "card");
  card.innerHTML = `<h3>Ledger — the wallet re-checks every payment itself, approval token or not</h3>`;
  const ledger = w.ledger || [];
  if (!ledger.length) {
    card.append(el("div", "muted", "No transactions yet."));
  } else {
    const t = el("table");
    t.innerHTML = `<thead><tr><th>id</th><th>time</th><th>recipient</th><th>amount</th><th>approval</th></tr></thead><tbody></tbody>`;
    const tb = t.querySelector("tbody");
    ledger.forEach((tx) => {
      const tr = el("tr");
      tr.innerHTML = `
        <td>${esc(tx.id)}</td><td>${tKey(tx.ts)}</td>
        <td class="plain">${esc(tx.recipient)}</td><td>${money(tx.amount)}</td>
        <td class="plain muted">${esc(tx.approval_id || "—")}</td>`;
      tb.append(tr);
    });
    card.append(t);
  }
  view.append(card);
}

/* ---------- Policy ---------- */
function renderPolicy(data) {
  const view = document.getElementById("view");
  view.innerHTML = "";
  const p = data.status.policy;
  const rows = [
    ["Daily budget (USD)", money(p.daily_budget_usd)],
    ["Max per transaction (USD)", money(p.max_per_transaction_usd)],
    ["Allowlisted recipients", (p.allowlist_recipients || []).join(", ")],
    ["Blocked recipients", (p.blocklist_recipients || []).join(", ") || "none"],
    ["Allowlisted domains", (p.allowlist_domains || []).join(", ")],
    ["Max retries", num(p.max_retries)],
    ["Snapshots without progress", num(p.max_snapshots_without_progress)],
    ["Identical checkpoints before stall", num(p.min_identical_checkpoints_for_stall || 4)],
    ["Pauses before auto-terminate", num(p.max_pauses_before_terminate)],
    ["Max estimated tokens per task", num(p.max_estimated_tokens_per_task)],
    ["Kill switch", p.kill_switch ? "ENGAGED" : "off"],
  ];
  const card = el("div", "card");
  card.innerHTML = `<h3>Deterministic policy — the hard gate. The agent cannot modify these.</h3>`;
  const t = el("table");
  t.innerHTML = "<tbody></tbody>";
  const tb = t.querySelector("tbody");
  rows.forEach(([k, v]) => {
    const tr = el("tr");
    tr.innerHTML = `<td class="plain">${esc(k)}</td><td>${v}</td>`;
    tb.append(tr);
  });
  card.append(t);
  view.append(card);
}

/* ---------- Audit ---------- */
function renderAudit(data) {
  const view = document.getElementById("view");
  view.innerHTML = "";
  const card = el("div", "card");
  card.innerHTML = `<h3>Append-only audit trail — ${num(data.events.length)} events</h3>`;
  card.append(renderFeed(data.events));
  view.append(card);
}

/* ---------- Reasoning (v2 supervision) ---------- */
function renderReasoning(data) {
  const view = document.getElementById("view");
  view.innerHTML = "";
  const sessions = data.reasoning_sessions || [];
  const instances = data.instances || [];
  const walletFrozen = (data.status && data.status.wallet && data.status.wallet.frozen_sessions) || [];
  const card = el("div", "card");

  // --- connected opencode instances (heartbeat registry, scoped to YOU) ---
  const auth = data.auth || {};
  const icard = el("div", "card");
  icard.innerHTML = `<h3>Your connected opencode instances <span class="muted">(heartbeat)</span></h3>`;
  if (!instances.length) {
    icard.append(el("div", "muted", "No opencode instances registered for this account yet. Open the Connect tab and follow steps 1–3."));
  } else {
    const it = el("table");
    it.innerHTML = `<thead><tr><th>instance</th><th>status</th><th>device</th><th>user</th><th>project</th><th>last seen</th></tr></thead><tbody></tbody>`;
    const tb = it.querySelector("tbody");
    instances.forEach((inst) => {
      const tr = el("tr");
      const age = Math.max(0, (Date.now() - inst.last_seen * 1000));
      const mine = !inst.owner || inst.user_email === auth.email;
      tr.innerHTML = `
        <td class="plain mono">${esc(inst.instance_id)}</td>
        <td>${inst.connected ? `<span class="badge approve">CONNECTED</span>` : `<span class="badge reject">OFFLINE (${Math.round(age / 1000)}s ago)</span>`}</td>
        <td class="plain">${esc(inst.hostname || "—")}</td>
        <td class="plain">${mine ? esc(inst.user_email || "you") : esc(inst.user_email || "—")}</td>
        <td class="plain">${esc(inst.project || "—")}</td>
        <td class="plain">${tKey(new Date(inst.last_seen * 1000).toISOString())}</td>`;
      tb.append(tr);
    });
    icard.append(it);
  }
  view.append(icard);

  card.innerHTML = `<h3>Reasoning supervision — checkpoints + objective signals</h3>
    <p class="muted" style="margin:0 0 10px">The big model publishes context.md after every thinking phase. Each version is stored
    with an embedding and scored against the real tool log. A paused or terminated session has its
    tools blocked and wallet access revoked until the owner acts.</p>
    ${walletFrozen.length ? `<div class="muted" style="margin-bottom:8px">Wallet frozen for: ${walletFrozen.map((s) => `<span class="badge reject">${esc(s)}</span>`).join(" ")}</div>` : ""}`;
  if (!sessions.length) {
    card.append(el("div", "muted", "No checkpoints recorded yet. Start an opencode session with the guardian plugin, or run the demo."));
  } else {
    const auth = data.auth || {};
    const t = el("table");
    t.innerHTML = `<thead><tr><th>session</th><th>owner</th><th>checkpoints</th><th>status</th><th>override</th><th>reason</th><th></th></tr></thead><tbody></tbody>`;
    const tb = t.querySelector("tbody");
    sessions.forEach((s) => {
      const tr = el("tr");
      const overridden = !!s.override;
      const untilClosed = overridden && !!s.override_until_closed;
      const mine = !s.owner || s.owner_email === auth.email || (auth && auth.is_admin);
      const ovBadge = overridden
        ? `<span class="badge approve" title="gate forced open by owner">${untilClosed ? "ALLOWED" : "ALLOWED " + (s.override_minutes || "…") + "m"}</span>`
        : `<span class="muted">—</span>`;
      tr.innerHTML = `
        <td class="plain mono">${esc(s.session_id)}</td>
        <td class="plain">${s.owner ? esc(s.owner_email || s.owner) : '<span class="muted">unowned</span>'}</td>
        <td>${num(s.n)}</td>
        <td>${statusBadge(s.status)}</td>
        <td>${ovBadge}</td>
        <td class="plain muted">${esc(s.reason || "")}</td>
        <td>${mine ? `
          <button class="btn ghost" data-act="trail" data-session="${esc(s.session_id)}">trail</button>
          <button class="btn ok" data-act="allow" data-session="${esc(s.session_id)}">Allow session</button>
          <button class="btn ghost" data-act="allow5" data-session="${esc(s.session_id)}">Allow 5m</button>
          ${overridden ? `<button class="btn danger" data-act="close" data-session="${esc(s.session_id)}">Close</button>` : ""}` : `
          <button class="btn ghost" data-act="trail" data-session="${esc(s.session_id)}">trail</button>`}
        </td>`;
      const trailBtn = tr.querySelector('[data-act="trail"]');
      trailBtn.onclick = () => loadTrail(s.session_id);
      const allowBtn = tr.querySelector('[data-act="allow"]');
      allowBtn.onclick = async () => {
        try { await post("/api/reasoning/override", { session_id: s.session_id, until_closed: true }, ADMIN_TOKEN); refresh(); }
        catch (e) { alert(e.message); }
      };
      const allow5Btn = tr.querySelector('[data-act="allow5"]');
      allow5Btn.onclick = async () => {
        try { await post("/api/reasoning/override", { session_id: s.session_id, minutes: 5 }, ADMIN_TOKEN); refresh(); }
        catch (e) { alert(e.message); }
      };
      const closeBtn = tr.querySelector('[data-act="close"]');
      if (closeBtn) closeBtn.onclick = async () => {
        try { await post("/api/reasoning/clear-override", { session_id: s.session_id }, ADMIN_TOKEN); refresh(); }
        catch (e) { alert(e.message); }
      };
      tb.append(tr);
    });
    card.append(t);
  }
  view.append(card);
  const detail = el("div", "card");
  detail.id = "trail-detail";
  detail.innerHTML = `<h3>Session trail</h3><div class="muted">Select a session to inspect its checkpoint history.</div>`;
  view.append(detail);

  // Owner actions — reset from the UI instead of a curl. The reset clears the
  // audit trail, un-pauses every session and unfreezes the wallet.
  const actions = el("div", "card");
  actions.innerHTML = `<h3>Owner actions</h3>
    <p class="muted" style="margin:0 0 10px">A paused/terminated session blocks its agent's tools and freezes the wallet.
    Only the owner can unblock it. Resetting clears all sessions, the audit trail and the wallet.</p>
    <button class="btn ghost" id="reasoning-reset" style="color:var(--red);border-color:var(--red)">Reset all sessions &amp; demo state</button>`;
  const resetBtn = actions.querySelector("button");
  resetBtn.onclick = async () => {
    if (!confirm("Reset all sessions, audit trail and wallet?")) return;
    try { await post("/api/reset", {}, ADMIN_TOKEN); } catch (e) { alert(e.message); }
    refresh();
  };
  view.append(actions);
}

function signalBadge(name, active) {
  if (!active) return "";
  return `<span class="badge reject">${esc(name).toUpperCase()}</span>`;
}

async function loadTrail(session_id) {
  const box = document.getElementById("trail-detail");
  if (!box) return;
  box.innerHTML = `<h3>Session trail — ${esc(session_id)}</h3><div class="muted">loading…</div>`;
  let trail;
  try {
    const r = await fetch("/api/reasoning?session_id=" + encodeURIComponent(session_id));
    trail = await r.json();
  } catch (e) {
    box.innerHTML = `<h3>Session trail</h3><div class="muted">failed to load: ${esc(e.message)}</div>`;
    return;
  }
  const cps = trail.checkpoints || [];
  box.innerHTML = `<h3>Session trail — <span class="mono">${esc(session_id)}</span></h3>
    <div style="margin-bottom:8px">status: ${statusBadge(trail.status.status)}${trail.status.reason ? " <span class='muted'>— " + esc(trail.status.reason) + "</span>" : ""}</div>`;
  if (!cps.length) {
    box.append(el("div", "muted", "No checkpoints."));
    return;
  }
  cps.forEach((cp) => {
    const sigs = (() => { try { return JSON.parse(cp.signals); } catch { return {}; } })();
    const verdict = (() => { try { return JSON.parse(cp.verdict); } catch { return {}; } })();
    const row = el("div", "ev");
    const contradictions = (sigs.contradiction || []).map((c) =>
      `<div class="muted">claim: “${esc(c.claim)}” · tool ${esc(c.tool)} → ${esc(c.output)}</div>`).join("");
    const repeats = (sigs.repeat_strategy || []).map((r) => {
      if (r.count) return `<div class="muted">same <b>${esc(r.tool)}</b> repeated ${r.count}x</div>`;
      return `<div class="muted">retried ${esc(r.tool)} after earlier ${esc(r.earlier_failure)} failure</div>`;
    }).join("");
    const waste = verdict.waste;
    const wasteRow = waste ? `<div class="msg" style="grid-column:2/4;color:var(--red)">
      ⏱ ${waste.elapsed_s}s · ~${num(waste.tokens_burned_est)} tokens · ${waste.repeats} repeats<br>
      ${(waste.evidence || []).map((e) => `· ${esc(e)}`).join("<br>")}
    </div>` : "";
    const badges =
      signalBadge("contradiction", sigs.contradiction && sigs.contradiction.length) +
      signalBadge("stall", sigs.stall) +
      signalBadge("churn", sigs.churn) +
      signalBadge("drift", sigs.drift) +
      signalBadge("repeat", sigs.repeat_strategy && sigs.repeat_strategy.length);
    const stateCls = verdict.state === "terminated" ? "bad" : verdict.state === "paused" ? "warn" : "ok";
    row.innerHTML = `<span class="ts">${tKey(cp.ts)}</span>
      <span class="tag ${stateCls}">${esc(verdict.state || "running").toUpperCase()}</span>
      <span class="msg">${badges || '<span class="muted">on track</span>'}</span>
      ${contradictions ? `<div class="msg" style="grid-column:2/4;color:var(--red)">${contradictions}</div>` : ""}
      ${repeats ? `<div class="msg" style="grid-column:2/4">${repeats}</div>` : ""}
      ${wasteRow}`;
    box.append(row);
  });
}

/* ---------- shared event feed ---------- */
function renderFeed(events) {
  const feed = el("div", "feed");
  events.forEach((ev) => feed.append(renderEvent(ev)));
  return feed;
}

function renderEvent(ev) {
  const row = el("div", "ev");
  let tag = "system", cls = "info", msg = "";
  const i = ev.intent || {};
  switch (ev.type) {
    case "system":
      tag = "system"; cls = "info";
      msg = `${ev.event || "system"}${ev.reason ? " — " + ev.reason : ""}${ev.spend_saved != null ? " (spend so far $" + ev.spend_saved + ")" : ""}${ev.judge ? " · judge: " + ev.judge : ""}`;
      break;
    case "agent_action":
      tag = "agent"; cls = "info";
      msg = `<b>${esc(ev.step || "")}</b> — ${esc(ev.detail || "")}`;
      break;
    case "intent_received":
      tag = "intent"; cls = "info";
      msg = `${esc(i.task || "")} · <b>${esc(i.recipient)}</b> · ${money(i.amount)} ${esc(i.currency)} · reason: "${esc(i.reason || "")}" · est ${num(i.estimated_tokens)} tokens`;
      break;
    case "judge_review":
      tag = "judge"; cls = ev.recommendation === "reject" ? "warn" : "info";
      msg = `${esc(ev.recommendation).toUpperCase()} (${Math.round(Number(ev.confidence) * 100)}%) · ${esc(ev.reason)}${ev.drift ? " · DRIFT FLAG" : ""}${ev.loop_suspect ? " · LOOP SUSPECT" : ""} · ${esc(ev.source || "")}`;
      break;
    case "policy_verdict":
      tag = "policy"; cls = ev.decision === "approve" ? "info" : "warn";
      msg = ev.decision === "approve"
        ? "all rules pass"
        : "blocked: " + (ev.reasons || []).join(" · ");
      break;
    case "decision":
      tag = "decision-" + ev.decision; cls = ev.decision === "approve" ? "ok" : "bad";
      msg = `<b>${ev.decision.toUpperCase()}</b> · ${esc(ev.source)} · ${esc(ev.reason)}${ev.approval_id ? " · " + ev.approval_id : ""}`;
      break;
    case "wallet_result":
      tag = "wallet"; cls = ev.executed ? "ok" : "bad";
      msg = ev.executed
        ? `<b>EXECUTED</b> ${money(ev.amount)} → ${esc(ev.recipient)} · balance ${money(ev.balance)} · ${esc(ev.ledger_id)}`
        : `<b>REFUSED</b> — ${esc(ev.reason)}${ev.note ? " · " + esc(ev.note) : ""}`;
      break;
    case "snapshot":
      tag = "snapshot"; cls = "info";
      msg = `step: ${esc(ev.snapshot?.current_step || "")} · tool: ${esc(ev.snapshot?.current_tool || "—")} · ${num(ev.snapshot?.tokens || 0)} tokens`;
      break;
    case "loop_detector":
      tag = "loop"; cls = "bad";
      msg = `<b>${esc(ev.state).toUpperCase()}</b> · stall ${num(ev.stall_count)} · ${esc(ev.reason || "")}`;
      break;
    case "retry":
      tag = "retry"; cls = "warn";
      msg = `retry #${num(ev.retries)}`;
      break;
    case "bypass_attempt":
      tag = "bypass"; cls = "bad";
      msg = `forged approval "${esc(ev.forged_approval_id || "")}" sent straight to the wallet`;
      break;
    case "reasoning_checkpoint":
      tag = "reasoning"; cls = ev.verdict_state === "paused" || ev.verdict_state === "terminated" ? "bad" : "info";
      msg = `checkpoint #${num(ev.checkpoint_id)} · ${esc(ev.verdict_state || "running").toUpperCase()}${ev.reason ? " — " + esc(ev.reason) : ""}` +
        `${ev.contradiction ? " · CONTRADICTION" : ""}${ev.stall ? " · STALL" : ""}${ev.churn ? " · CHURN" : ""}${ev.drift ? " · DRIFT" : ""}`;
      break;
    case "reasoning_tool":
      tag = "tool"; cls = ev.status === "error" ? "bad" : "info";
      msg = `${esc(ev.tool)} → ${esc(ev.status === "error" ? ev.output : "ok")}`;
      if (ev.detail && ev.status === "error") msg += ` <span class="dim">(${esc(ev.detail)})</span>`;
      break;
    case "reasoning_verdict":
      tag = "judge"; cls = ev.state === "paused" || ev.state === "terminated" ? "warn" : "info";
      msg = `<b>${esc(ev.state).toUpperCase()}</b> · contradiction: ${ev.contradiction} · ${esc(ev.reason)}`;
      break;
    case "topup":
      tag = "owner"; cls = "ok";
      msg = `top-up +${money(ev.amount)} → balance ${money(ev.balance)}${ev.session_id ? " · " + esc(ev.session_id) : ""}`;
      break;
    case "kill_switch":
      tag = "kill"; cls = ev.active ? "bad" : "info";
      msg = ev.active ? "ENGAGED — all spending frozen" : "released";
      break;
    default:
      tag = ev.type; cls = "info"; msg = JSON.stringify(ev);
  }
  row.innerHTML = `<span class="ts">${tKey(ev.ts)}</span><span class="tag ${cls}">${esc(tag).toUpperCase()}</span><span class="msg">${msg}</span>`;
  row.classList.add(cls);
  return row;
}
