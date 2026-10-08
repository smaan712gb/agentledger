/* AgentLedger web app — no build step. Everything rendered here is escaped: document text and AI
   output are untrusted. */
"use strict";

const S = { token: null, me: null, users: [], clients: [], dev: false };
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v) => { const n = Number(v); return isFinite(n) ? n.toLocaleString("en-US", { style: "currency", currency: "USD" }) : esc(v); };
const sevPill = (s) => `<span class="pill ${({ critical: "p-bad", high: "p-bad", medium: "p-warn", low: "p-info", info: "p-mute" })[s] || "p-mute"}">${esc(s)}</span>`;
const riskPill = (r) => `<span class="pill ${({ low: "p-good", medium: "p-warn", high: "p-bad", critical: "p-bad" })[r] || "p-mute"}">${esc(r)} risk</span>`;
const statusPill = (s) => `<span class="pill ${({ adopted: "p-good", pending: "p-warn", rejected: "p-mute", rolled_back: "p-mute", failed: "p-bad", open: "p-warn", resolved: "p-good", filed: "p-good", needs_review: "p-warn" })[s] || "p-mute"}">${esc(String(s).replace("_", " "))}</span>`;
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
// Real session tokens live only for the browser tab (sessionStorage); demo identities may persist.
const session = { get() { try { return sessionStorage.getItem("agentledger.session"); } catch { return null; } },
  set(v) { try { v ? sessionStorage.setItem("agentledger.session", v) : sessionStorage.removeItem("agentledger.session"); } catch {} } };

function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 4200); }

async function api(path, opts = {}) {
  const headers = { Authorization: `Bearer ${S.token}`, ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}) };
  const res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) },
    body: opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (res.status === 401 && !S.dev && !path.startsWith("/api/auth/")) { session.set(null); S.token = null; renderLogin("Your session ended. Sign in again."); throw new Error("signed out"); }
  if (!res.ok) {
    let d = await res.text(); try { d = JSON.parse(d).detail; } catch {}
    // Approving, reconciling, closing books and creating firms need a recent sign-in: verify, then retry once.
    if (res.status === 403 && d === "step_up_required" && !opts._steppedUp) { await stepUp(); return api(path, { ...opts, _steppedUp: true }); }
    throw new Error(typeof d === "string" ? d : JSON.stringify(d) || res.statusText);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

// ---------------------------------------------------------------------------------------- shell
async function boot() {
  const r = await fetch("/api/users");
  S.dev = r.ok;
  try { S.identity = (await (await fetch("/api/auth/config")).json()).identity; } catch { S.identity = "local"; }
  // Back from the sign-in provider: the session arrives in the URL fragment (never sent to a server); clear it at once.
  const back = location.hash.match(/^#(session|signin_error|step_up|link)=(.*)$/);
  if (back) {
    history.replaceState(null, "", location.pathname + "#/home");
    if (back[1] === "session") session.set(back[2]);
    if (back[1] === "signin_error") return renderLogin(decodeURIComponent(back[2]));
    if (back[1] === "step_up") toast("Verified. Repeat the action to continue.");
    if (back[1] === "link") toast("Your sign-in provider is now linked to this account.");
  }
  window.addEventListener("hashchange", route);
  const invite = location.hash.match(/^#\/accept\/([\w-]+)/);
  if (invite) return renderAccept(invite[1]);
  if (S.dev) {
    S.users = await r.json();
    S.token = store.get("agentledger.token") || S.users[0].token;
  } else {
    S.token = session.get();
    if (!S.token) return renderLogin();
  }
  await loadMe();
  route();
}

async function loadMe() {
  try { S.me = await api("/api/me"); } catch (e) { if (!S.dev) return; S.token = S.users[0].token; S.me = await api("/api/me"); }
  S.clients = S.me.base_role === "platform_admin" ? [] : await api("/api/clients");
  renderNav();
}

// ---------------------------------------------------------------------------------------- sign-in
function authShell(inner) {
  $("#nav").innerHTML = `<div class="brand"><div class="mark">V</div><div>AgentLedger<small>Autonomous accounting & tax</small></div></div>`;
  $("#main").innerHTML = `<div class="card" style="max-width:420px;margin:60px auto">${inner}</div>`;
}

async function providerSignIn(purpose, extra = {}) {
  const q = new URLSearchParams({ purpose, ...extra });
  const r = await api(`/api/auth/idp/start?${q}`);
  location.assign(r.url);
}

function renderLogin(msg = "") {
  const idp = S.identity === "workos";
  const form = `<form id="login"><label>Email<input name="email" type="email" autocomplete="username" required style="width:100%"></label>
    <label>Password<input name="password" type="password" autocomplete="current-password" required style="width:100%"></label>
    <button class="btn ${idp ? "" : "primary"}" style="margin-top:10px">Continue</button></form>`;
  authShell(`<h2>Sign in</h2>${msg ? `<p class="muted">${esc(msg)}</p>` : ""}
    ${idp ? `<button class="btn primary" id="idp" style="width:100%">Sign in</button>
      <p class="small muted">Two-step verification or a passkey is required.</p>
      <details style="margin-top:14px"><summary class="small muted">Platform administrator sign-in</summary>${form}</details>` : form}
    <p class="small muted" id="err"></p>`);
  if (idp) $("#idp").onclick = () => providerSignIn("login").catch((err) => { $("#err").textContent = err.message; });
  $("#login").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { mfaStep(await api("/api/auth/login", { method: "POST", body: { email: f.get("email"), password: f.get("password") } })); }
    catch (err) { $("#err").textContent = err.message; }
  };
}

function mfaStep(step) {
  const enrol = step.next === "enroll";
  authShell(`<h2>${enrol ? "Set up two-step verification" : "Two-step verification"}</h2>
    ${enrol ? `<p>Add AgentLedger to your authenticator app, then enter the 6-digit code it shows.</p>
      <p><a href="${esc(step.otpauth_uri)}">Open in authenticator app</a></p>
      <p class="small muted">Or enter this key manually:</p><p><code style="font-size:15px;letter-spacing:1px">${esc(step.secret.replace(/(.{4})/g, "$1 ").trim())}</code></p>`
      : `<p>Enter the 6-digit code from your authenticator app.</p>`}
    <form id="mfa"><input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="\\d{6}" required style="width:100%;font-size:20px;letter-spacing:6px">
    <button class="btn primary" style="margin-top:10px">Verify</button></form><p class="small muted" id="err"></p>`);
  $("#mfa").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/auth/mfa", { method: "POST", body: { challenge: step.challenge, code: new FormData(e.target).get("code") } });
      S.token = r.token; session.set(r.token); location.hash = "#/home"; await loadMe(); route();
    } catch (err) { $("#err").textContent = err.message; }
  };
}

function renderAccept(token) {
  if (S.identity === "workos") {
    authShell(`<h2>Join AgentLedger</h2><p class="muted">Continue with the email address this invitation was sent to.
      Two-step verification or a passkey is required.</p>
      <button class="btn primary" id="idp" style="width:100%">Continue</button><p class="small muted" id="err"></p>`);
    $("#idp").onclick = () => providerSignIn("invite", { invite: token }).catch((err) => { $("#err").textContent = err.message; });
    return;
  }
  authShell(`<h2>Join AgentLedger</h2><p class="muted">Choose your name and a password of at least 12 characters.</p>
    <form id="accept"><label>Full name<input name="name" required style="width:100%"></label>
    <label>Password<input name="password" type="password" autocomplete="new-password" minlength="12" required style="width:100%"></label>
    <button class="btn primary" style="margin-top:10px">Continue</button></form><p class="small muted" id="err"></p>`);
  $("#accept").onsubmit = async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try { mfaStep(await api("/api/auth/accept", { method: "POST", body: { token, name: f.get("name"), password: f.get("password") } })); }
    catch (err) { $("#err").textContent = err.message; }
  };
}

// Step-up: provider accounts re-authenticate at the provider (and repeat the action on return); local accounts enter
// their one-time code here and the action is retried.
async function stepUp() {
  if (S.identity === "workos" && S.me && S.me.auth_method && S.me.auth_method.startsWith("workos:")) {
    await providerSignIn("step_up");
    throw new Error("verifying your sign-in");
  }
  const code = await new Promise((resolve, reject) => {
    const d = document.createElement("div");
    d.className = "modal";
    d.innerHTML = `<div class="card" style="max-width:360px;margin:15vh auto"><h3>Confirm it's you</h3>
      <p class="small muted">This action needs a recent sign-in. Enter the 6-digit code from your authenticator app.</p>
      <form><input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="\\d{6}" required style="width:100%;font-size:20px;letter-spacing:6px">
      <button class="btn primary" style="margin-top:10px">Verify</button> <button type="button" class="btn" data-cancel>Cancel</button></form></div>`;
    document.body.appendChild(d);
    d.querySelector("[data-cancel]").onclick = () => { d.remove(); reject(new Error("verification cancelled")); };
    d.querySelector("form").onsubmit = (e) => { e.preventDefault(); const v = new FormData(e.target).get("code"); d.remove(); resolve(v); };
  });
  await api("/api/auth/step-up", { method: "POST", body: { code } });
}

async function signOut() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch {}
  session.set(null); S.token = null; location.hash = ""; renderLogin("Signed out.");
}

// Downloads use a short-lived signed link so the session token never appears in a URL.
async function download(path) {
  const r = await api("/api/links", { method: "POST", body: { path } });
  window.open(r.url, "_blank", "noopener");
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-download]");
  if (!a) return;
  e.preventDefault();
  download(a.dataset.download).catch((err) => toast(err.message));
});

function renderNav() {
  if (!S.me) return;
  const cpa = S.me.role === "cpa";
  const platform = S.me.base_role === "platform_admin";
  const items = platform ? [
    ["firms", "▦", "Firms"], ["group", "", "Shared platform"], ["foundry", "⚙", "Agent foundry"], ["rules", "§", "Regulations"],
    ["security", "⛨", "Sign-in activity"],
  ] : cpa ? [
    ["home", "◎", "Command center"], ["ask", "✦", "Ask AgentLedger"], ["clients", "▦", "Clients"], ["inbox", "⇩", "Intake inbox"],
    ["crm", "◇", "CRM & tasks"], ["group", "", "Autonomy"], ["foundry", "⚙", "Agent foundry"], ["rules", "§", "Regulations"],
    ["brain", "✧", "CPA second brain"], ["integrations", "⇄", "Integrations"], ["audit", "⛓", "Audit trail"],
    ["group", "", "Firm"], ["team", "☺", "Team & access"], ["security", "⛨", "Sign-in activity"],
  ] : [
    ["home", "◎", "My business"], ["ask", "✦", "Ask AgentLedger"], ["client/" + S.me.client_id + "/documents", "⇩", "Upload documents"],
    ["client/" + S.me.client_id + "/business", "◇", "Customers & vendors"], ["audit", "⛓", "Activity"],
  ];
  const h = location.hash.slice(2) || "home";
  $("#nav").innerHTML = `<div class="brand"><div class="mark">V</div><div>AgentLedger<small>Autonomous accounting & tax</small></div></div>` +
    items.map(([id, ic, label]) => id === "group" ? `<div class="group">${label}</div>` :
      `<a class="item ${h.startsWith(id) ? "active" : ""}" href="#/${id}"><span>${ic}</span>${label}</a>`).join("") +
    (S.dev ? `<div class="group">Signed in</div><div style="padding:0 8px"><select id="who" style="width:100%">${S.users.map((u) =>
      `<option value="${esc(u.token)}" ${u.token === S.token ? "selected" : ""}>${esc(u.name)} · ${esc(u.role)}</option>`).join("")}</select>
      <div class="small muted" style="margin-top:6px">Dev mode: demo identities. Never use with real client data.</div></div>`
      : `<div class="group">Signed in</div><div style="padding:0 8px"><div>${esc(S.me.name)}</div><div class="small muted">${esc(S.me.email)} · ${esc(S.me.base_role || S.me.role)}</div>
      <button class="btn" id="signout" style="margin-top:8px">Sign out</button></div>`);
  if (S.dev) $("#who").onchange = async (e) => { S.token = e.target.value; store.set("agentledger.token", S.token); await loadMe(); location.hash = "#/home"; route(); };
  else $("#signout").onclick = signOut;
}

function page(title, sub, right = "") {
  return `<header class="top"><div><h1>${title}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div><div class="right">${right}</div></header>`;
}

async function route() {
  renderNav();
  if (!S.me) return;
  const [view, ...rest] = (location.hash.slice(2) || (S.me.base_role === "platform_admin" ? "firms" : "home")).split("/");
  const main = $("#main");
  main.innerHTML = `<div class="empty">Loading…</div>`;
  try {
    const fn = { home: viewHome, ask: viewAsk, clients: viewClients, client: viewClient, inbox: viewInbox, foundry: viewFoundry,
      rules: viewRules, brain: viewBrain, crm: viewCrm, integrations: viewIntegrations, audit: viewAudit,
      firms: viewFirms, team: viewTeam, security: viewSecurity }[view] || (S.me.base_role === "platform_admin" ? viewFirms : viewHome);
    await fn(main, ...rest);
  } catch (e) { main.innerHTML = page("Something went wrong", "") + `<div class="card">${esc(e.message)}</div>`; }
}

// ---------------------------------------------------------------------------------------- home
async function viewHome(main) {
  const d = await api("/api/dashboard");
  if (S.me.role === "client") return viewClientHome(main, d);
  const ai = d.ai || {};
  const roles = Object.entries(ai.roles || {});
  main.innerHTML = page("Command center", `Today is ${esc(d.today)} · knowledge base v${esc(d.kb.version)} · ${d.kb.rules} live rules`) + `
  <div class="hero" style="margin-bottom:16px"><div style="font-size:30px">✦</div><div style="flex:1"><h2>Your AI workforce is on duty.</h2>
    <p>${d.runs.length} recent agent runs · ${d.pending.length} change(s) awaiting your judgment · ${d.review_queue} document(s) to file · ${d.tasks.length} open task(s).</p></div>
    <a class="btn" href="#/ask">Ask anything</a><a class="btn" href="#/foundry">Review changes</a></div>
  <div class="grid g4" style="margin-bottom:16px">
    ${d.clients.map((c) => `<a class="card" href="#/client/${esc(c.id)}/overview" style="color:inherit">
      <div class="row"><div><b>${esc(c.name)}</b><div class="small muted">${esc(c.domain.replace("_", " "))} · ${esc(c.kind)}</div></div><div class="spacer"></div>
      <div class="ring" style="--v:${c.integrity};--c:${c.integrity > 80 ? "var(--good)" : c.integrity > 50 ? "var(--warn)" : "var(--bad)"}"><span>${c.integrity}</span></div></div>
      <div class="row small" style="margin-top:8px">${c.open_findings ? `<span class="pill p-warn">${c.open_findings} finding(s)</span>` : `<span class="pill p-good">clean</span>`}
      <span class="pill p-mute">${c.open_tasks} task(s)</span><span class="pill p-mute">${c.docs_this_month} doc(s) this month</span></div></a>`).join("")}
  </div>
  <div class="grid g3">
    <div class="card"><h3>⚖ Changes awaiting your judgment <a class="small" href="#/foundry" style="margin-left:auto">all</a></h3>
      <div class="list">${d.pending.map((p) => `<div><div class="row"><b>${esc(p.title)}</b></div><div class="row small">${riskPill(p.risk)}<span class="pill p-mute">${esc(p.kind.replace("_", " "))}</span><span class="muted">by ${esc(p.agent)}</span></div></div>`).join("") || `<div class="empty">Nothing waiting. Routine changes were adopted automatically.</div>`}</div></div>
    <div class="card"><h3>⏳ Regulation radar</h3><div class="list">${d.staleness.slice(0, 8).map((a) => `<div><div class="row">${sevPill(a.severity)}<b>${esc(a.title)}</b></div>
      <div class="small muted">${esc(a.status.replace("_", " "))}${a.tax_year ? " · tax year " + a.tax_year : ""} · ${a.days >= 0 ? "due in " + a.days + " days" : Math.abs(a.days) + " days late"} · ${esc(a.publication)}</div></div>`).join("") || `<div class="empty">Every parameter is current.</div>`}</div></div>
    <div class="card"><h3>✔ Recently adopted</h3><div class="list">${d.adopted.map((p) => `<div><b>${esc(p.title)}</b><div class="small muted">${riskPill(p.risk)} ${p.decision ? (p.decision.mode === "auto" ? "auto-adopted under policy" : "approved by " + esc(p.decision.by)) : ""}</div></div>`).join("") || `<div class="empty">No adoptions yet.</div>`}</div></div>
    <div class="card"><h3>☑ Your tasks</h3><div class="list">${d.tasks.map(taskRow).join("") || `<div class="empty">All clear.</div>`}</div></div>
    <div class="card"><h3>🧠 Model roles <a class="small" href="#/foundry/models" style="margin-left:auto">details</a></h3>
      <div class="small row" style="margin-bottom:6px"><span class="dot ${ai.local?.available ? "on" : "off"}"></span> Local open-source ${ai.local?.available ? "online" : "offline"}
        <span class="dot ${ai.frontier?.available ? "on" : "off"}" style="margin-left:8px"></span> Frontier ${ai.frontier?.calls_today ?? 0}/${ai.frontier?.daily_budget ?? 0} calls today</div>
      <table>${roles.map(([r, m]) => `<tr><td>${esc(r)}</td><td><span class="pill ${m.tier === "local" ? "p-good" : "p-accent"}">${esc(m.tier)}</span></td><td class="mono">${esc(m.model)}</td></tr>`).join("")}</table></div>
    <div class="card"><h3>⚙ Agent activity</h3><div class="list">${d.runs.map((r) => `<div class="row small"><span class="dot ${r.ok ? "on" : "off"}"></span><b>${esc(r.agent)}</b>
      <span class="muted">${esc(r.started_at.slice(5, 16).replace("T", " "))}</span><span class="spacer"></span>${r.proposals.length ? `<span class="pill p-accent">${r.proposals.length} proposal(s)</span>` : ""}${r.alerts.length ? `<span class="pill p-warn">${r.alerts.length} alert(s)</span>` : ""}</div>`).join("") || `<div class="empty">No runs yet.</div>`}</div></div>
  </div>`;
  bindTasks(main);
}

async function viewClientHome(main, d) {
  const c = d.clients[0];
  main.innerHTML = page(`Welcome, ${esc(S.me.name.split(" ")[0])}`, esc(c.name)) + `
  <div class="hero" style="margin-bottom:16px"><div style="font-size:30px">✦</div><div style="flex:1"><h2>Your books are being kept for you.</h2>
    <p>Forward receipts and tax forms, or drop them here. AgentLedger files them, matches them and tells you — and your CPA — exactly what's missing.</p></div>
    <a class="btn" href="#/client/${esc(c.id)}/documents">Upload</a><a class="btn" href="#/ask">Ask a question</a></div>
  <div class="grid g3">
    <div class="card"><h3>Integrity</h3><div class="row"><div class="ring" style="--v:${c.integrity}"><span>${c.integrity}</span></div>
      <div class="small muted">Shared score you and your CPA both see. Open items lower it; explaining or fixing them raises it.</div></div></div>
    <div class="card" style="grid-column: span 2"><h3>What we need from you</h3><div class="list">${d.tasks.map(taskRow).join("") || `<div class="empty">Nothing right now. 🎉</div>`}</div></div>
  </div>
  <div style="margin-top:16px"><a class="btn" href="#/client/${esc(c.id)}/overview">Open my full workspace →</a></div>`;
  bindTasks(main);
}

function taskRow(t) {
  return `<div class="row"><input type="checkbox" data-task="${t.id}"><div style="flex:1"><b>${esc(t.title)}</b>
    <div class="small muted">${t.client_name ? esc(t.client_name) + " · " : ""}${t.due ? "due " + esc(t.due) : ""} ${t.source && t.source.startsWith("automation") ? '· <span class="pill p-accent">automated</span>' : ""}</div></div></div>`;
}
function bindTasks(root) {
  root.querySelectorAll("[data-task]").forEach((cb) => cb.onchange = async () => {
    try { await api(`/api/tasks/${cb.dataset.task}/done`, { method: "POST", body: { note: "completed in app" } }); cb.closest(".row").style.opacity = .4; toast("Done — recorded in the audit trail."); }
    catch (e) { cb.checked = false; toast(e.message); }
  });
}

// ---------------------------------------------------------------------------------------- ask
const ASK_HISTORY = [];
async function viewAsk(main) {
  const cpa = S.me.role === "cpa";
  main.innerHTML = page("Ask AgentLedger", "Answers come only from cited evidence. Every number is checked against its source before you see it as verified.",
    cpa ? `<select id="askClient"><option value="">Firm-wide (rules only)</option>${S.clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select>
      <label class="small row"><input type="checkbox" id="deep"> Deep reasoning</label>` : "") +
    `<div class="chat" id="chat">${ASK_HISTORY.join("") || `<div class="card"><h3>Try asking</h3><div class="list">
      ${["What is the standard deduction for married couples this year, and is next year's published yet?", "Why is our taxable income different from book income?",
         "Which of our expenses are missing receipts?", "Do we have to file a beneficial ownership report?", "Should we elect Section 179 or bonus depreciation on this year's equipment?"]
        .map((q) => `<div><a href="#" data-q="${esc(q)}">${esc(q)}</a></div>`).join("")}</div></div>`}</div>
    <div class="composer"><div class="box"><textarea id="q" placeholder="Ask about taxes, your books, a regulation, a document…"></textarea><button class="btn primary" id="send">Ask</button></div></div>`;
  main.querySelectorAll("[data-q]").forEach((a) => a.onclick = (e) => { e.preventDefault(); $("#q").value = a.dataset.q; send(); });
  const send = async () => {
    const q = $("#q").value.trim(); if (!q) return;
    $("#q").value = "";
    const chat = $("#chat"); if (!ASK_HISTORY.length) chat.innerHTML = "";
    chat.insertAdjacentHTML("beforeend", `<div class="msg q">${esc(q)}</div>`);
    const a = document.createElement("div"); a.className = "msg a"; a.innerHTML = `<span class="muted">Gathering evidence…</span>`; chat.appendChild(a);
    let text = "", meta = "";
    const res = await fetch("/api/ask", { method: "POST", headers: { Authorization: `Bearer ${S.token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, client_id: cpa ? $("#askClient").value || null : null, deep: cpa && $("#deep").checked ? true : null }) });
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    const paint = (badges = "") => { a.innerHTML = esc(text).replace(/\[(R|E|F|D|C|M|P):([^\]]+)\]/g, '<span class="cite">$1:$2</span>') + `<div class="meta">${meta}${badges}</div>`; };
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, ""); buf = buf.slice(i + 2);
        const ev = JSON.parse(line);
        if (ev.type === "meta") { meta = `<span class="pill ${ev.tier === "local" ? "p-good" : "p-accent"}">${esc(ev.tier)} · ${esc(ev.model.split(":").slice(1).join(":"))}</span><span class="pill p-mute">${ev.evidence_items} evidence items</span>`; text = ""; paint(); }
        else if (ev.type === "token") { text += ev.text; paint(); }
        else if (ev.type === "verify") { paint(ev.grounded ? `<span class="pill p-good">✓ verified: every number traced to evidence</span>` :
            `<span class="pill p-warn">⚠ not verified${ev.unsupported_numbers.length ? ": " + esc(ev.unsupported_numbers.join(", ")) + " not in evidence" : ""}${ev.unknown_citations.length ? "; unknown citations" : ""}</span>`); }
        else if (ev.type === "escalate") { text += "\n\n— escalating to deep reasoning for a verified answer —\n\n"; paint(); }
        else if (ev.type === "error") { text += `\n[${ev.message}]`; paint(); }
      }
    }
    ASK_HISTORY.push(`<div class="msg q">${esc(q)}</div>`, a.outerHTML);
  };
  $("#send").onclick = send;
  $("#q").onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } };
}

// ---------------------------------------------------------------------------------------- clients
async function viewClients(main) {
  const d = await api("/api/dashboard");
  main.innerHTML = page("Clients", "Every client in their own segregated workspace", `<button class="btn primary" id="newc">+ New client</button>`) +
    `<div class="card"><table><tr><th>Client</th><th>Industry pack</th><th>Kind</th><th class="num">Integrity</th><th class="num">Open findings</th><th class="num">Tasks</th></tr>
    ${d.clients.map((c) => `<tr><td><a href="#/client/${esc(c.id)}/overview"><b>${esc(c.name)}</b></a></td><td>${esc(c.domain)}</td><td>${esc(c.kind)}</td>
      <td class="num">${c.integrity}</td><td class="num">${c.open_findings}</td><td class="num">${c.open_tasks}</td></tr>`).join("")}</table></div>
    <div class="card" id="newform" style="display:none;margin-top:16px"><h3>New client</h3><div class="grid g3">
      <input id="nc_id" placeholder="id (e.g. bright-dental)"><input id="nc_name" placeholder="Legal name"><select id="nc_kind"><option>business</option><option>individual</option></select>
      <select id="nc_entity"><option value="">entity type</option><option>llc</option><option>s_corp</option><option>c_corp</option><option>partnership</option><option>sole_prop</option></select>
      <select id="nc_domain">${["general", "auto_repair", "gas_station", "medical_practice", "insurance_agency", "pe_fund", "hedge_fund"].map((x) => `<option>${x}</option>`).join("")}</select>
      <input id="nc_email" placeholder="client email (routes their mail)"></div>
      <div class="row" style="margin-top:10px"><label class="small row"><input type="checkbox" id="nc_consent"> IRC §7216 consent on file</label><span class="spacer"></span><button class="btn primary" id="nc_go">Create & onboard</button></div>
      <div class="small muted" style="margin-top:6px">Industry not listed? Use <a href="#/foundry/design">Agent foundry → Design</a> to have the Architect draft a new industry pack.</div></div>`;
  $("#newc").onclick = () => $("#newform").style.display = "block";
  $("#nc_go").onclick = async () => {
    try {
      await api("/api/clients", { method: "POST", body: { id: $("#nc_id").value, name: $("#nc_name").value, kind: $("#nc_kind").value,
        entity_type: $("#nc_entity").value || null, domain: $("#nc_domain").value, emails: $("#nc_email").value ? [$("#nc_email").value] : [],
        consent_7216_at: $("#nc_consent").checked ? new Date().toISOString() : null } });
      toast("Client created; chart of accounts instantiated, onboarding automations queued."); await loadMe(); route();
    } catch (e) { toast(e.message); }
  };
}

async function viewClient(main, id, tab = "overview") {
  const d = await api(`/api/clients/${id}`);
  const c = d.client;
  const tabs = [["overview", "Overview"], ["ledger", "Ledger"], ["documents", "Documents"], ["findings", `Findings (${d.findings.filter((f) => f.status === "open").length})`],
    ["business", "Customers & vendors"], ["planning", "Opportunities"], ["facts", "Profile"]];
  main.innerHTML = page(esc(c.name), `${esc(d.pack?.title || c.domain)} · ${esc(c.kind)}${c.entity_type ? " · " + esc(c.entity_type) : ""} ·
    ${d.chain.ok ? `<span class="pill p-good">⛓ ledger chain intact (${d.chain.checked})</span>` : `<span class="pill p-bad">ledger chain broken at #${d.chain.broken_at}</span>`}`,
    `<a class="btn" href="#/ask">Ask about ${esc(c.name.split(" ")[0])}</a>`) +
    `<div class="tabs">${tabs.map(([t, l]) => `<button class="${t === tab ? "active" : ""}" data-tab="${t}">${l}</button>`).join("")}</div><div id="tab"></div>`;
  main.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => location.hash = `#/client/${id}/${b.dataset.tab}`);
  const el = $("#tab");
  ({ overview: tabOverview, ledger: tabLedger, documents: tabDocuments, findings: tabFindings, business: tabBusiness, planning: tabPlanning, facts: tabFacts }[tab] || tabOverview)(el, d);
}

function tabOverview(el, d) {
  const m1 = d.m1 && d.m1.lines ? d.m1 : null;
  el.innerHTML = `<div class="grid g4" style="margin-bottom:16px">
      <div class="card"><h3>Integrity</h3><div class="ring" style="--v:${d.integrity};--c:${d.integrity > 80 ? "var(--good)" : d.integrity > 50 ? "var(--warn)" : "var(--bad)"}"><span>${d.integrity}</span></div></div>
      ${d.kpis.slice(0, 3).map((k) => `<div class="card"><h3>${esc(k.title)}</h3><div class="stat">${k.value === null ? "—" : k.unit === "USD" ? money(k.value) : esc(k.value) + " " + esc(k.unit)}</div>${k.missing ? `<div class="small muted">needs: ${esc(k.missing)}</div>` : ""}</div>`).join("")}
    </div>
    <div class="grid g2">
      ${m1 ? `<div class="card"><h3>Book → tax bridge (Schedule M-1, ${d.year}) <span class="muted small">live from ledger + rules</span></h3><table>
        ${m1.lines.map((l) => `<tr><td class="mono">${esc(l.line)}</td><td>${esc(l.label)}</td><td class="num"><b>${money(l.amount)}</b></td></tr>`).join("")}</table>
        <details style="margin-top:8px"><summary class="small">Rules used (${m1.trace.length})</summary>${m1.trace.map((t) => `<div class="small mono">${esc(t.rule_id)} = ${esc(JSON.stringify(t.value))} · ${esc(t.source)}</div>`).join("")}</details></div>` : ""}
      <div class="card"><h3>Upcoming deadlines</h3><div class="list">${d.deadlines.slice(0, 8).map((x) => `<div class="row"><b>${esc(x.title)}</b><span class="spacer"></span><span class="pill ${x.days < 14 ? "p-warn" : "p-mute"}">${esc(x.due)}</span></div>`).join("") || `<div class="empty">Nothing in the next 120 days (or profile facts missing).</div>`}</div></div>
      <div class="card"><h3>Open findings</h3><div class="list">${d.findings.filter((f) => f.status === "open").slice(0, 6).map((f) => `<div><div class="row">${sevPill(f.severity)}<b>${esc(f.title)}</b></div><div class="small muted">${esc(f.owner === "client" ? "client to resolve" : f.owner === "cpa" ? "CPA to resolve" : "both")}${f.citation ? " · " + esc(f.citation) : ""}</div></div>`).join("") || `<div class="empty">No open findings.</div>`}</div></div>
      <div class="card"><h3>Opportunities & risks (second brain)</h3><div class="list">${d.opportunities.filter((o) => o.status === "applies").slice(0, 6).map((o) => `<div><div class="row"><span class="pill ${o.category === "risk" ? "p-bad" : o.category === "planning" ? "p-accent" : "p-info"}">${esc(o.category)}</span><b>${esc(o.title)}</b></div><div class="small muted">${esc(o.summary)}</div></div>`).join("") || `<div class="empty">Fill in the profile to unlock opportunities.</div>`}</div></div>
    </div>`;
}

async function tabLedger(el, d) {
  const id = d.client.id;
  const [entries, templates] = await Promise.all([api(`/api/clients/${id}/entries`), api(`/api/clients/${id}/templates`)]);
  el.innerHTML = `<div class="grid g2" style="margin-bottom:16px">
    <div class="card"><h3>Post with an industry template</h3><select id="tpl" style="width:100%">${templates.map((t) => `<option value="${esc(t.id)}">${esc(t.title)}</option>`).join("")}</select>
      <div id="tplform" class="grid g2" style="margin-top:10px"></div><div class="row" style="margin-top:10px"><input type="date" id="tpldate" value="${new Date().toISOString().slice(0, 10)}"><span class="spacer"></span><button class="btn primary" id="tplgo">Post</button></div>
      <div class="small muted" style="margin-top:6px">Templates always balance and pull statutory rates from the live rule base.</div></div>
    <div class="card"><h3>Import bank / card transactions</h3><textarea id="csv" rows="6" placeholder="Paste a CSV export (Date, Description, Amount)…"></textarea>
      <div class="row" style="margin-top:8px"><span class="small muted">Learns from your own history first, then known merchants, then the local AI.</span><span class="spacer"></span><button class="btn" id="preview">Suggest categories</button></div></div></div>
    <div class="card" id="suggest" style="display:none;margin-bottom:16px"></div>
    <div class="card"><h3>Journal (append-only; corrections are reversals)</h3><table><tr><th>#</th><th>Date</th><th>Memo</th><th>Postings</th><th>Source</th></tr>
    ${entries.map((e) => `<tr><td class="mono">${e.id}</td><td>${esc(e.date)}</td><td>${esc(e.memo)}${e.document_id ? ' <span class="pill p-good">receipt</span>' : ""}</td>
      <td class="mono small">${e.postings.map((p) => `${esc(p.account_code)} ${Number(p.amount) > 0 ? "Dr" : "Cr"} ${money(Math.abs(p.amount))}${p.tax_treatment ? " · " + esc(p.tax_treatment) : ""}`).join("<br>")}</td>
      <td class="small muted">${esc(e.source)}<br>${esc(e.created_by)}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No entries yet.</td></tr>`}</table></div>`;
  const renderForm = () => {
    const t = templates.find((x) => x.id === $("#tpl").value);
    $("#tplform").innerHTML = t.inputs.map((i) => `<label class="small">${esc(i.name.replaceAll("_", " "))}${i.description ? ` <span class="muted">(${esc(i.description)})</span>` : ""}<input data-in="${esc(i.name)}" value="${esc(t.sample[i.name] ?? "")}" style="width:100%"></label>`).join("");
  };
  $("#tpl").onchange = renderForm; renderForm();
  $("#tplgo").onclick = async () => {
    const body = { date: $("#tpldate").value }; el.querySelectorAll("[data-in]").forEach((i) => body[i.dataset.in] = i.value);
    try { const r = await api(`/api/clients/${id}/templates/${$("#tpl").value}`, { method: "POST", body }); toast(`Posted entry #${r.entry_id}`); route(); } catch (e) { toast(e.message); }
  };
  $("#preview").onclick = async () => {
    try {
      const sug = await api(`/api/clients/${id}/bank/preview`, { method: "POST", body: { csv: $("#csv").value } });
      const box = $("#suggest"); box.style.display = "block";
      box.innerHTML = `<h3>Review suggestions — nothing posts until you confirm</h3><table><tr><th></th><th>Date</th><th>Description</th><th class="num">Amount</th><th>Account</th><th>Why</th></tr>
        ${sug.map((s, i) => `<tr><td><input type="checkbox" data-i="${i}" ${s.account && !s.duplicate && s.confidence >= .6 ? "checked" : ""}></td><td>${esc(s.date)}</td><td>${esc(s.description)}${s.duplicate ? ' <span class="pill p-bad">duplicate</span>' : ""}</td>
          <td class="num">${money(s.amount)}</td><td><input data-acct="${i}" value="${esc(s.account || "")}" size="6"> <span class="small muted">${esc(s.account_name || "")}</span></td><td class="small">${esc(s.basis)} <span class="pill p-mute">${Math.round(s.confidence * 100)}%</span></td></tr>`).join("")}</table>
        <div class="row" style="margin-top:10px"><span class="spacer"></span><button class="btn primary" id="postsel">Post selected</button></div>`;
      $("#postsel").onclick = async () => {
        const chosen = sug.filter((s, i) => el.querySelector(`[data-i="${i}"]`).checked).map((s) => ({ ...s, account: el.querySelector(`[data-acct="${s.index}"]`).value }));
        const r = await api(`/api/clients/${id}/bank/post`, { method: "POST", body: chosen }); toast(`Posted ${r.posted.length} entries; next import will learn from them.`); route();
      };
    } catch (e) { toast(e.message); }
  };
}

function tabDocuments(el, d) {
  const id = d.client.id;
  el.innerHTML = `<div class="drop" id="drop" style="margin-bottom:16px"><b>Drop anything here</b> — PDFs, photos of receipts, Word, Excel, CSV, emails (.eml), even ZIPs.<br>
    <span class="small">AgentLedger reads it, classifies it, files it in this client's vault and links it to the books.</span><br><input type="file" id="file" multiple style="margin-top:10px"></div>
    <div class="card"><table><tr><th>Document</th><th>Type</th><th>Year</th><th>Status</th><th>Filed under</th><th>Read by</th></tr>
    ${d.documents.map((x) => `<tr><td><a href="#" data-download="/api/documents/${esc(x.id)}/file">${esc(x.original_name)}</a><div class="small muted">${esc(x.summary || "")}</div></td>
      <td>${esc(x.doc_type)}</td><td>${esc(x.tax_year || "")}</td><td>${statusPill(x.status)} <span class="small muted">${Math.round((x.confidence || 0) * 100)}%</span></td>
      <td class="small mono">${esc(x.vault_path)}</td><td class="small">${esc(x.classified_by || "")}<br><span class="muted">${esc(x.channel)}</span></td></tr>`).join("") || `<tr><td colspan="6" class="empty">No documents yet.</td></tr>`}</table></div>`;
  const up = async (files) => {
    for (const f of files) {
      const fd = new FormData(); fd.append("file", f); fd.append("client_id", id);
      toast(`Reading ${f.name}…`);
      try { const r = await api("/api/documents/upload", { method: "POST", body: fd }); toast(r.map((x) => `${x.name}: ${x.doc_type} → ${x.status}`).join("; ")); } catch (e) { toast(e.message); }
    }
    route();
  };
  const drop = $("#drop");
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); }; drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); up(e.dataTransfer.files); };
  $("#file").onchange = (e) => up(e.target.files);
}

function tabFindings(el, d) {
  const cpa = S.me.role === "cpa";
  el.innerHTML = `<div class="row" style="margin-bottom:12px"><span class="muted">Findings can be explained, corrected or (by the CPA) accepted as a risk — never deleted. Both sides see every resolution.</span><span class="spacer"></span><button class="btn" id="rerun">Re-run checks</button></div>
    ${d.findings.map((f) => `<div class="card" style="margin-bottom:10px"><div class="row">${sevPill(f.severity)}${statusPill(f.status)}<b>${esc(f.title)}</b><span class="spacer"></span><span class="small muted">${esc(f.check_id)}</span></div>
      <div style="margin:6px 0">${esc(f.detail)}</div>${f.citation ? `<div class="small muted">Authority: ${esc(f.citation)}</div>` : ""}
      ${f.resolutions.map((r) => `<div class="small" style="margin-top:6px">↳ <b>${esc(r.actor)}</b> (${esc(r.role)}) ${esc(r.action.replace("_", " "))}: ${esc(r.note)} <span class="muted">${esc(r.at.slice(0, 16))}</span></div>`).join("")}
      ${f.status === "open" ? `<div class="row" style="margin-top:8px"><input data-note="${f.id}" placeholder="Explain what happened or what you fixed…" style="flex:1">
        <button class="btn sm" data-res="${f.id}" data-a="explained">Explain</button><button class="btn sm" data-res="${f.id}" data-a="corrected">Corrected</button>
        ${cpa ? `<button class="btn sm bad" data-res="${f.id}" data-a="accepted_risk">Accept risk</button>` : ""}</div>` : ""}</div>`).join("") || `<div class="empty">No findings. Clean books.</div>`}`;
  $("#rerun").onclick = async () => { await api(`/api/clients/${d.client.id}/integrity/run`, { method: "POST" }); route(); };
  el.querySelectorAll("[data-res]").forEach((b) => b.onclick = async () => {
    try { await api(`/api/findings/${b.dataset.res}/resolve`, { method: "POST", body: { action: b.dataset.a, note: el.querySelector(`[data-note="${b.dataset.res}"]`).value } }); route(); }
    catch (e) { toast(e.message); }
  });
}

async function tabBusiness(el, d) {
  const id = d.client.id;
  const parties = await api(`/api/clients/${id}/parties`);
  const custs = parties.filter((p) => p.kind !== "vendor"), vendors = parties.filter((p) => p.kind !== "customer");
  const ar = d.ar || { buckets: {}, invoices: [] }, deals = d.deals || { stages: {} };
  el.innerHTML = `<div class="grid g2" style="margin-bottom:16px">
    <div class="card"><h3>Receivables aging</h3><div class="row">${Object.entries(ar.buckets).map(([b, v]) => `<div style="flex:1"><div class="small muted">${esc(b)}</div><b>${money(v)}</b></div>`).join("")}</div>
      <table style="margin-top:10px">${ar.invoices.map((i) => `<tr><td>#${esc(i.number)} ${esc(i.party_name)}</td><td>${i.days_late ? `<span class="pill p-warn">${i.days_late}d late</span>` : `<span class="pill p-mute">due ${esc(i.due)}</span>`}</td><td class="num">${money(i.total)}</td><td><button class="btn sm" data-pay="${i.id}">Mark paid</button></td></tr>`).join("")}</table></div>
    <div class="card"><h3>New invoice</h3><div class="grid g2"><select id="inv_party">${custs.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select><input id="inv_no" placeholder="Invoice #">
      <input id="inv_amt" placeholder="Amount"><input id="inv_tax" placeholder="Sales tax" value="0"></div><input id="inv_desc" placeholder="Description" style="width:100%;margin-top:8px">
      <div class="row" style="margin-top:8px"><span class="small muted">Posts to A/R and revenue automatically.</span><span class="spacer"></span><button class="btn primary" id="inv_go">Create invoice</button></div></div></div>
    <div class="card" style="margin-bottom:16px"><h3>Sales pipeline <span class="muted small">weighted open pipeline ${money(deals.weighted_open_pipeline || 0)}</span></h3>
      <div class="kanban">${Object.entries(deals.stages).map(([s, ds]) => `<div class="col"><h4>${esc(s)}</h4>${ds.map((x) => `<div class="tile"><b>${esc(x.title)}</b><div class="small muted">${esc(x.party_name || "")} · ${money(x.value)}</div></div>`).join("")}</div>`).join("")}</div></div>
    <div class="grid g2"><div class="card"><h3>Vendors & 1099 readiness <span class="muted small">threshold from live rules</span></h3><table><tr><th>Vendor</th><th class="num">Paid ${d.year}</th><th>1099</th><th>W-9</th><th></th></tr>
      ${(d.vendors_1099 || []).map((v) => `<tr><td>${esc(v.name)}</td><td class="num">${money(v.paid)}</td><td>${v.exempt_corporation ? '<span class="pill p-mute">corp exempt</span>' : v.requires_1099 ? '<span class="pill p-warn">required</span>' : '<span class="pill p-mute">no</span>'}</td>
      <td>${v.w9_on_file ? '<span class="pill p-good">on file</span>' : '<span class="pill p-bad">missing</span>'}</td><td class="small">${esc(v.action)}</td></tr>`).join("")}</table></div>
    <div class="card"><h3>Add customer / vendor</h3><div class="grid g2"><select id="pt_kind"><option>customer</option><option>vendor</option><option>both</option></select><input id="pt_name" placeholder="Name">
      <input id="pt_email" placeholder="Email"><select id="pt_entity"><option value="">entity type</option><option>individual</option><option>llc</option><option>s_corp</option><option>c_corp</option></select></div>
      <div class="row" style="margin-top:8px"><label class="small row"><input type="checkbox" id="pt_w9"> W-9 on file</label><span class="spacer"></span><button class="btn primary" id="pt_go">Add</button></div></div></div>`;
  el.querySelectorAll("[data-pay]").forEach((b) => b.onclick = async () => { await api(`/api/clients/${id}/invoices/${b.dataset.pay}/pay`, { method: "POST" }); toast("Payment recorded and posted."); route(); });
  $("#inv_go").onclick = async () => { try { await api(`/api/clients/${id}/invoices`, { method: "POST", body: { party_id: $("#inv_party").value, number: $("#inv_no").value, amount: $("#inv_amt").value, sales_tax: $("#inv_tax").value, description: $("#inv_desc").value } }); toast("Invoice created and posted."); route(); } catch (e) { toast(e.message); } };
  $("#pt_go").onclick = async () => { try { await api(`/api/clients/${id}/parties`, { method: "POST", body: { kind: $("#pt_kind").value, name: $("#pt_name").value, email: $("#pt_email").value, entity_type: $("#pt_entity").value || null, w9_on_file: $("#pt_w9").checked } }); route(); } catch (e) { toast(e.message); } };
}

function tabPlanning(el, d) {
  el.innerHTML = `<div class="muted" style="margin-bottom:12px">Every playbook the firm knows, run against this client's facts. "Needs a fact" items become client requests automatically.</div>
    ${d.opportunities.map((o) => `<div class="card" style="margin-bottom:10px"><div class="row"><span class="pill ${o.category === "risk" ? "p-bad" : o.category === "planning" ? "p-accent" : "p-info"}">${esc(o.category)}</span>
      <b>${esc(o.title)}</b>${o.status === "needs_facts" ? `<span class="pill p-warn">needs: ${esc(o.missing_fact)}</span>` : `<span class="pill p-good">applies</span>`}<span class="pill p-mute">${esc(o.aggressiveness)}</span>
      <span class="spacer"></span>${o.freshness.fresh ? `<span class="pill p-good">current</span>` : `<span class="pill p-warn" title="${esc(o.freshness.reasons.join("; "))}">review due</span>`}</div>
      <div style="margin-top:6px">${esc(o.summary)}</div></div>`).join("") || `<div class="empty">No playbooks apply.</div>`}`;
}

function tabFacts(el, d) {
  const c = d.client, wanted = new Set([...(d.pack?.facts || []), ...Object.keys(c.facts), "state", "employees", "has_rnd", "owner_has_home", "real_property_basis", "state_has_ptet", "contractor_payments", "has_retirement_plan"]);
  el.innerHTML = `<div class="card"><h3>Profile facts <span class="muted small">used by playbooks, deadlines and KPIs</span></h3><div class="grid g3">
    ${[...wanted].map((k) => `<label class="small">${esc(k.replaceAll("_", " "))}<input data-fact="${esc(k)}" value="${esc(c.facts[k] ?? "")}" style="width:100%"></label>`).join("")}</div>
    <div class="row" style="margin-top:10px"><span class="small muted">true / false / numbers are understood.</span><span class="spacer"></span><button class="btn primary" id="savefacts">Save</button></div></div>`;
  $("#savefacts").onclick = async () => {
    const facts = {}; el.querySelectorAll("[data-fact]").forEach((i) => { const v = i.value.trim(); if (v === "") return; facts[i.dataset.fact] = v === "true" ? true : v === "false" ? false : isNaN(Number(v)) ? v : Number(v); });
    await api(`/api/clients/${c.id}/facts`, { method: "PATCH", body: facts }); toast("Saved. Opportunities and deadlines recalculated."); route();
  };
}

// ---------------------------------------------------------------------------------------- inbox
async function viewInbox(main) {
  const docs = await api("/api/documents/review");
  main.innerHTML = page("Intake inbox", "Everything that arrived by email, maildrop, upload or connector and could not be filed with confidence. Nothing is ever guessed into a client.") +
    `<div class="drop" id="drop" style="margin-bottom:16px"><b>Drop documents for any client</b> — AgentLedger will work out whose they are.<br><input type="file" id="file" multiple style="margin-top:10px"></div>
    <div class="card"><table><tr><th>Document</th><th>Looks like</th><th>Why it's here</th><th>File to</th></tr>
    ${docs.map((x) => `<tr><td><a href="#" data-download="/api/documents/${esc(x.id)}/file">${esc(x.original_name)}</a><div class="small muted">${esc(x.sender || x.channel)} · ${esc(x.received_at.slice(0, 16))}</div></td>
      <td>${esc(x.doc_type)} ${esc(x.tax_year || "")}<div class="small muted">${esc(x.summary || "")}</div></td><td class="small">${Math.round((x.confidence || 0) * 100)}% confidence · ${esc(x.classified_by)}</td>
      <td><select data-doc="${esc(x.id)}"><option value="">choose client…</option>${S.clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select></td></tr>`).join("") || `<tr><td colspan="4" class="empty">Inbox zero. Everything was filed automatically.</td></tr>`}</table></div>`;
  main.querySelectorAll("[data-doc]").forEach((s) => s.onchange = async () => { await api(`/api/documents/${s.dataset.doc}/assign`, { method: "POST", body: { client_id: s.value } }); toast("Filed."); route(); });
  const up = async (files) => { for (const f of files) { const fd = new FormData(); fd.append("file", f); const r = await api("/api/documents/upload", { method: "POST", body: fd }); toast(r.map((x) => `${x.name}: ${x.doc_type} → ${x.status}`).join("; ")); } route(); };
  const drop = $("#drop");
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); }; drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); up(e.dataTransfer.files); }; $("#file").onchange = (e) => up(e.target.files);
}

// ---------------------------------------------------------------------------------------- foundry
async function viewFoundry(main, tab = "proposals") {
  const tabs = [["proposals", "Proposals"], ["agents", "Agents"], ["design", "Design (no code)"], ["models", "Models"], ["oss", "Open-source radar"]];
  main.innerHTML = page("Agent foundry", "Agents observe, propose and verify. Routine, verified changes adopt themselves under policy; everything else waits for you — in plain language.") +
    `<div class="tabs">${tabs.map(([t, l]) => `<button class="${t === tab ? "active" : ""}" data-t="${t}">${l}</button>`).join("")}</div><div id="ft"></div>`;
  main.querySelectorAll("[data-t]").forEach((b) => b.onclick = () => location.hash = `#/foundry/${b.dataset.t}`);
  const el = $("#ft");
  if (tab === "proposals") {
    const ps = await api("/api/proposals");
    el.innerHTML = ps.map((p) => `<div class="card" style="margin-bottom:12px"><div class="row">${statusPill(p.status)}${riskPill(p.risk)}<span class="pill p-mute">${esc(p.kind.replace("_", " "))}</span><b>${esc(p.title)}</b>
      <span class="spacer"></span><span class="small muted">${esc(p.agent)} · ${esc(p.created_at.slice(0, 16))}</span></div>
      <div style="margin:8px 0">${esc(p.summary)}</div>
      ${p.source ? `<div class="small">Source: <a href="${esc(p.source.url)}" target="_blank" rel="noopener">${esc(p.source.title)}</a></div>` : ""}
      <div class="grid g2" style="margin-top:8px"><div><div class="small muted">Deterministic checks</div><div class="checks small">${p.checks.map((c) => `<div><span class="${c.ok ? "ok" : "no"}">${c.ok ? "✓" : "✗"}</span><span><b>${esc(c.name)}</b> ${esc(c.detail)}</span></div>`).join("")}</div></div>
      <div><div class="small muted">Why this risk level</div>${p.risk_reasons.map((r) => `<div class="small">• ${esc(r)}</div>`).join("")}
      ${(p.payload.changes || []).map((c) => `<div class="small" style="margin-top:6px"><span class="mono">${esc(c.rule_id)}</span> → <b class="mono">${esc(JSON.stringify(c.value))}</b> from ${esc(c.effective_from)}<br>${(c.evidence_quotes || []).map((q) => `<span class="muted">“${esc(q)}”</span>`).join("<br>")}</div>`).join("")}
      ${p.impact && p.impact.clients && p.impact.clients.length ? `<div class="small" style="margin-top:6px"><b>Client impact:</b> ${p.impact.clients.map((x) => `${esc(x.client_id)} TY${x.tax_year} Δ ${money(x.delta)}`).join(", ")}</div>` : ""}</div></div>
      ${p.decision ? `<div class="small muted" style="margin-top:8px">${p.decision.mode === "auto" ? "Adopted automatically under policy" : "Decided by " + esc(p.decision.by)}${p.decision.note ? ": " + esc(p.decision.note) : ""}</div>` : ""}
      <div class="row" style="margin-top:10px">${p.status === "pending" ? `<input data-note="${p.id}" placeholder="Note (optional)" style="flex:1"><button class="btn good sm" data-d="approve" data-p="${p.id}">Approve</button><button class="btn bad sm" data-d="reject" data-p="${p.id}">Reject</button>` : ""}
      ${p.status === "adopted" ? `<span class="spacer"></span><button class="btn sm" data-d="rollback" data-p="${p.id}">Roll back</button>` : ""}</div></div>`).join("") || `<div class="empty">No proposals yet. Run an agent from the Agents tab.</div>`;
    el.querySelectorAll("[data-d]").forEach((b) => b.onclick = async () => {
      try { await api(`/api/proposals/${b.dataset.p}/${b.dataset.d}`, { method: "POST", body: { note: el.querySelector(`[data-note="${b.dataset.p}"]`)?.value || "" } }); toast(`${b.dataset.d}d`); route(); } catch (e) { toast(e.message); }
    });
  } else if (tab === "agents") {
    const a = await api("/api/agents");
    el.innerHTML = `<div class="grid g2">${a.agents.map((s) => `<div class="card"><div class="row"><span class="dot ${s.enabled ? "on" : "off"}"></span><b>${esc(s.title || s.id)}</b><span class="pill p-mute">${esc(s.kind)}</span><span class="spacer"></span>
      <button class="btn sm" data-run="${esc(s.id)}">Run now</button></div><div class="small" style="margin-top:6px">${esc(s.mission)}</div>
      <div class="small muted" style="margin-top:6px">every ${s.every_hours}h · ${s.last_run ? `last ${esc(s.last_run.started_at.slice(0, 16))} ${s.last_run.ok ? "✓" : "✗ " + esc(s.last_run.error || "")}` : "never run"}${s.due ? " · due" : ""}</div>
      ${s.last_run && s.last_run.log.length ? `<details><summary class="small">log</summary><pre class="code">${esc(s.last_run.log.join("\n"))}</pre></details>` : ""}</div>`).join("")}</div>`;
    el.querySelectorAll("[data-run]").forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = "running…"; try { const r = await api(`/api/agents/${b.dataset.run}/run`, { method: "POST" }); toast(`${r.agent}: ${r.ok ? "ok" : r.error} · ${r.proposals.length} proposal(s), ${r.alerts.length} alert(s)`); } catch (e) { toast(e.message); } route(); });
  } else if (tab === "design") {
    el.innerHTML = `<div class="grid g2">${[["agent", "New monitoring agent", "Watch the Texas Comptroller news page for franchise tax threshold changes"],
      ["domain_pack", "New industry pack", "Dental practice with orthodontics: patient financing, lab fees, PPO write-offs"],
      ["playbook", "New CPA playbook", "Qualified small business stock (Section 1202) planning for C-corp founders"],
      ["automation", "New automation", "When a 1099-K arrives for a client, ask the client to confirm which deposits it covers"]]
      .map(([k, t, ex]) => `<div class="card"><h3>${t}</h3><textarea rows="3" data-design="${k}" placeholder="${esc(ex)}"></textarea><div class="row" style="margin-top:8px"><span class="small muted">Drafted by AI, verified mechanically, approved by you.</span><span class="spacer"></span><button class="btn primary sm" data-go="${k}">Draft</button></div></div>`).join("")}</div>`;
    el.querySelectorAll("[data-go]").forEach((b) => b.onclick = async () => {
      const ta = el.querySelector(`[data-design="${b.dataset.go}"]`); b.textContent = "drafting…";
      try { const p = await api(`/api/design/${b.dataset.go}`, { method: "POST", body: { description: ta.value || ta.placeholder } }); toast(`Proposal ${p.id}: ${p.title}`); location.hash = "#/foundry/proposals"; } catch (e) { toast(e.message); b.textContent = "Draft"; }
    });
  } else if (tab === "models") {
    const m = await api("/api/models");
    el.innerHTML = `<div class="grid g2"><div class="card"><h3>Role champions</h3><table><tr><th>Role</th><th>Tier</th><th>Model</th><th>Purpose</th></tr>${Object.entries(m.router.roles).map(([r, x]) => `<tr><td><b>${esc(r)}</b></td><td>${esc(x.tier)}</td><td class="mono">${esc(x.model)}</td><td class="small muted">${esc(x.purpose)}</td></tr>`).join("")}</table></div>
      <div class="card"><h3>Installed open-source models</h3>${m.router.local.installed.map((x) => `<div class="mono small">${esc(x)}</div>`).join("") || `<div class="empty">Ollama offline</div>`}
      <h3 style="margin-top:14px">Promotion history</h3>${m.router.history.map((h) => `<div class="small">${esc(h.at.slice(0, 10))} ${esc(h.role)}: ${esc(h.from?.model || "-")} → <b>${esc(h.to.model)}</b> (${esc(h.reason)})</div>`).join("") || `<div class="small muted">No swaps yet.</div>`}</div>
      ${m.scout ? `<div class="card" style="grid-column: span 2"><h3>Latest scout benchmark</h3>${Object.entries(m.scout.roles).map(([r, x]) => `<div><b>${esc(r)}</b> (champion ${esc(x.champion)})<table>${Object.entries(x.scores).map(([mm, s]) => `<tr><td class="mono">${esc(mm)}</td><td class="num">${s.score}</td><td class="num small">${s.seconds_per_case}s/case</td><td class="num small">json ${s.json_valid}</td></tr>`).join("")}</table></div>`).join("")}
        <h3 style="margin-top:10px">Newly available</h3>${m.scout.discovered.slice(0, 15).map((d) => `<span class="pill ${d.installed ? "p-good" : "p-mute"}" style="margin:2px">${esc(d.name)}</span>`).join("")}</div>` : `<div class="card"><div class="empty">Run the model scout to benchmark candidates.</div></div>`}</div>`;
  } else if (tab === "oss") {
    const o = await api("/api/oss");
    const rep = o.report;
    el.innerHTML = `<div class="card"><h3>Open-source stack</h3><table><tr><th>Repository</th><th>Role</th><th>Used</th><th>Health</th><th>Latest release</th><th class="num">★</th><th>Licence</th></tr>
      ${(rep ? rep.repos : o.inventory.repos).map((r) => `<tr><td><a href="https://github.com/${esc(r.repo)}" target="_blank" rel="noopener">${esc(r.repo)}</a></td><td class="small">${esc(r.role)}</td><td>${r.used ? "✓" : ""}</td>
        <td>${r.health ? `<span class="pill ${r.health === "active" ? "p-good" : "p-bad"}">${esc(r.health)}</span>` : ""}</td><td class="small">${esc(r.latest_release || "")}</td><td class="num">${esc(r.stars ?? "")}</td><td class="small">${esc(r.license || "")}</td></tr>`).join("")}</table>
      ${rep ? `<h3 style="margin-top:14px">Discovered candidates</h3>${rep.candidates.map((c) => `<div class="small"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.repo)}</a> ★${c.stars} · ${esc(c.description || "")}</div>`).join("") || `<div class="small muted">none</div>`}` : `<div class="small muted" style="margin-top:8px">Run the repo scout for live health data.</div>`}</div>`;
  }
}

// ---------------------------------------------------------------------------------------- rules
async function viewRules(main) {
  const [rules, stale] = await Promise.all([api("/api/rules"), api("/api/staleness")]);
  main.innerHTML = page("Regulations as code", "Every statutory number the platform uses — effective-dated, cited, and maintained by agents.") +
    `<div class="grid g2" style="margin-bottom:16px"><div class="card"><h3>Calculator</h3><div class="row"><select id="calc"></select><input id="calcin" style="flex:1" value='{"tax_year": 2026, "filing_status": "mfj"}'><button class="btn" id="calcgo">Run</button></div><pre class="code" id="calcout" style="margin-top:8px">Deterministic. Shows the rules it used.</pre></div>
    <div class="card"><h3>Due & sunsetting</h3><div class="list">${stale.filter((a) => a.severity !== "info").map((a) => `<div class="row small">${sevPill(a.severity)}<b>${esc(a.title)}</b><span class="spacer"></span>${esc(a.status.replace("_", " "))} ${esc(a.expected_by)}</div>`).join("")}</div></div></div>
    <div class="card"><table><tr><th>Rule</th><th>Current value</th><th>Authority</th><th></th></tr>${rules.map((r) => `<tr><td><b>${esc(r.title)}</b><div class="mono small muted">${esc(r.id)}</div></td>
      <td class="mono small">${esc(JSON.stringify(r.current))} ${esc(r.unit || "")}</td><td class="small">${esc(r.current_source || "")}<div class="muted">${esc(r.citation)}</div></td><td>${r.indexed ? '<span class="pill p-info">indexed</span>' : ""}</td></tr>`).join("")}</table></div>`;
  const calcs = await api("/api/calculators");
  $("#calc").innerHTML = Object.entries(calcs).map(([k, v]) => `<option value="${esc(k)}" title="${esc(v)}">${esc(k)}</option>`).join("");
  $("#calcgo").onclick = async () => { try { $("#calcout").textContent = JSON.stringify(await api(`/api/calculators/${$("#calc").value}`, { method: "POST", body: JSON.parse($("#calcin").value) }), null, 2); } catch (e) { $("#calcout").textContent = e.message; } };
}

// ---------------------------------------------------------------------------------------- brain
async function viewBrain(main) {
  const pbs = await api("/api/playbooks");
  main.innerHTML = page("CPA second brain", "Expert playbooks and firm precedents. Linked to live rules: when a rule changes, the playbook is flagged for review.") +
    `<div class="card" style="margin-bottom:16px"><h3>Record a firm precedent</h3><div class="grid g3"><input id="pr_topic" placeholder="Topic"><input id="pr_sit" placeholder="Situation"><input id="pr_cit" placeholder="Citations (comma separated)"></div>
      <textarea id="pr_j" rows="2" placeholder="Your judgment and why" style="margin-top:8px"></textarea><div class="row" style="margin-top:8px"><span class="small muted">Precedents become evidence the AI cites for everyone at the firm.</span><span class="spacer"></span><button class="btn primary" id="pr_go">Save</button></div></div>
    ${pbs.map((p) => `<div class="card" style="margin-bottom:10px"><div class="row"><span class="pill ${p.category === "risk" ? "p-bad" : p.category === "planning" ? "p-accent" : "p-info"}">${esc(p.category)}</span><b>${esc(p.title)}</b><span class="pill p-mute">${esc(p.aggressiveness)}</span>
      <span class="spacer"></span>${p.freshness.fresh ? '<span class="pill p-good">current</span>' : `<span class="pill p-warn">${esc(p.status.replace("_", " "))}</span>`}</div><div style="margin-top:6px">${esc(p.summary)}</div>
      <details style="margin-top:6px"><summary class="small">Steps, substance and authority</summary><ol class="small">${p.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol><div class="small"><b>Substance required:</b> ${esc(p.substance)}</div>
      <div class="small muted">Applies when: <span class="mono">${esc(p.applies_when)}</span> · ${esc(p.citations.join("; "))}</div>${p.freshness.reasons.map((r) => `<div class="small" style="color:var(--warn)">• ${esc(r)}</div>`).join("")}</details></div>`).join("")}`;
  $("#pr_go").onclick = async () => { await api("/api/precedents", { method: "POST", body: { topic: $("#pr_topic").value, situation: $("#pr_sit").value, judgment: $("#pr_j").value, citations: $("#pr_cit").value.split(",").map((s) => s.trim()).filter(Boolean) } }); toast("Precedent saved."); };
}

// ---------------------------------------------------------------------------------------- crm
async function viewCrm(main) {
  const [pipe, tasks, msgs, autos] = await Promise.all([api("/api/crm/pipeline"), api("/api/tasks"), api("/api/messages"), api("/api/automations")]);
  main.innerHTML = page("CRM & tasks", "Engagements, client requests, messages and the automations that keep them moving.", `<button class="btn" id="runauto">Run automations now</button>`) +
    `<div class="card" style="margin-bottom:16px"><h3>Engagement pipeline</h3><div class="kanban">${Object.entries(pipe).map(([s, es]) => `<div class="col"><h4>${esc(s.replace("_", " "))}</h4>
      ${es.map((e) => `<div class="tile"><b>${esc(e.client_name)}</b><div class="small">${esc(e.type)} ${esc(e.tax_year || "")}</div><div class="small muted">${e.due_date ? "due " + esc(e.due_date) : ""} · ${e.open_tasks} task(s)</div>
        <select data-eng="${e.id}" class="small" style="margin-top:4px">${Object.keys(pipe).map((x) => `<option ${x === s ? "selected" : ""}>${x}</option>`).join("")}</select></div>`).join("")}</div>`).join("")}</div></div>
    <div class="grid g2"><div class="card"><h3>Open tasks & client requests</h3><div class="list">${tasks.map(taskRow).join("") || `<div class="empty">Nothing open.</div>`}</div></div>
    <div class="card"><h3>Messages <span class="muted small">drafted by automations, sent only by you</span></h3><div class="list">${msgs.map((m) => `<div><div class="row"><b>${esc(m.subject)}</b>${statusPill(m.status)}<span class="spacer"></span>${m.status === "draft" ? `<button class="btn sm" data-send="${m.id}">Send</button>` : ""}</div>
      <div class="small muted">${esc(m.client_name || "")} ${m.to_addr ? "→ " + esc(m.to_addr) : ""}</div><details><summary class="small">body</summary><pre class="code">${esc(m.body)}</pre></details></div>`).join("") || `<div class="empty">No messages.</div>`}</div></div></div>
    <div class="card" style="margin-top:16px"><h3>Automations <a class="small" href="#/foundry/design" style="margin-left:auto">design a new one in plain English</a></h3><table>${autos.automations.map((a) => `<tr><td><b>${esc(a.title)}</b><div class="small mono muted">${esc(a.trigger)}${a.when ? " · " + esc(a.when) : ""}</div></td><td class="small">${a.actions.map((x) => esc(x.type)).join(", ")}</td><td>${a.enabled ? '<span class="pill p-good">on</span>' : '<span class="pill p-mute">off</span>'}</td></tr>`).join("")}</table></div>`;
  bindTasks(main);
  main.querySelectorAll("[data-eng]").forEach((s) => s.onchange = async () => { await api(`/api/crm/engagements/${s.dataset.eng}/stage`, { method: "POST", body: { stage: s.value } }); route(); });
  main.querySelectorAll("[data-send]").forEach((b) => b.onclick = async () => { const r = await api(`/api/messages/${b.dataset.send}/send`, { method: "POST" }); toast(r.sent ? "Sent." : r.reason); route(); });
  $("#runauto").onclick = async () => { const r = await api("/api/automations/run", { method: "POST" }); toast(`${r.events} event(s) processed, ${r.fired.length} action(s) fired.`); route(); };
}

// ---------------------------------------------------------------------------------------- integrations
async function viewIntegrations(main) {
  const p = await api("/api/plugins");
  const cats = {};
  p.catalog.forEach((c) => (cats[c.category] = cats[c.category] || []).push(c));
  main.innerHTML = page("Integrations", "Connectors, importers, exporters, webhooks and MCP. Every integration runs with only the permissions its manifest declares and flows through the same checks and audit.") +
    `<div class="grid g3" style="margin-bottom:16px">${p.installed.map((m) => `<div class="card"><div class="row"><b>${esc(m.name)}</b><span class="spacer"></span><span class="pill p-mute">${esc(m.kind)}</span></div>
      <div class="small" style="margin:6px 0">${esc(m.description)}</div><div class="row">${m.permissions.map((x) => `<span class="pill p-info">${esc(x)}</span>`).join("")}${(m.network_domains || []).map((x) => `<span class="pill p-warn">${esc(x)}</span>`).join("")}</div>
      ${m.kind === "exporter" ? `<div class="row" style="margin-top:8px"><select data-exp="${esc(m.id)}">${S.clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select><button class="btn sm" data-dl="${esc(m.id)}">Download</button></div>` : ""}</div>`).join("")}</div>
    <div class="card" style="margin-bottom:16px"><h3>Catalog — the AI Engineer builds these on request</h3><table><tr><th>Integration</th><th>Category</th><th>Status</th><th></th></tr>
      ${p.catalog.map((c) => `<tr><td><b>${esc(c.name)}</b>${c.docs ? ` <a class="small" href="${esc(c.docs)}" target="_blank" rel="noopener">docs</a>` : ""}</td><td>${esc(c.category)}</td>
        <td><span class="pill ${c.status === "available" ? "p-good" : c.status === "buildable" ? "p-accent" : "p-info"}">${esc(c.status.replaceAll("_", " "))}</span></td>
        <td>${c.status === "buildable" ? `<button class="btn sm" data-build="${esc(c.id)}" data-docs="${esc(c.docs || "")}">Request build</button>` : ""}</td></tr>`).join("")}</table></div>
    <div class="grid g2"><div class="card"><h3>Webhooks</h3><div class="small">Inbound: <span class="mono">POST /api/hooks/&lt;firm-id&gt;/&lt;client-id&gt;</span> with header <span class="mono">X-AgentLedger-Signature: hex(HMAC-SHA256(body, AGENTLEDGER_WEBHOOK_SECRET))</span>.
      Body: <span class="mono">{"transactions": [...], "documents": [...]}</span>. Works with Zapier, Make, n8n and any SaaS.<br><br>Outbound: automations with <span class="mono">type: webhook</span> post to Slack, Teams or any URL held in an environment variable.</div></div>
      <div class="card"><h3>MCP (Model Context Protocol)</h3><div class="small">Run <span class="mono">agentledger mcp</span> to expose AgentLedger to Claude Desktop, Claude Code or any MCP agent. Scope is set by the operator:
      <span class="mono">AGENTLEDGER_MCP_ROLE=client AGENTLEDGER_MCP_CLIENT=&lt;id&gt;</span> limits it to one client.<br><br>The <b>Any MCP server</b> connector pulls from external MCP servers (QuickBooks, Gmail, Drive, banks) into the normal pipelines.</div></div></div>`;
  main.querySelectorAll("[data-dl]").forEach((b) => b.onclick = () => { const cid = main.querySelector(`[data-exp="${b.dataset.dl}"]`).value; download(`/api/clients/${encodeURIComponent(cid)}/export/${b.dataset.dl}`).catch((err) => toast(err.message)); });
  main.querySelectorAll("[data-build]").forEach((b) => b.onclick = async () => { await api("/api/plugins/request", { method: "POST", body: { connector_id: b.dataset.build, docs: b.dataset.docs } }); toast("Queued for the AI Engineer. You'll approve the result."); b.disabled = true; });
}

// ---------------------------------------------------------------------------------------- audit
async function viewAudit(main) {
  const a = await api("/api/audit");
  main.innerHTML = page("Audit trail", a.verification.ok ? `<span class="pill p-good">⛓ hash chain verified · ${a.verification.checked} events · head ${esc(a.verification.head.slice(0, 12))}…</span>` : `<span class="pill p-bad">chain broken at event ${a.verification.broken_at}</span>`) +
    `<div class="card"><div class="muted small" style="margin-bottom:8px">The same trail is visible to the CPA and the client: AI answers, overrides, adoptions, filings and resolutions. It cannot be edited.</div>
    <table><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr>${a.events.map((e) => `<tr><td class="small mono">${esc(e.at.slice(0, 19).replace("T", " "))}</td>
      <td class="small"><b>${esc(e.actor)}</b><div class="muted">${esc(e.role)}</div></td><td><span class="pill p-mute">${esc(e.action)}</span>${e.client_id ? `<div class="small muted">${esc(e.client_id)}</div>` : ""}</td>
      <td class="small">${esc(e.payload.title || e.payload.question || e.payload.memo || e.payload.name || e.payload.note || "")}${e.payload.grounded === false ? ' <span class="pill p-warn">unverified answer</span>' : e.payload.grounded ? ' <span class="pill p-good">verified answer</span>' : ""}</td></tr>`).join("")}</table></div>`;
}

boot();

// ---------------------------------------------------------------------------------------- firm & platform administration
async function viewTeam(main) {
  const users = await api("/api/auth/users");
  const admin = S.me.base_role === "firm_admin";
  main.innerHTML = page("Team & access", "Everyone who can sign in to this firm. Every account uses two-step verification.") +
    `<div class="card"><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Two-step</th><th>Last sign-in</th><th></th></tr>
    ${users.map((u) => `<tr><td>${esc(u.name)}</td><td>${esc(u.email)}</td><td>${esc(u.role)}${u.client_id ? ` · ${esc(u.client_id)}` : ""}</td>
      <td>${u.mfa_enrolled_at ? "on" : "pending"}</td><td>${esc((u.last_login_at || "").slice(0, 16))}</td>
      <td>${admin && u.id !== S.me.id ? `<button class="btn" data-dis="${esc(u.id)}" data-v="${u.disabled ? 0 : 1}">${u.disabled ? "Enable" : "Disable"}</button>` : ""}</td></tr>`).join("")}</table></div>
    <div class="card"><h3>Invite someone</h3><form id="inv">
      <input name="email" type="email" placeholder="email" required>
      <select name="role">${admin ? `<option value="cpa">CPA</option><option value="staff">Staff</option><option value="firm_admin">Firm administrator</option>` : ""}<option value="client">Client</option></select>
      <select name="client_id"><option value="">(for client users) choose client</option>${S.clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("")}</select>
      <button class="btn primary">Create invitation</button></form><p id="invout" class="small"></p></div>`;
  $("#inv").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    if (!f.client_id) delete f.client_id;
    try {
      const r = await api("/api/auth/invite", { method: "POST", body: f });
      $("#invout").innerHTML = `Send this link to ${esc(f.email)} (valid 7 days, single use):<br><code>${esc(location.origin + "/#/accept/" + r.invite_token)}</code>`;
    } catch (err) { $("#invout").textContent = err.message; }
  };
  main.querySelectorAll("[data-dis]").forEach((b) => b.onclick = async () => {
    await api(`/api/auth/users/${b.dataset.dis}/disable`, { method: "POST", body: { disabled: b.dataset.v === "1" } }); route();
  });
}

async function viewFirms(main) {
  const firms = await api("/api/platform/firms");
  main.innerHTML = page("Firms", "Each firm has its own database, document vault and encryption key.") +
    `<div class="card"><table><tr><th>Firm</th><th>Id</th><th>Status</th><th>Since</th></tr>
    ${firms.map((f) => `<tr><td>${esc(f.name)}</td><td><code>${esc(f.id)}</code></td><td>${esc(f.status)}</td><td>${esc(f.created_at.slice(0, 10))}</td></tr>`).join("")}</table></div>
    <div class="card"><h3>Onboard a firm</h3><form id="nf"><input name="name" placeholder="Firm name" required>
      <input name="id" placeholder="firm-id (lowercase)" pattern="[a-z0-9][a-z0-9-]{1,40}" required>
      <input name="admin_email" type="email" placeholder="administrator email" required>
      <button class="btn primary">Create firm</button></form><p id="nfout" class="small"></p></div>`;
  $("#nf").onsubmit = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/platform/firms", { method: "POST", body: Object.fromEntries(new FormData(e.target)) });
      $("#nfout").innerHTML = `Firm created. Send the administrator this link:<br><code>${esc(location.origin + "/#/accept/" + r.admin_invite_token)}</code>`;
    } catch (err) { $("#nfout").textContent = err.message; }
  };
}

async function viewSecurity(main) {
  const ev = await api("/api/auth/events");
  main.innerHTML = page("Sign-in activity", "Append-only record of sign-ins, failures, invitations and access changes.") +
    `<div class="card"><table><tr><th>When (UTC)</th><th>Event</th><th>User</th><th>From</th><th>Detail</th></tr>
    ${ev.map((e) => `<tr><td>${esc(e.at)}</td><td>${esc(e.event)}</td><td>${esc(e.email || e.user_id || "")}</td><td>${esc(e.ip || "")}</td><td>${esc(e.detail || "")}</td></tr>`).join("")}</table></div>`;
}
