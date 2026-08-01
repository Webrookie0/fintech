"use strict";
/* util.js — tiny helpers, shared global scope (no modules, no build step). */

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function money(n) {
  if (n == null || isNaN(n)) return "$0.00";
  return "$" + Number(n).toFixed(2);
}

function num(n) {
  if (n == null) return "0";
  return Number(n).toLocaleString();
}

function esc(s) {
  if (s == null) return "";
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}

function tsShort(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour12: false });
}

function tKey(iso) {
  return new Date(iso).toISOString().slice(11, 19);
}

function post(path, body, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers["X-Admin-Token"] = token;
  return fetch(path, {
    method: "POST",
    headers,
    body: body ? JSON.stringify(body) : "{}",
  }).then(async (r) => {
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || ("HTTP " + r.status));
    return data;
  });
}

function sleep(ms) {
  return new Promise((res) => setTimeout(res, ms));
}
