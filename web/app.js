/* Startup Screening Desk: the screen. Plain JavaScript, no build step, no outside libraries. */
"use strict";

const S = {
  status: null, batch: null, runId: null, run: null, events: [], lastSeq: 0, running: false, t0: 0,
  tab: null, saveTimer: null, clockTimer: null, uploadErrors: {}, calcTimers: {},
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? String(u) : "");
const LETTERS = "ABCDEF";
// Shown on every result of the sample batch, recorded or live (18 Nov 2026: the recorded run said nowhere on screen that
// the three companies are invented, so a viewer could not tell; the load toast disappears and a replay never shows it).
const SAMPLE_NOTE = "Sample batch: the three companies, their people, investors, figures and documents are invented for this demo.";

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* not JSON */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function toast(text, ms = 2600) {
  const t = $("#toast");
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, ms);
}

function money(n) {
  if (n === null || n === undefined) return "not sized";
  const v = Number(n);
  if (Math.abs(v) >= 1e6) return "S$" + (v / 1e6).toFixed(2).replace(/\.?0+$/, "") + " million";
  if (Math.abs(v) >= 1e3) return "S$" + Math.round(v / 1e3).toLocaleString("en-SG") + "k";
  return "S$" + Math.round(v).toLocaleString("en-SG");
}

function parseMoney(text) {
  const t = String(text || "").toLowerCase().replace(/s\$|\$|,|\s/g, "");
  const m = t.match(/^(\d+(?:\.\d+)?)(k|m|mn|million|b|bn|billion)?$/);
  if (!m) return null;
  const scale = { k: 1e3, m: 1e6, mn: 1e6, million: 1e6, b: 1e9, bn: 1e9, billion: 1e9 }[m[2]] || 1;
  return parseFloat(m[1]) * scale;
}

const reviewer = () => ($("#reviewer").value || "").trim();
// a draft is signed with the reviewer's own name; with no name typed the sign-off line is left off
const signed = (text) => String(text || "").replace(/\n?\{\{reviewer\}\}/g, reviewer() ? "\n" + reviewer() : "").trimEnd();

/* ------------------------------------------------------------------ boot */

async function boot() {
  S.status = await api("/api/status");
  const st = S.status;
  $("#policyDate").textContent = st.policy.read_on;
  const mode = $("#mode");
  if (st.live.ok) { mode.className = "pill live"; mode.textContent = "Live runs on"; mode.title = "The model API is reachable from this machine."; }
  else { mode.className = "pill off"; mode.textContent = "Live runs off"; mode.title = st.live.reason || ""; }
  for (const id of ["judgeModel", "readerModel"]) {
    $("#" + id).innerHTML = st.models.map((m) => `<option value="${esc(m.id)}">${esc(m.label)} (${esc(m.note)})</option>`).join("");
  }
  $("#judgeModel").value = st.defaults.judge_model;
  $("#readerModel").value = st.defaults.reader_model;
  $("#maxSearches").value = String(st.defaults.max_searches);
  $("#reviewer").value = localStorage.getItem("ssd.reviewer") || "";
  $("#recordedBtn").hidden = !st.recording;
  // A running server keeps the code it started with. If the files on disk are newer (or the server is too old
  // to say), a live run would use the old version, so the screen asks for a restart instead.
  S.stale = !st.code || st.code.stale;
  if (S.stale) {
    const b = $("#banner");
    b.hidden = false;
    b.textContent = "This window is running an older version of the app than the files on disk. Close the terminal window that started it, run the start line again, then reload this page. Past runs and the recorded run still open.";
  } else if (!st.live.ok) {
    const b = $("#banner");
    b.hidden = false;
    b.textContent = (st.live.reason || "Live runs are off.") + (st.recording ? " Start will play the recorded run instead." : "");
  }
  const saved = localStorage.getItem("ssd.batch");
  let batch = null;
  if (saved) { try { batch = await api("/api/batch/" + encodeURIComponent(saved)); } catch (e) { batch = null; } }
  S.batch = batch || await api("/api/batch", { method: "POST" });
  localStorage.setItem("ssd.batch", S.batch.id);
  renderSetup();
  bind();
}

function fitHeader() {
  // the comparison table's heading row sticks just under the top bar, whatever height that bar is
  document.documentElement.style.setProperty("--top-h", $(".top").offsetHeight + "px");
}

function bind() {
  fitHeader();
  window.addEventListener("resize", fitHeader);
  $("#sampleBtn").addEventListener("click", loadSample);
  $("#clearBtn").addEventListener("click", clearBatch);
  $("#startBtn").addEventListener("click", () => start(S.status.live.ok ? "live" : "recorded"));
  $("#recordedBtn").addEventListener("click", () => start("recorded"));
  $("#runsBtn").addEventListener("click", showRuns);
  $("#modalClose").addEventListener("click", () => { $("#modal").hidden = true; });
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
  $("#reviewer").addEventListener("change", () => localStorage.setItem("ssd.reviewer", reviewer()));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { $("#pop").hidden = true; $("#modal").hidden = true; } });
  document.addEventListener("click", onDocClick);
}

/* ------------------------------------------------------------------ 1. set-up */

function fileMeta(d) {
  const size = d.size >= 1048576 ? (d.size / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(d.size / 1024)) + " KB";
  return (d.pages ? d.pages + " p, " : "") + size;
}

function renderSetup() {
  const b = S.batch;
  const cards = b.startups.map((s) => `
    <div class="scard" data-sid="${esc(s.id)}">
      <div class="scard-head"><span class="letter">${esc(s.id)}</span>
        <input class="name" type="text" data-f="name" placeholder="Start-up name" value="${esc(s.name)}" maxlength="120" aria-label="Name of start-up ${esc(s.id)}">
        ${b.startups.length > 1 ? `<button class="btn ghost" type="button" data-act="remove" title="Remove this start-up">Remove</button>` : ""}
      </div>
      <label>Website (optional)<input type="text" data-f="website" placeholder="example.com" value="${esc(s.website)}" maxlength="200"></label>
      <label>One line on what it does (public, safe to search)<textarea data-f="one_liner" maxlength="300" placeholder="What the company sells, in one sentence">${esc(s.one_liner)}</textarea></label>
      <div class="drop" data-act="pick" tabindex="0" role="button" aria-label="Upload documents for start-up ${esc(s.id)}">
        <strong>Upload documents</strong> or drop them here<br><small>PDF, Word, PowerPoint, Excel, text, CSV, up to ${S.status.limits.upload_mb} MB each</small>
        <input type="file" multiple hidden accept="${esc(S.status.limits.types.join(","))}">
      </div>
      <ul class="files">${s.docs.map((d) => `
        <li><span class="ftype">${esc(d.ext.replace(".", ""))}</span>
          <a href="/api/file/${encodeURIComponent(b.id)}/${encodeURIComponent(s.id)}/${encodeURIComponent(d.id)}" target="_blank" rel="noopener" title="Open ${esc(d.name)}">${esc(d.name)}</a>
          <span class="fmeta">${esc(fileMeta(d))}</span>
          <button type="button" data-act="deldoc" data-doc="${esc(d.id)}" title="Remove ${esc(d.name)}" aria-label="Remove ${esc(d.name)}">&times;</button></li>`).join("")}
      </ul>
      ${(S.uploadErrors[s.id] || []).map((e) => `<div class="err">${esc(e)}</div>`).join("")}
    </div>`).join("");
  const add = b.startups.length < S.status.limits.startups
    ? `<button class="btn ghost" type="button" id="addBtn" style="align-self:start">+ Add a start-up</button>` : "";
  $("#cards").innerHTML = cards + add;
  if ($("#addBtn")) $("#addBtn").addEventListener("click", addStartup);
  for (const card of $$(".scard")) {
    const sid = card.dataset.sid;
    for (const input of $$("[data-f]", card)) input.addEventListener("input", () => onField(sid, input));
    const drop = $(".drop", card), picker = $("input[type=file]", card);
    drop.addEventListener("click", () => picker.click());
    drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); picker.click(); } });
    picker.addEventListener("change", () => { if (picker.files.length) upload(sid, picker.files); });
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files.length) upload(sid, e.dataTransfer.files); });
    for (const btn of $$("[data-act=deldoc]", card)) btn.addEventListener("click", () => deleteDoc(sid, btn.dataset.doc));
    const rm = $("[data-act=remove]", card);
    if (rm) rm.addEventListener("click", () => removeStartup(sid));
  }
  updateStartHint();
}

function updateStartHint() {
  const named = S.batch.startups.filter((s) => s.name.trim());
  const docs = S.batch.startups.reduce((n, s) => n + s.docs.length, 0);
  const btn = $("#startBtn");
  const live = S.status.live.ok;
  if (S.stale) {
    $("#startHint").textContent = "Restart the app before a live run: this window is running older code than the files on disk.";
    btn.disabled = true;
    return;
  }
  let hint;
  if (!live && S.status.recording) hint = "Live runs are off on this machine, so Start plays the recorded run of the sample batch.";
  else if (!live) hint = "Live runs are off and no recorded run is stored.";
  else if (!named.length) hint = "Name at least one start-up, or load the sample batch.";
  else hint = `Ready: ${named.length} start-up${named.length > 1 ? "s" : ""}, ${docs} document${docs === 1 ? "" : "s"}. Start reads the documents, searches the web, compares and recommends. It takes one to two minutes.`;
  const bare = named.filter((s) => !s.website.trim() && !s.one_liner.trim() && !s.docs.length).map((s) => s.name.trim());
  if (live && bare.length) hint += ` ${bare.join(", ")} ${bare.length > 1 ? "have" : "has"} a name only: add the website, one line or a document, so the search finds the right company and has more to go on.`;
  $("#startHint").textContent = hint;
  btn.disabled = S.running || (live ? !named.length : !S.status.recording);
}

function onField(sid, input) {
  const s = S.batch.startups.find((x) => x.id === sid);
  s[input.dataset.f] = input.value;
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(saveBatch, 500);
  updateStartHint();
}

async function saveBatch() {
  clearTimeout(S.saveTimer);
  S.saveTimer = null;
  const body = { startups: S.batch.startups.map((s) => ({ id: s.id, name: s.name, website: s.website, one_liner: s.one_liner, country: s.country || "" })) };
  S.batch = await api("/api/batch/" + encodeURIComponent(S.batch.id), { method: "PUT", body });
}

async function loadSample() {
  try {
    S.batch = await api("/api/batch/sample", { method: "POST" });
    localStorage.setItem("ssd.batch", S.batch.id);
    S.uploadErrors = {};
    renderSetup();
    toast("Sample batch loaded: three invented companies with the documents they 'sent'.");
  } catch (e) { toast(e.message); }
}

async function clearBatch() {
  S.batch = await api("/api/batch", { method: "POST" });
  localStorage.setItem("ssd.batch", S.batch.id);
  S.uploadErrors = {};
  renderSetup();
}

async function addStartup() {
  if (S.saveTimer) await saveBatch();
  const used = new Set(S.batch.startups.map((s) => s.id));
  const letter = LETTERS.split("").find((l) => !used.has(l));
  if (!letter) return;
  S.batch.startups.push({ id: letter, name: "", website: "", one_liner: "", country: "", docs: [] });
  await saveBatch();
  renderSetup();
}

async function removeStartup(sid) {
  S.batch.startups = S.batch.startups.filter((s) => s.id !== sid);
  await saveBatch();
  renderSetup();
}

async function upload(sid, fileList) {
  if (S.saveTimer) await saveBatch();
  const form = new FormData();
  for (const f of fileList) form.append("files", f, f.name);
  try {
    const out = await api(`/api/batch/${encodeURIComponent(S.batch.id)}/startups/${encodeURIComponent(sid)}/docs`, { method: "POST", body: form });
    S.batch = out.batch;
    S.uploadErrors[sid] = out.errors;
  } catch (e) { S.uploadErrors[sid] = [e.message]; }
  renderSetup();
}

async function deleteDoc(sid, docId) {
  if (S.saveTimer) await saveBatch();
  S.batch = await api(`/api/batch/${encodeURIComponent(S.batch.id)}/startups/${encodeURIComponent(sid)}/docs/${encodeURIComponent(docId)}`, { method: "DELETE" });
  renderSetup();
}

/* ------------------------------------------------------------------ 2. run and progress */

async function start(mode) {
  if (S.running) return;
  try {
    if (S.saveTimer) await saveBatch();
    const body = mode === "recorded" ? { mode: "recorded" } : {
      mode: "live", batch_id: S.batch.id, judge_model: $("#judgeModel").value, reader_model: $("#readerModel").value,
      web: $("#webOn").checked, max_searches: parseInt($("#maxSearches").value, 10),
    };
    const out = await api("/api/runs", { method: "POST", body });
    S.runId = out.run_id;
    S.events = [];
    S.lastSeq = 0;
    S.run = await api("/api/runs/" + encodeURIComponent(S.runId));
    S.t0 = Date.now();
    setRunning(true);
    $("#results").hidden = true;
    $("#results").innerHTML = "";
    $("#progress").hidden = false;
    renderProgress();
    $("#progress").scrollIntoView({ behavior: "smooth", block: "start" });
    poll();
  } catch (e) { toast(e.message, 5000); }
}

function setRunning(on) {
  S.running = on;
  const btn = $("#startBtn");
  btn.classList.toggle("running", on);
  btn.lastChild.textContent = on ? "Running" : "Start";
  clearInterval(S.clockTimer);
  if (on) S.clockTimer = setInterval(tick, 500);
  updateStartHint();
}

function tick() {
  const s = Math.round((Date.now() - S.t0) / 1000);
  $("#clock").textContent = `${S.run && S.run.mode === "recorded" ? "Recorded run, playing back. " : ""}${s} s elapsed`;
}

async function poll() {
  try {
    const out = await api(`/api/runs/${encodeURIComponent(S.runId)}/events?after=${S.lastSeq}`);
    if (out.events.length) {
      S.events.push(...out.events);
      S.lastSeq = out.events[out.events.length - 1].seq;
      renderProgress();
    }
    if (out.status === "running") { setTimeout(poll, 600); return; }
    setRunning(false);
    S.run = await api("/api/runs/" + encodeURIComponent(S.runId));
    renderProgress();
    if (S.run.status === "done") {
      const u = S.run.usage || {};
      $("#clock").textContent = S.run.mode === "recorded"
        ? "Recorded run played back."
        : `Done in ${Math.round(S.run.result.duration_s)} s. ${u.searches || 0} web searches, about US$${(u.usd || 0).toFixed(2)}.`;
      S.tab = null;
      renderResults();
      $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
      S.status = await api("/api/status");
      $("#recordedBtn").hidden = !S.status.recording;
    } else {
      $("#clock").textContent = "The run stopped.";
      toast(S.run.error || "The run failed.", 7000);
    }
  } catch (e) {
    setTimeout(poll, 1500);
  }
}

const STEPS = [
  ["docs", "Read the documents"],
  ["web", "Search the public web"],
  ["profile", "Build the profile"],
  ["support", "Map what it needs from the team"],
  ["check", "Check facts against sources"],
];
const ICON = { pending: "", running: "", done: "&#10003;", skip: "&ndash;", error: "!", warn: "!" };

function stepState(lane, step) {
  const evs = S.events.filter((e) => e.lane === lane && e.step === step);
  if (!evs.length) return { state: "pending", text: "", queries: [] };
  const last = evs[evs.length - 1];
  const state = { start: "running", info: "running", done: "done", skip: "skip", error: "error", warn: "running" }[last.status] || "running";
  const closing = evs.filter((e) => ["done", "skip", "error"].includes(e.status)).pop();
  const lines = evs.filter((e) => e.status === "info" && !(e.data && e.data.query)).map((e) => e.text);
  return {
    state, text: closing ? closing.text : (lines.length ? lines[lines.length - 1] : last.text),
    queries: evs.filter((e) => e.data && e.data.query).map((e) => e.data.query),
  };
}

function renderProgress() {
  if (!S.run) return;
  const startups = S.run.batch.startups.filter((s) => (s.name || "").trim());
  // a finished run that never had a step (an older recording) does not show that step as waiting
  const steps = STEPS.filter(([key]) => S.run.status === "running" || S.events.some((e) => e.step === key));
  $("#lanes").innerHTML = startups.map((s) => `
    <div class="lane"><h3><span class="mini">${esc(s.id)}</span>${esc(s.name)}</h3>
      ${steps.map(([key, label]) => {
        const st = stepState(s.id, key);
        return `<div class="steprow"><span class="icon ${st.state}">${ICON[st.state]}</span>
          <div><div class="label">${label}</div>
          ${st.queries.length ? `<ul class="queries">${st.queries.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : ""}
          <div class="msg">${esc(st.text)}</div></div></div>`;
      }).join("")}
    </div>`).join("");
  const batch = [["compare", "Compare the start-ups and write the briefing"], ["plans", "Draw up the plans to choose from"]];
  const runError = S.events.filter((e) => e.lane === "all" && e.step === "run" && e.status === "error").pop();
  $("#batchSteps").innerHTML = batch.map(([key, label]) => {
    const st = stepState("all", key);
    return `<div class="lane"><div class="steprow"><span class="icon ${st.state}">${ICON[st.state]}</span>
      <div><div class="label">${label}</div><div class="msg">${esc(st.text)}</div></div></div></div>`;
  }).join("") + (runError ? `<div class="lane"><div class="steprow"><span class="icon error">!</span><div><div class="label">The run stopped</div><div class="msg">${esc(runError.text)}</div></div></div></div>` : "");
}

/* ------------------------------------------------------------------ 3. results: helpers */

const okStartups = () => S.run.result.startups.filter((s) => s.status === "ok");
const byId = (sid) => S.run.result.startups.find((s) => s.id === sid);
const RANK = { quote: 0, cited: 1, check: 2 };

function factMap(s) {
  const out = {};
  for (const f of s.profile.facts) {
    if (!(f.status in RANK)) continue;
    if (!out[f.field] || RANK[f.status] < RANK[out[f.field].status]) out[f.field] = f;
  }
  return out;
}

function chips(s, ids, max = 4) {
  const list = (ids || []).slice(0, max);
  return list.map((id) => `<button type="button" class="chip ${id[0] === "W" ? "w" : ""}" data-ev="${esc(s.id)}:${esc(id)}" title="Show the source" aria-label="Source ${esc(id)}">${esc(id)}</button>`).join("")
    + ((ids || []).length > max ? `<span class="note"> +${ids.length - max}</span>` : "");
}

function mark(item) {
  if (item.status === "cited") return `<span class="mark cited" title="${esc(item.status_why)}">&#9680; cited</span>`;
  if (item.status === "check") return `<span class="mark check" title="${esc(item.status_why)}">&#9888; not found in its source, not used</span>`;
  return "";
}

function factCell(s, key) {
  const f = factMap(s)[key];
  if (!f) return `<span class="none">Not found</span>`;
  return `${esc(f.value_text)}${mark(f)}${chips(s, f.evidence_ids)}${f.note ? `<div class="note">${esc(f.note)}</div>` : ""}`;
}

function listCell(s, items, render, empty = "None found") {
  const good = (items || []).filter((i) => i.status in RANK);
  if (!good.length) return `<span class="none">${empty}</span>`;
  return `<ul>${good.map((i) => `<li>${render(i)}${mark(i)}${chips(s, i.evidence_ids, 2)}</li>`).join("")}</ul>`;
}

function decisionOf(sid) {
  return ((S.run.decisions.state.startups || {})[sid]) || {};
}

function gateCounts(s) {
  const c = { pass: 0, fail: 0, unknown: 0 };
  for (const g of s.gates) c[g.status] += 1;
  return c;
}

function recOption(s) {
  return s.sizing.options.find((o) => o.key === s.sizing.recommended) || s.sizing.options[s.sizing.options.length - 1];
}

const workstreams = (res) => (res.policy.workstreams || []);
// the app's first-meeting read is yes, no, or no call (too little was found to judge)
const readOf = (s) => (s.meeting.recommend === true ? "yes" : s.meeting.recommend === false ? "no" : "none");
const READ_LABEL = { yes: "Yes", no: "No", none: "No call yet" };
const needsOf = (s) => ((s.support || {}).needs || []);
const bringOf = (s) => ((s.support || {}).who_to_bring || []);

function sourceById(id) {
  return (S.run.result.policy.sources || []).find((x) => x.id === id);
}

function srcLink(id) {
  const src = sourceById(id);
  if (!src) return "";
  return ` <a href="${esc(safeUrl(src.url))}" target="_blank" rel="noopener noreferrer" title="${esc(src.title)} (page updated ${esc(src.page_updated)})">source</a>`;
}

/* ------------------------------------------------------------------ 3. results: render */

function renderResults() {
  const run = S.run, res = run.result;
  const ok = okStartups();
  if (!S.tab || !ok.find((s) => s.id === S.tab)) S.tab = (res.ranking && res.ranking[0]) || (ok[0] && ok[0].id);
  const failed = res.startups.filter((s) => s.status !== "ok");
  const rec = res.recorded;
  const cmp = res.comparison || {};
  const html = `
    <section class="panel">
      <div class="panel-head"><div><span class="step">3</span><h2>Result</h2></div>
        <div class="actions"><a class="btn" href="/api/runs/${encodeURIComponent(run.id)}/memo">Download memo</a></div></div>
      ${rec ? `<div class="banner info" style="margin:0 0 12px">Recorded run. This is a replay of the live run of ${esc((rec.recorded_at || "").replace("T", " ").slice(0, 16))} (judge ${esc(rec.options.judge_model)}, reader ${esc(rec.options.reader_model)}, about US$${Number(rec.usage.usd || 0).toFixed(2)}). Nothing was sent to the internet just now.${(res.added_later || []).map((a) => ` ${esc(a.note)}`).join("")}</div>` : ""}
      ${run.batch && run.batch.sample ? `<div class="banner info" style="margin:0 0 12px">${esc(run.batch.note || SAMPLE_NOTE)}</div>` : ""}
      ${cmp.headline ? `<p class="headline">${esc(cmp.headline)}</p>` : ""}
      ${(cmp.what_separates_them || []).length ? `<ul class="sep">${cmp.what_separates_them.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${(cmp.flags || []).length ? `<div class="flagbox"><strong>Check before relying on the briefing text:</strong> ${cmp.flags.map(esc).join(" ")}</div>` : ""}
      ${failed.length ? `<div class="flagbox">${failed.map((s) => `${esc(s.name)} could not be profiled: ${esc(s.error || "no evidence was found")}.`).join(" ")}</div>` : ""}
      ${res.weights ? `<p class="note" style="margin-top:8px">Scores below use your weights. The briefing text was written for the default weights.</p>` : ""}
    </section>
    ${plansPanel(res)}
    ${comparePanel(ok, res)}
    ${scoresPanel(ok, res)}
    ${detailPanel(ok)}
    ${logPanel(run)}
    ${basisPanel(res)}`;
  const root = $("#results");
  root.innerHTML = html;
  root.hidden = false;
  for (const s of ok) if (s.id === S.tab) initCalc(s);
}

function plansPanel(res) {
  const accepted = S.run.decisions.state.plan;
  return `<section class="panel"><div class="panel-head"><div><h2>Recommendations: choose one</h2></div></div>
    <p class="hint">${res.plans.length === 1 ? "One way to act on this batch came out of the rules." : `${res.plans.length === 2 ? "Two" : "Three"} ways to act on this batch.`} Accepting one records a first-meeting decision and a capital decision for every start-up; you can still change any of them below.</p>
    <div class="plans">${res.plans.map((p) => `
      <div class="plan ${p.key === "recommended" ? "rec" : ""} ${accepted === p.key ? "accepted" : ""}">
        ${accepted === p.key ? `<span class="tag">Accepted</span>` : (p.key === "recommended" ? `<span class="tag">Recommended</span>` : `<span class="tag alt">Alternative</span>`)}
        <h3>${esc(p.label)}</h3>
        ${p.rationale ? `<p class="why">${esc(p.rationale)}</p>` : ""}
        <ul>${p.items.map((i) => `<li><span class="mini">${esc(i.id)}</span>
          <div><div>${esc(i.name)}: ${i.meeting === null ? `<span class="yn">get documents first</span>` : `<span class="yn ${i.meeting ? "y" : "n"}">${i.meeting ? "meet" : "no meeting"}</span>`}</div><div class="sub">${esc(i.option_label)}</div></div>
          <span class="amt">${i.amount === null ? "to size" : esc(money(i.amount))}</span></li>`).join("")}</ul>
        <div class="total"><span>${p.meetings} first meeting${p.meetings === 1 ? "" : "s"}${p.undecided ? `, ${p.undecided} awaiting documents` : ""}</span><strong>${esc(money(p.total))} indicative</strong></div>
        <button class="btn ${accepted === p.key ? "" : "primary"}" type="button" data-plan="${esc(p.key)}">${accepted === p.key ? "Accepted &#10003;" : "Accept this plan"}</button>
      </div>`).join("")}</div></section>`;
}

function comparePanel(ok, res) {
  const rankOf = (s) => (res.ranking || []).indexOf(s.id) + 1;
  const row = (label, fn, title = "") => `<tr><th class="rowh" ${title ? `title="${esc(title)}"` : ""}>${label}</th>${ok.map((s) => `<td>${fn(s)}</td>`).join("")}</tr>`;
  const group = (label) => `<tr class="group"><th colspan="${ok.length + 1}">${label}</th></tr>`;
  const sow = (s) => {
    const rows = (s.share_of_wallet || []).filter((w) => w.pct !== null && w.pct !== undefined);
    if (!rows.length) return `<span class="none">Not found</span>`;
    return `<ul>${rows.map((w) => `<li><strong>${esc(w.pct)}%</strong> of ${esc(w.customer)}'s spend${w.how ? ` <span class="note">(${esc(w.how)})</span>` : ""}${mark(w)}${chips(s, w.evidence_ids, 2)}</li>`).join("")}</ul>`;
  };
  const gates = (s) => {
    const c = gateCounts(s);
    const bad = s.gates.filter((g) => g.status !== "pass");
    return `<span class="st pass">&#10003; ${c.pass} pass</span> ${c.fail ? `<span class="st fail">&#10005; ${c.fail} fail</span>` : ""} ${c.unknown ? `<span class="st unknown">? ${c.unknown} open</span>` : ""}`
      + (bad.length ? `<ul style="margin-top:5px">${bad.map((g) => `<li class="note">${esc(g.detail)}</li>`).join("")}</ul>` : "");
  };
  const meeting = (s) => {
    const d = decisionOf(s.id).meeting;
    return `<span class="yn ${{ yes: "y", no: "n", none: "" }[readOf(s)]}">${READ_LABEL[readOf(s)]}</span> <span class="note">${readOf(s) === "none" ? "too little found: get documents first" : "app's read"}</span>`
      + (d ? `<div><strong>You: ${esc(d.value === "yes" ? "Yes" : "No")}</strong>${d.overrode ? ` <span class="note">(overrides the app)</span>` : ""}</div>` : "");
  };
  const capital = (s) => {
    const o = recOption(s), d = decisionOf(s.id).investment;
    return `<strong>${o.amount === null ? "To size" : esc(money(o.amount))}</strong><div class="note">${esc(o.label)}</div>`
      + (d ? `<div><strong>You: ${esc(d.label)}${d.amount ? ", " + esc(money(d.amount)) : ""}</strong></div>` : "");
  };
  return `<section class="panel"><div class="panel-head"><div><h2>Side by side</h2></div>
      <div class="note">D = uploaded document, W = public web. Click a tag to see the quoted source.</div></div>
    <div><table class="cmp"><thead><tr><th class="rowh"></th>${ok.map((s) => `<th><span class="mini" style="display:inline-grid;margin-right:6px">${esc(s.id)}</span>${esc(s.name)} <span class="rank">#${rankOf(s)}</span></th>`).join("")}</tr></thead><tbody>
      ${group("Verdict")}
      ${row("Score", (s) => `<div class="scoreline"><b>${s.scores.total}</b><div class="meter" role="img" aria-label="Score ${s.scores.total} of 100"><i style="width:${s.scores.total}%"></i></div></div>`, "Weighted score out of 100, from seven criteria")}
      ${row("First meeting", meeting)}
      ${row("Indicative capital", capital, "A ceiling under the published co-investment schedule, not a valuation")}
      ${row("Scheme checks", gates, "Startup SG Equity eligibility, checked by code against the facts found")}
      ${row("Evidence coverage", (s) => `${Math.round(s.coverage.value * 100)}% <span class="note">(${s.coverage.found} of ${s.coverage.of} items found)</span>`, "How much of what the desk looks for was found with a source")}
      ${group("Company")}
      ${row("What it does", (s) => esc(s.profile.one_liner))}
      ${row("Sector", (s) => esc(s.profile.sector_domain || "Not established"))}
      ${row("Stage", (s) => factCell(s, "stage"))}
      ${row("Headcount", (s) => factCell(s, "headcount"))}
      ${row("Products", (s) => listCell(s, s.profile.products, (p) => `${esc(p.name)} <span class="note">(${esc(p.maturity || "unknown")})</span>`))}
      ${row("Research-based IP", (s) => factCell(s, "research_based_ip"))}
      ${group("Traction")}
      ${row("Revenue", (s) => factCell(s, "revenue"))}
      ${row("Customers", (s) => listCell(s, s.profile.customers, (c) => `${esc(c.name)} <span class="note">(${esc(c.relationship)})</span>`))}
      ${row("Share of wallet", sow, "How much of a customer's yearly spend on this category goes to the start-up. Calculated by code from the two figures.")}
      ${group("Round")}
      ${row("This round", (s) => factCell(s, "round_size"))}
      ${row("Lead or co-investor", (s) => factCell(s, "lead_investor"))}
      ${row("Committed", (s) => factCell(s, "coinvestor_commitment"))}
      ${row("Raised to date", (s) => factCell(s, "funding_to_date"))}
      ${group("Market")}
      ${row("Market size", (s) => factCell(s, "tam"))}
      ${row("Estimates found", (s) => listCell(s, s.profile.market, (m) => `<strong>${esc(m.source)}</strong>: ${esc(m.figure)}`))}
      ${row("Competitors", (s) => listCell(s, s.profile.competitors, (c) => `${esc(c.name)}${c.based_in ? ` <span class="note">(${esc(c.based_in)})</span>` : ""}`))}
      ${workstreams(res).length && ok.some((s) => needsOf(s).length) ? group("What it needs from the team") + workstreams(res).map((w) =>
        row(esc(w.label), (s) => listCell(s, needsOf(s).filter((n) => n.workstream === w.key), (n) => esc(n.need), "Nothing in the evidence"), w.covers)).join("") : ""}
    </tbody></table></div></section>`;
}

function scoresPanel(ok, res) {
  const crit = res.policy.criteria;
  const weights = res.weights || Object.fromEntries(crit.map((c) => [c.key, c.weight]));
  const dots = (n) => `<span class="dots" role="img" aria-label="${n} of 5">${[1, 2, 3, 4, 5].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span>`;
  return `<section class="panel"><div class="panel-head"><div><h2>How the score is built</h2></div>
      <div class="actions"><button class="btn ghost" type="button" id="resetWeights" ${res.weights ? "" : "disabled"}>Reset weights</button></div></div>
    <p class="hint">The model rates each criterion from 1 to 5 on the evidence. Code turns the ratings into a score with these weights. Change a weight and the ranking, the first-meeting read and the plans update without another model call.</p>
    <div style="overflow-x:auto"><table class="crit"><thead><tr><th style="width:26%">Criterion</th><th style="width:84px">Weight</th>
      ${ok.map((s) => `<th>${esc(s.id)}. ${esc(s.name)} <span class="bigscore" style="font-size:17px;margin-left:6px">${s.scores.total}</span>${s.scores.rated_n !== undefined && s.scores.rated_n < s.scores.criteria.length ? ` <span class="note">on ${s.scores.rated_n} of ${s.scores.criteria.length} criteria</span>` : ""}</th>`).join("")}</tr></thead><tbody>
      ${crit.map((c) => `<tr><td><strong>${esc(c.label)}</strong><div class="reason">${esc(c.basis)}${c.source ? srcLink(c.source) : ""}</div></td>
        <td><input type="number" min="0" max="100" step="5" value="${esc(weights[c.key])}" data-weight="${esc(c.key)}" aria-label="Weight for ${esc(c.label)}"></td>
        ${ok.map((s) => { const r = s.scores.criteria.find((x) => x.key === c.key); return `<td>${r.score >= 1 ? `${dots(r.score)}<strong>${r.score}</strong>` : `<span class="st unknown">not rated</span>`}${chips(s, r.evidence_ids, 3)}<div class="reason">${esc(r.reason)}</div></td>`; }).join("")}</tr>`).join("")}
    </tbody></table></div></section>`;
}

function detailPanel(ok) {
  const s = ok.find((x) => x.id === S.tab);
  if (!s) return "";
  return `<section class="panel" id="detail"><div class="panel-head"><div><h2>Start-up by start-up</h2></div></div>
    <div class="tabs" role="tablist">${ok.map((x) => `<button class="tab ${x.id === s.id ? "on" : ""}" type="button" role="tab" aria-selected="${x.id === s.id}" data-tab="${esc(x.id)}"><span class="mini">${esc(x.id)}</span>${esc(x.name)}</button>`).join("")}</div>
    <div class="grid2"><div>${leftColumn(s)}</div><div>${rightColumn(s)}</div></div></section>`;
}

function leftColumn(s) {
  const n = s.narrative || {};
  const groups = {};
  for (const f of S.status.fields) (groups[f.group] = groups[f.group] || []).push(f);
  const facts = factMap(s);
  const missing = s.coverage.missing || [];
  const a = s.audit || {};
  const web = a.web || {}, docs = a.docs || {}, checks = a.checks || {};
  const droppedLines = [...(docs.dropped_lines || []), ...(web.dropped_lines || [])];
  return `
    <div class="box"><h4>Summary</h4><p>${esc(n.summary || s.profile.one_liner)}</p>
      <div class="grid2" style="margin-top:12px;gap:14px">
        <div><strong>For a first meeting</strong><ul class="sep">${(n.why_meet || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>None given.</li>"}</ul></div>
        <div><strong>Against, or the main risks</strong><ul class="sep">${(n.why_not || []).map((x) => `<li>${esc(x)}</li>`).join("") || "<li>None given.</li>"}</ul></div>
      </div></div>
    <div class="box"><h4>Facts found <small>with the source behind each</small></h4>
      ${Object.entries(groups).map(([g, fs]) => { const rows = fs.filter((f) => facts[f.key]); return rows.length ? `<p class="note" style="margin:8px 0 4px;font-weight:600">${esc(g)}</p><dl class="kv">${rows.map((f) => `<dt>${esc(f.label)}</dt><dd>${factCell(s, f.key)}</dd>`).join("")}</dl>` : ""; }).join("")}
      ${missing.length ? `<p class="note" style="margin:10px 0 4px;font-weight:600">Not found, so asked in the first meeting</p><p>${missing.map((m) => esc(m.label)).join(", ")}</p>` : ""}
    </div>
    <div class="box"><h4>People, products and customers</h4>
      <p class="note" style="font-weight:600">Management team</p>${listCell(s, s.profile.team, (t) => `<strong>${esc(t.name)}</strong>, ${esc(t.role)}<div class="sub note">${esc(t.background)}</div>`)}
      <p class="note" style="font-weight:600;margin-top:8px">Products</p>${listCell(s, s.profile.products, (p) => `<strong>${esc(p.name)}</strong> <span class="note">(${esc(p.maturity || "unknown")})</span>: ${esc(p.description)}`)}
      <p class="note" style="font-weight:600;margin-top:8px">Customers</p>${listCell(s, s.profile.customers, (c) => `<strong>${esc(c.name)}</strong> <span class="note">(${esc(c.relationship)})</span>: ${esc(c.detail)}`)}
    </div>
    <div class="box"><h4>Market and competitors</h4>
      <p class="note" style="font-weight:600">Market-size estimates found</p>${listCell(s, s.profile.market, (m) => `<strong>${esc(m.source)}</strong>: ${esc(m.figure)} <span class="note">(${esc(m.scope)})</span>`)}
      <p class="note" style="font-weight:600;margin-top:8px">Competitors</p>${listCell(s, s.profile.competitors, (c) => `<strong>${esc(c.name)}</strong>${c.based_in ? ` <span class="note">(${esc(c.based_in)})</span>` : ""}: ${esc(c.what_they_do)}<div class="sub note">How ${esc(s.name)} differs: ${esc(c.how_startup_differs || "not stated")}</div>`)}
      <p class="note" style="font-weight:600;margin-top:8px">Risks</p>${listCell(s, s.profile.risks, (r) => esc(r.risk))}
    </div>
    <div class="box"><h4>How this was gathered <small>the audit trail</small></h4>
      <dl class="kv">
        <dt>Documents read</dt><dd>${s.docs.length ? s.docs.map((d) => esc(d.name)).join(", ") : "None uploaded"}</dd>
        <dt>Web searches</dt><dd>${(web.searches || []).length ? `<ul class="srcs">${web.searches.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>` : "None"}</dd>
        <dt>Evidence kept</dt><dd>${s.evidence.filter((e) => e.kind === "doc").length} from documents, ${s.evidence.filter((e) => e.kind === "web").length} from the web, each with a quoted passage</dd>
        <dt>Source check (code)</dt><dd><span class="st quote">&#10003; ${checks.quote || 0} shown in the quote</span> <span class="st cited">&#9680; ${checks.cited || 0} cited</span> ${checks.check ? `<span class="st check">&#9888; ${checks.check} flagged</span>` : ""} ${checks.unsupported ? `<span class="st check">${checks.unsupported} dropped, no source</span>` : ""}</dd>
        <dt>Thrown away</dt><dd>${droppedLines.length ? `<details><summary>${droppedLines.length} statement${droppedLines.length === 1 ? "" : "s"} the reader wrote with no source behind ${droppedLines.length === 1 ? "it" : "them"}</summary><ul class="srcs">${droppedLines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>` : "Nothing: every statement had a source"}</dd>
        ${(s.notes || []).length ? `<dt>Researcher's notes</dt><dd><ul class="srcs">${s.notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></dd>` : ""}
      </dl></div>`;
}

function rightColumn(s) {
  const d = decisionOf(s.id);
  const meet = d.meeting, inv = d.investment;
  const pack = s.pack || {};
  const n = s.narrative || {};
  return `
    <div class="box decide ${meet ? "done" : ""}">
      <p class="q">First meeting with stakeholders: ${esc(s.name)}?</p>
      <p class="suggest">The app's read: <strong>${READ_LABEL[readOf(s)]}</strong>. ${esc(s.meeting.reasons.join(" "))}</p>
      ${bringOf(s).length ? `<p class="suggest" style="margin-top:4px">Worth having in the room: ${bringOf(s).map(esc).join("; ")}.</p>` : ""}
      <div class="yesno">
        <button class="btn big yes ${meet && meet.value === "yes" ? "on" : ""}" type="button" data-meet="yes" data-sid="${esc(s.id)}">Yes</button>
        <button class="btn big no ${meet && meet.value === "no" ? "on" : ""}" type="button" data-meet="no" data-sid="${esc(s.id)}">No</button>
        ${meet ? `<span class="suggest">Recorded: <strong>${meet.value === "yes" ? "Yes" : "No"}</strong> by ${esc(meet.by)}${meet.overrode ? ", overriding the app" : ""}.</span>` : `<span class="suggest">Your call. Nothing is sent to anyone.</span>`}
      </div>
    </div>
    <div class="box"><h4>How much to invest <small>indicative, under the published schedule</small></h4>
      <p class="note">${esc(s.sizing.why)}</p>
      <div class="opts">${s.sizing.options.map((o) => `
        <div class="opt ${o.key === s.sizing.recommended ? "rec" : ""} ${inv && inv.value === o.key ? "on" : ""}">
          <div><span class="oname">${esc(o.label)}</span>${o.key === s.sizing.recommended ? `<span class="otag">app's pick</span>` : ""}</div>
          <div class="oamt">${o.amount === null ? "to size" : esc(money(o.amount))}</div>
          <div class="obasis">${esc(o.basis)}</div>
          <div style="grid-column:1/-1"><button class="btn ${inv && inv.value === o.key ? "" : "primary"}" type="button" data-invest="${esc(o.key)}" data-sid="${esc(s.id)}">${inv && inv.value === o.key ? "Accepted &#10003;" : "Accept"}</button></div>
        </div>`).join("")}</div>
      ${s.sizing.assumptions.length ? `<p class="note" style="margin-top:8px">Assumed: ${s.sizing.assumptions.map(esc).join(" ")}</p>` : ""}
      ${s.sizing.conflict ? `<div class="flagbox">${esc(s.sizing.conflict)}</div>` : ""}
    </div>
    ${!meet && readOf(s) === "none" && pack.request_draft ? `<div class="box"><h4>Ask for documents first <small>draft only, nothing is sent</small></h4>
      <p class="note">Little was found in public. This asks the company for exactly what is missing; upload what comes back and press Start again.
        <button class="btn ghost" type="button" data-copy="request" data-sid="${esc(s.id)}" style="padding:2px 8px;margin-left:6px">Copy</button></p>
      <pre class="draft">${esc(signed(pack.request_draft))}</pre></div>` : ""}
    ${meet ? `<div class="box"><h4>${meet.value === "yes" ? "First-meeting pack" : "If you pass"} <small>drafts only, nothing is sent</small></h4>
      ${meet.value === "yes" ? `
        ${bringOf(s).length ? `<p class="note" style="font-weight:600">Who to bring</p><ul class="sep" style="margin:2px 0 10px">${bringOf(s).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        <p class="note" style="font-weight:600">Questions to ask</p><ol class="qs">${(pack.questions || []).map((q) => `<li>${esc(q)}</li>`).join("")}</ol>
        <p class="note" style="font-weight:600;margin-top:10px">Agenda (30 minutes)</p><ul class="sep" style="margin-top:2px">${(pack.agenda || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
        <p class="note" style="font-weight:600;margin-top:10px">Draft invitation <button class="btn ghost" type="button" data-copy="invite" data-sid="${esc(s.id)}" style="padding:2px 8px;margin-left:6px">Copy</button></p><pre class="draft">${esc(signed(pack.invite_draft))}</pre>`
      : `
        ${(n.revisit_triggers || []).length ? `<p class="note" style="font-weight:600">Look again if</p><ul class="sep" style="margin-top:2px">${n.revisit_triggers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        <p class="note" style="font-weight:600;margin-top:10px">Draft reply <button class="btn ghost" type="button" data-copy="decline" data-sid="${esc(s.id)}" style="padding:2px 8px;margin-left:6px">Copy</button></p><pre class="draft">${esc(signed(pack.decline_draft))}</pre>`}
    </div>` : `<div class="box"><h4>Questions for a first meeting <small>from what could not be found</small></h4><ol class="qs">${(pack.questions || []).map((q) => `<li>${esc(q)}</li>`).join("") || "<li>None.</li>"}</ol></div>`}
    ${needsOf(s).length ? `<div class="box"><h4>What it needs from the team <small>from the evidence, by workstream</small></h4>
      <ul class="list">${needsOf(s).map((n) => `<li><strong>${esc(n.workstream_label)}</strong>: ${esc(n.need)}${mark(n)}${chips(s, n.evidence_ids, 3)}
        ${n.who_could_help ? `<div class="sub">Who could help: ${esc(n.who_could_help)}</div>` : ""}</li>`).join("")}</ul>
      <p class="note" style="margin-top:8px">Kinds of people and organisations only. The app never names a contact: matching to real stakeholders is the team's call.</p></div>` : ""}
    <div class="box"><h4>Scheme checks <small>Startup SG Equity, checked by code</small></h4>
      <ul class="list">${s.gates.map((g) => `<li class="gate"><span class="st ${g.status}">${g.status === "pass" ? "&#10003; pass" : g.status === "fail" ? "&#10005; fail" : "? open"}</span>
        <div>${esc(g.label)}${g.hard ? "" : ` <span class="note">(not a hard stop)</span>`}${chips(s, g.evidence_ids, 3)}<div class="sub">${esc(g.detail)}${g.note ? " " + esc(g.note) : ""}${g.source ? srcLink(g.source) : ""}</div></div></li>`).join("")}</ul>
    </div>
    <div class="box"><h4>Co-investment calculator <small>try a different commitment</small></h4>
      <div class="calc" data-calc="${esc(s.id)}">
        <label>Co-investor commits (S$)<input type="text" data-c="co" value="${esc(Math.round(s.sizing.co_commitment || 0).toLocaleString("en-SG"))}" inputmode="decimal"></label>
        <label>Earlier government equity (S$)<input type="text" data-c="prior" value="${esc(Math.round(s.sizing.prior_gov || 0).toLocaleString("en-SG"))}" inputmode="decimal"></label>
        <label class="check"><input type="checkbox" data-c="first" ${s.sizing.first_round ? "checked" : ""}> First institutional round</label>
      </div>
      <div id="calcOut-${esc(s.id)}"></div>
    </div>`;
}

function logPanel(run) {
  const rows = run.decisions.log;
  const nameOf = (sid) => (byId(sid) || {}).name || "";
  const what = (d) => d.kind === "plan" ? `Plan accepted: ${esc(d.label)}` : `${esc(d.startup_id)}. ${esc(nameOf(d.startup_id))}: ${esc(d.label)}${d.amount ? ", " + esc(money(d.amount)) : ""}`;
  return `<section class="panel"><div class="panel-head"><div><h2>Decision log</h2></div>
      <div class="actions">${rows.length ? `<button class="btn ghost" type="button" id="resetDecisions">Clear decisions</button>` : ""}<a class="btn" href="/api/runs/${encodeURIComponent(run.id)}/memo">Download memo</a></div></div>
    ${rows.length ? `<table class="log"><thead><tr><th>When</th><th>Who</th><th>Decision</th><th>Against the app's read</th></tr></thead><tbody>
      ${rows.map((d) => `<tr><td>${esc(d.ts.replace("T", " ").slice(0, 19))}</td><td>${esc(d.by)}</td><td>${what(d)}</td><td>${d.overrode ? `<span class="st cited">override</span>` : `<span class="note">${d.recommended === "none" ? "the app made no call" : "same"}</span>`}</td></tr>`).join("")}
    </tbody></table>` : `<p class="hint">No decisions yet. Accept a plan above, or press Yes or No for a start-up.</p>`}</section>`;
}

function basisPanel(res) {
  const p = res.policy;
  const tiers = (t) => t.tiers.map((x) => `${x.gov}:${x.co} up to ${money(x.upto)}${x.first_round_only ? " (first institutional round; otherwise 1:1)" : ""}`).join("; ");
  return `<section class="panel"><div class="panel-head"><div><h2>Basis</h2></div><div class="note">Read on ${esc(p.read_on)}</div></div>
    <p class="hint">Every amount on this page comes from the published ${esc(p.scheme)} schedule, applied by code. The model reads and writes; it never sets an amount.</p>
    <div class="grid2" style="margin-top:12px">
      <div class="box"><h4>The schedule used</h4>
        <dl class="kv">${Object.values(p.tracks).map((t) => `<dt>${esc(t.label)}</dt><dd>Cap ${esc(money(t.cap))} for each start-up. Government to co-investor: ${esc(tiers(t))}.${t.conflict ? `<div class="flagbox">${esc(t.conflict)}</div>` : ""}</dd>`).join("")}</dl>
        <h4 style="margin-top:14px">Decision rules</h4>
        <ul class="srcs"><li>First meeting: yes when no hard requirement fails and the score is ${p.decision_rules.meet_min_score} or more.</li>
          <li>Match in full: score ${p.decision_rules.full_match_min_score} or more, evidence coverage ${Math.round(p.decision_rules.full_match_min_confidence * 100)}% or more, every requirement passing.</li>
          <li>Half now, half on a milestone: score ${p.decision_rules.staged_min_score} or more.</li><li>Otherwise no capital yet.</li></ul>
      </div>
      <div class="box"><h4>Sources</h4><ul class="srcs">${p.sources.map((x) => `<li><a href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener noreferrer">${esc(x.title)}</a> <span class="note">${esc(x.publisher)}, page updated ${esc(x.page_updated)}</span></li>`).join("")}</ul>
        <h4 style="margin-top:14px">Assumptions this app makes</h4><ul class="srcs">${p.assumptions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    </div></section>`;
}

/* ------------------------------------------------------------------ interactions */

async function decide(kind, sid, value) {
  try {
    S.run.decisions = await api(`/api/runs/${encodeURIComponent(S.run.id)}/decisions`, { method: "POST", body: { kind, startup_id: sid || "", value, by: reviewer() } });
    const y = window.scrollY;
    renderResults();
    window.scrollTo(0, y);
  } catch (e) { toast(e.message, 5000); }
}

async function setWeights(weights) {
  try {
    S.run = await api(`/api/runs/${encodeURIComponent(S.run.id)}/weights`, { method: "POST", body: { weights } });
    const y = window.scrollY;
    renderResults();
    window.scrollTo(0, y);
    toast("Re-scored with your weights. No model call was needed.");
  } catch (e) { toast(e.message, 5000); }
}

function onDocClick(e) {
  const pop = $("#pop");
  const chip = e.target.closest("[data-ev]");
  if (chip) { showEvidence(chip); return; }
  if (!e.target.closest("#pop")) pop.hidden = true;
  const t = e.target.closest("button");
  if (!t || !S.run || !S.run.result) return;
  if (t.dataset.plan) decide("plan", "", t.dataset.plan);
  else if (t.dataset.meet) decide("meeting", t.dataset.sid, t.dataset.meet);
  else if (t.dataset.invest) decide("investment", t.dataset.sid, t.dataset.invest);
  else if (t.dataset.tab) { S.tab = t.dataset.tab; const y = window.scrollY; renderResults(); window.scrollTo(0, y); }
  else if (t.id === "resetDecisions") decide("reset", "", "all");
  else if (t.id === "resetWeights") setWeights({});
  else if (t.dataset.copy) {
    const pack = (byId(t.dataset.sid) || {}).pack || {};
    const text = signed({ invite: pack.invite_draft, decline: pack.decline_draft, request: pack.request_draft }[t.dataset.copy]);
    navigator.clipboard.writeText(text || "").then(() => toast("Draft copied. Nothing was sent."), () => toast("Copy failed: select the text and copy it."));
  }
}

document.addEventListener("change", (e) => {
  const w = e.target.closest("[data-weight]");
  if (!w || !S.run) return;
  const weights = {};
  for (const input of $$("[data-weight]")) weights[input.dataset.weight] = Math.max(0, Number(input.value) || 0);
  setWeights(weights);
});

function showEvidence(chip) {
  const [sid, id] = chip.dataset.ev.split(":");
  const s = byId(sid);
  const ev = s && s.evidence.find((x) => x.id === id);
  const pop = $("#pop");
  if (!ev) { pop.hidden = true; return; }
  const where = (q) => {
    if (q.url) {
      const url = safeUrl(q.url);
      return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(q.title || q.label)}</a> <span class="note">${esc(q.label)}</span>` : esc(q.label);
    }
    const run = S.run;
    const href = `/api/file/${encodeURIComponent(run.batch.id)}/${encodeURIComponent(sid)}/${encodeURIComponent(q.doc_id)}?name=${encodeURIComponent(q.doc || "")}${q.page ? "#page=" + q.page : ""}`;
    return `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(q.label)}</a> <span class="note">uploaded document</span>`;
  };
  pop.innerHTML = `<h5>${esc(id)}: ${ev.kind === "web" ? "public web" : "uploaded document"}</h5>
    <div class="claim">${esc(ev.claim)}</div>
    ${ev.matched_by === "code" ? `<div class="note" style="margin-bottom:6px">The reader did not cite this line, so code found the passage that carries its figures.</div>` : ""}
    ${ev.quotes.map((q) => `<blockquote>${esc(q.quote)}</blockquote><div class="where" style="margin-bottom:8px">${where(q)}</div>`).join("")}`;
  pop.hidden = false;
  const r = chip.getBoundingClientRect();
  const width = Math.min(440, window.innerWidth - 24);
  const left = Math.max(12, Math.min(r.left + window.scrollX, window.scrollX + window.innerWidth - width - 12));
  pop.style.left = left + "px";
  pop.style.top = (r.bottom + window.scrollY + 6) + "px";
}

/* ---- co-investment calculator ---- */

function initCalc(s) {
  const root = $(`[data-calc="${s.id}"]`);
  if (!root) return;
  const run = () => runCalc(s, root);
  for (const input of $$("[data-c]", root)) input.addEventListener("input", () => { clearTimeout(S.calcTimers[s.id]); S.calcTimers[s.id] = setTimeout(run, 250); });
  run();
}

async function runCalc(s, root) {
  const out = $("#calcOut-" + s.id);
  const co = parseMoney($("[data-c=co]", root).value), prior = parseMoney($("[data-c=prior]", root).value);
  if (co === null || prior === null) { out.innerHTML = `<p class="err" style="margin-top:8px">Enter amounts as numbers, for example 3,000,000 or 3m.</p>`; return; }
  try {
    const r = await api("/api/ceiling", { method: "POST", body: { track: s.sizing.track, co_amount: co, prior_gov: prior, first_round: $("[data-c=first]", root).checked } });
    const total = r.gov || 0;
    const cls = (ratio) => ({ "2:1": "t1", "7:3": "t1", "1:1": "t2", "1:2": "t3", "1:3": "t4" }[ratio] || "t2");
    out.innerHTML = `<p class="calcout">The schedule allows up to <b>${esc(money(Math.round(total)))}</b> against ${esc(money(co))} from the co-investor${r.cap_reached ? " (the cap is reached)" : ""}.</p>
      ${r.steps.length ? `<div class="tierbar" role="img" aria-label="How the amount is built up by tier">${r.steps.map((st) => `<div class="${cls(st.ratio)}" style="flex:${Math.max(st.gov, total * 0.02)}" title="${esc(st.ratio)}: ${esc(money(Math.round(st.gov)))} against ${esc(money(Math.round(st.co)))}">${st.gov / total > 0.16 ? esc(money(Math.round(st.gov)).replace(" million", "m")) : ""}</div>`).join("")}</div>
      <div class="legend">${r.steps.map((st) => `<span><i class="${cls(st.ratio)}"></i>${esc(st.ratio)} tier</span>`).join("")}</div>
      <table class="tiers" style="margin-top:8px"><thead><tr><th>Tier (government : co-investor)</th><th>Government side</th><th>Co-investor money used</th></tr></thead><tbody>
        ${r.steps.map((st) => `<tr><td>${esc(st.ratio)}, until the government's total reaches ${esc(money(st.upto))}</td><td>${esc(money(Math.round(st.gov)))}</td><td>${esc(money(Math.round(st.co)))}</td></tr>`).join("")}
      </tbody></table>` : ""}
      ${r.co_unmatched > 1 ? `<p class="note" style="margin-top:6px">${esc(money(Math.round(r.co_unmatched)))} of the co-investor's money is past the cap and is not matched.</p>` : ""}
      <p class="note" style="margin-top:6px">${esc(s.sizing.track_label)} schedule.${srcLink("ssge_eligibility")}</p>`;
  } catch (e) { out.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

/* ---- past runs ---- */

async function showRuns() {
  const runs = await api("/api/runs");
  $("#modalTitle").textContent = "Past runs";
  $("#modalBody").innerHTML = runs.length ? runs.map((r) => `
    <div class="runrow"><div><strong>${esc(r.names.filter(Boolean).join(", ") || "Unnamed batch")}</strong>
      <div class="note">${esc((r.created_at || "").replace("T", " ").slice(0, 16))} | ${esc(r.mode)} | ${esc(r.status)}${r.usd ? " | about US$" + Number(r.usd).toFixed(2) : ""}</div></div>
      <button class="btn" type="button" data-open="${esc(r.id)}" ${r.status === "done" ? "" : "disabled"}>Open</button></div>`).join("")
    : `<p class="hint">No runs yet.</p>`;
  for (const b of $$("[data-open]", $("#modalBody"))) b.addEventListener("click", () => openRun(b.dataset.open));
  $("#modal").hidden = false;
}

async function openRun(runId) {
  $("#modal").hidden = true;
  S.runId = runId;
  S.run = await api("/api/runs/" + encodeURIComponent(runId));
  const ev = await api(`/api/runs/${encodeURIComponent(runId)}/events?after=0`);
  S.events = ev.events;
  S.lastSeq = S.events.length ? S.events[S.events.length - 1].seq : 0;
  $("#progress").hidden = false;
  $("#clock").textContent = "Opened from past runs.";
  renderProgress();
  S.tab = null;
  renderResults();
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}

boot().catch((e) => { document.body.insertAdjacentHTML("afterbegin", `<div class="banner">The desk could not start: ${esc(e.message)}</div>`); });
