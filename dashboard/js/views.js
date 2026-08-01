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
