"use strict";
/* main.js — bootstrap, polling, tab routing, demo controls. Loads last. */

const TABS = {
  overview: { title: "Overview", render: renderOverview },
  requests: { title: "Requests", render: renderRequests },
  snapshots: { title: "Snapshots", render: renderSnapshots },
  reasoning: { title: "Reasoning", render: renderReasoning },
  connect: { title: "Connect", render: renderConnect },
  wallet: { title: "Wallet", render: renderWallet },
  policy: { title: "Policy", render: renderPolicy },
  audit: { title: "Audit", render: renderAudit },
};

const BEATS = {
  approve: { label: "Legit purchase", sub: "OpenAI credits → approved", icon: "✅" },
  reject: { label: "Policy violation", sub: "$500 → unknown wallet → blocked", icon: "🚫" },
  bypass: { label: "Direct wallet bypass", sub: "forged token → wallet refuses", icon: "🛡️" },
  loop: { label: "Retry loop", sub: "stuck agent → auto-terminated", icon: "🔁" },
  kill: { label: "Kill switch", sub: "owner freezes everything", icon: "⛔" },
  hallucinate: { label: "Hallucinated claim", sub: "context.md lies → judge pauses", icon: "🧠" },
  recover: { label: "Owner top-up + resume", sub: "review → top up → continue", icon: "🔓" },
};

let DATA = null;
let ADMIN_TOKEN = localStorage.getItem("guardian_token") || "demo";
let SESSION_TOKEN = localStorage.getItem("guardian_session") || "";
let BUSY = false;
let LAST_SIG = null;

function $id(x) { return document.getElementById(x); }

// fetch with auth headers (session token + optional admin token).
async function fetchAuth(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (SESSION_TOKEN) headers["X-Session-Token"] = SESSION_TOKEN;
  return fetch(path, Object.assign({}, opts, { headers }));
}

function postAuth(path, body, useAdmin) {
  return post(path, body, useAdmin ? ADMIN_TOKEN : null);
}

// Lightweight fingerprint of the payload — only re-render when the state we
// show actually changed. Without this, the audit trail and statusbar rebuild
// every poll, which flashes the whole view every ~1s and looks like an alarm.
function payloadSig(d) {
  const st = d.status.state;
  return [
    st.status,
    st.budget_remaining, st.spent_today,
    st.approvals, st.rejections, st.retries, st.violations,
    d.status.wallet.balance,
    d.cursor,
    d.mode || "enforce",
    (d.instances || []).map((i) => i.connected + ":" + (i.last_seen | 0)).join(","),
    (d.reasoning_sessions || []).map((s) => (s.override ? 1 : 0) + ":" + s.status).join(","),
    (d.auth && d.auth.email) || "",
  ].join("|");
}

async function refresh() {
  try {
    const r = await fetchAuth("/api/data");
    if (r.status === 401) {
      SESSION_TOKEN = "";
      localStorage.removeItem("guardian_session");
    }
    DATA = await r.json();
  } catch (e) {
    $id("sub").textContent = "offline — " + e.message;
    return;
  }
  const auth = DATA.auth || {};
  if (!auth.authenticated) {
    renderAuth();
    return;
  }
  $id("auth-screen").hidden = true;
  $id("account-link").textContent = auth.is_admin ? auth.email + " · admin" : auth.email;
  $id("account-link").href = "#connect";
  // If the server runs the default token ("demo"), a stale token saved in
  // localStorage (e.g. from an earlier custom setup) must not override it —
  // otherwise every admin call 403s with "invalid admin token".
  if (!DATA.admin_required && ADMIN_TOKEN !== "demo") {
    ADMIN_TOKEN = "demo";
    localStorage.removeItem("guardian_token");
  }
  $id("admin-row").hidden = !DATA.admin_required || !auth.is_admin;
  const sig = payloadSig(DATA);
  if (sig === LAST_SIG) return; // nothing changed — keep the current view static
  LAST_SIG = sig;
  renderStatusbar();
  renderModeControl();
  // A bug in one view must never blank the whole dashboard center: render
  // defensively and surface the error in the view instead of dying silently.
  try {
    route();
  } catch (e) {
    console.error("[guardian-dashboard] render failed:", e);
    const v = $id("view");
    if (v) v.innerHTML = `<div class="card"><h3>Render error</h3><div class="muted">${esc((e && e.message) || String(e))}</div></div>`;
  }
}

function renderStatusbar() {
  if (!DATA) return;
  const st = DATA.status.state;
  const w = DATA.status.wallet;
  const p = DATA.status.policy;
  const kill = !!p.kill_switch;
  const connected = (DATA.instances || []).filter((i) => i.connected).length;
  const bar = $id("statusbar");
  bar.innerHTML = "";
  const pills = [
    pill("workflow", statusBadge(st.status), ""),
    pill("mode", DATA.mode || "enforce", DATA.mode === "enforce" ? "amber" : "brand"),
    pill("agents connected", num(connected), connected ? "green" : "muted"),
    pill("budget left", money(st.budget_remaining), "brand"),
    pill("spent today", money(st.spent_today), "green"),
    pill("balance", money(w.balance), "brand"),
    pill("approvals", num(st.approvals), "green"),
    pill("rejections", num(st.rejections), "red"),
    pill("retries", num(st.retries), "amber"),
    pill("kill switch", kill ? "ENGAGED" : "off", kill ? "red" : "muted"),
  ];
  pills.forEach((p2) => bar.append(p2));
  $id("n-req").textContent = st.approvals + st.rejections;
}

function renderModeControl() {
  const sel = $id("mode-select");
  const hint = $id("mode-hint");
  const row = $id("mode-row");
  if (!sel) return;
  const auth = (DATA && DATA.auth) || {};
  // Mode is global (affects every user) — only admins can change it.
  if (row) row.hidden = !auth.is_admin;
  const mode = DATA ? DATA.mode || "enforce" : "enforce";
  sel.value = mode;
  const hints = {
    enforce: "paused/terminated sessions block spendy tools",
    watch: "never blocks — advisory only",
    ask: "blocks on pause; Allow session (until you close it) opens the gate",
  };
  if (hint) hint.textContent = hints[mode] || "";
}

function route() {
  const hash = location.hash.replace("#", "") || "overview";
  const tab = TABS[hash] ? hash : "overview";
  document.querySelectorAll("#nav a").forEach((a) => {
    a.classList.toggle("active", a.dataset.v === tab);
  });
  $id("title").textContent = TABS[tab].title;
  $id("sub").textContent = DATA ? "generated " + tKey(DATA.generated_at) + " · judge: " + DATA.status.judge : "";
  if (DATA) TABS[tab].render(DATA);
}

function buildBeatButtons() {
  const box = $id("beat-buttons");
  box.innerHTML = "";
  Object.entries(BEATS).forEach(([name, b]) => {
    const btn = el("button", "btn");
    btn.id = "beat-" + name;
    btn.innerHTML = `<span>${b.icon} ${esc(b.label)}<div class="k">${esc(b.sub)}</div></span>`;
    btn.onclick = () => runBeat(name);
    box.append(btn);
  });
}

async function runBeat(name) {
  if (BUSY) return;
  BUSY = true;
  setBusy(true);
  try {
    await post("/api/beat/" + name, {}, ADMIN_TOKEN);
  } catch (e) {
    alert("beat failed: " + e.message);
  } finally {
    BUSY = false;
    setBusy(false);
    refresh();
  }
}

async function runFullDemo() {
  if (BUSY) return;
  BUSY = true;
  setBusy(true);
  try {
    await post("/api/reset", {}, ADMIN_TOKEN);
    for (const name of ["approve", "reject", "bypass", "loop", "hallucinate", "recover", "kill"]) {
      await post("/api/beat/" + name, {}, ADMIN_TOKEN);
      await sleep(350);
    }
  } catch (e) {
    alert("demo failed: " + e.message);
  } finally {
    BUSY = false;
    setBusy(false);
    refresh();
  }
}

function setBusy(b) {
  document.querySelectorAll("#dock .btn").forEach((x) => {
    x.disabled = b;
    if (b) x.style.opacity = 0.5; else x.style.opacity = "";
  });
}

function wireControls() {
  $id("btn-alldemo").onclick = runFullDemo;
  $id("btn-kill").onclick = async () => {
    try { await post("/api/kill", { active: true }, ADMIN_TOKEN); refresh(); }
    catch (e) { alert(e.message); }
  };
  $id("btn-unkill").onclick = async () => {
    try { await post("/api/kill", { active: false }, ADMIN_TOKEN); refresh(); }
    catch (e) { alert(e.message); }
  };
  $id("btn-reset").onclick = async () => {
    if (!confirm("Reset the demo (clears the audit trail and wallet)?")) return;
    try { await post("/api/reset", {}, ADMIN_TOKEN); refresh(); }
    catch (e) { alert(e.message); }
  };
  $id("btn-save-token").onclick = () => {
    ADMIN_TOKEN = $id("admin-token").value.trim() || "demo";
    localStorage.setItem("guardian_token", ADMIN_TOKEN);
    alert("admin token saved");
  };
  $id("btn-set-mode").onclick = async () => {
    const mode = $id("mode-select").value;
    try { await post("/api/reasoning/mode", { mode }, ADMIN_TOKEN); refresh(); }
    catch (e) { alert(e.message); }
  };
  $id("account-link").onclick = (ev) => {
    ev.preventDefault();
    logout();
  };
}

async function logout() {
  try { await fetchAuth("/api/auth/logout", { method: "POST" }); } catch {}
  SESSION_TOKEN = "";
  localStorage.removeItem("guardian_session");
  DATA = null;
  LAST_SIG = null;
  location.hash = "#overview";
  refresh();
}

function boot() {
  buildBeatButtons();
  wireControls();
  window.addEventListener("hashchange", route);
  refresh();
  setInterval(refresh, 4000);
}

boot();
