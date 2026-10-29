/* CareerCopilot AI – conversational "Add to Existing Resume" workflow.
 * Every number shown here comes from the backend scoring engine; the UI never computes or invents scores.
 * Nothing is applied to the document without an explicit approval in the confirmation dialog. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => (n === null || n === undefined ? "–" : Number(n).toFixed(1));
const signed = (n) => (n > 0 ? "+" : n < 0 ? "−" : "±") + Math.abs(Number(n)).toFixed(1);

const state = {
  sid: null, summary: null, log: [], overrides: {}, sim: {}, editing: {}, answering: {}, busy: {},
  activeTab: "current",
};

/* ---------------- API ---------------- */
async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body ? "POST" : "GET"), headers: {} };
  if (opts.body instanceof FormData) init.body = opts.body;
  else if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  // Long operations (upload + OCR, PDF export) get more time than quick ones.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeout || (opts.body instanceof FormData ? 180000 : 60000));
  init.signal = ctrl.signal;
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    throw new Error(e.name === "AbortError"
      ? "The server took too long to respond. Please try again."
      : navigator.onLine === false ? "You're offline. Check your connection and try again."
      : "Can't reach the CareerCopilot server. Make sure it is running, then try again.");
  } finally {
    clearTimeout(timer);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || data.detail?.[0]?.msg ||
      (res.status >= 500 ? "Something went wrong on the server. Your resume was not changed." : `Request failed (${res.status})`));
    err.status = res.status; err.errorId = data.error_id;
    throw err;
  }
  return data;
}

// Any error a button handler forgot to catch still reaches the user instead of failing silently.
window.addEventListener("unhandledrejection", (e) => { toast(e.reason?.message || "Something went wrong"); });
window.addEventListener("error", (e) => { if (e.message) console.error(e.error || e.message); });
const S = (p) => `/api/sessions/${state.sid}${p}`;

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3200);
}

/* tiny, safe markdown: **bold**, *em*, "- " lists */
function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (const l of lines) {
    const m = l.match(/^- (.*)/);
    if (m) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${m[1]}</li>`; continue; }
    if (inList) { html += "</ul>"; inList = false; }
    if (l.trim()) html += `<p>${l}</p>`;
  }
  if (inList) html += "</ul>";
  return html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/\*(.+?)\*/g, "<em>$1</em>");
}

/* word-level diff for original → proposed */
function wordDiff(a, b) {
  const A = (a || "").split(/(\s+)/), B = (b || "").split(/(\s+)/);
  const n = A.length, m = B.length;
  if (n * m > 250000) return esc(b);
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  let i = 0, j = 0, out = "";
  while (i < n && j < m) {
    if (A[i] === B[j]) { out += esc(A[i]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { if (A[i].trim()) out += `<del>${esc(A[i])}</del>`; else out += esc(A[i]); i++; }
    else { out += B[j].trim() ? `<ins>${esc(B[j])}</ins>` : esc(B[j]); j++; }
  }
  while (i < n) { if (A[i].trim()) out += `<del>${esc(A[i])}</del>`; i++; }
  while (j < m) { out += B[j].trim() ? `<ins>${esc(B[j])}</ins>` : esc(B[j]); j++; }
  return out;
}

/* ---------------- landing ---------------- */
$("#file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  $("#drop").classList.toggle("has-file", !!f);
  $("#drop-label").innerHTML = f ? `<strong>${esc(f.name)}</strong><br><small>${(f.size / 1024).toFixed(0)} KB · click to change</small>`
    : "<strong>Choose your resume</strong><br><small>DOCX or PDF, up to 15&nbsp;MB</small>";
});

$("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = $("#file").files[0];
  if (!f) { $("#upload-error").textContent = "Choose a DOCX or PDF resume."; return; }
  const fd = new FormData(); fd.append("file", f); fd.append("jd_text", $("#jd").value);
  fd.append("company", $("#company").value); fd.append("role", $("#role").value);
  fd.append("url", $("#job-url").value); fd.append("track", $("#track").checked ? "true" : "false");
  await startSession(() => api("/api/sessions", { body: fd }));
});

$("#sample-btn").addEventListener("click", async () => {
  await startSession(() => api("/api/sample", { body: {} }));
});

async function startSession(fn) {
  const btn = $("#analyze-btn");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Analyzing…';
  $("#upload-error").textContent = "";
  try {
    const summary = await fn();
    state.sid = summary.session_id;
    state.log = [];
    state.pendingSummary = summary;
    location.hash = `#s=${state.sid}`;
  } catch (err) {
    $("#upload-error").textContent = err.message;
  } finally {
    btn.disabled = false; btn.textContent = "Analyze resume";
  }
}

$("#new-session").addEventListener("click", () => { location.hash = "#/start"; });

/* ---------------- workspace ---------------- */
function enterWorkspace(summary) {
  showView("workspace");
  $("#topbar-right").hidden = false;
  setSummary(summary);
  loadDocument();
}

function setSummary(summary) {
  state.summary = summary;
  state.sim = {};
  $("#file-chip").textContent = `${summary.filename} · v${summary.head}`;
  $("#score-pill").textContent = `${fmt(summary.score.overall)} / 100`;
  $("#download-btn").href = S(`/download?version=${summary.head}`);
  $("#letter-btn").href = `#/letters?s=${summary.session_id}`;
  $("#editor-btn").href = `#/editor?s=${summary.session_id}`;
  $("#file-chip").textContent = `${summary.company ? summary.company + " · " : ""}${summary.filename} · v${summary.head}`;
  renderChat();
  if (state.activeTab === "history") renderHistory();
}

async function refresh() {
  setSummary(await api(S("")));
}

function scoreCard(sc, doc) {
  const comps = Object.values(sc.components).filter((c) => c.applicable);
  const reqs = sc.requirements.filter((r) => ["skill", "certification", "years", "education"].includes(r.type));
  const docNote = doc.scanned
    ? `<p class="blocked">This PDF is a scan. Text was read with OCR (${fmt(doc.ocr_confidence)}% confidence); edits will be reconstructed on the page image and the original typeface cannot be reproduced exactly.</p>` : "";
  return `
    <div class="score-big"><strong>${fmt(sc.overall)}</strong><span class="muted">/ 100 current calculated score</span></div>
    ${docNote}
    <div class="bars">${comps.map((c) => `
      <div class="bar-row"><span>${esc(c.label)} <small class="muted">(${Math.round(c.weight * 100)}%)</small></span>
      <span class="bar" aria-hidden="true"><i style="width:${(c.score * 100).toFixed(0)}%"></i></span>
      <span class="num">${(c.score * 100).toFixed(0)}</span></div>`).join("")}
    </div>
    <div class="req-list">${reqs.map((r) => `<span class="chip ${r.status}" title="${esc(r.jd_text)}">${r.status === "met" ? "✓" : r.status === "partial" ? "◐" : "✕"} ${esc(r.name)}</span>`).join("")}</div>
    <p class="muted" style="margin-top:8px;font-size:12px">Methodology ${esc(sc.methodology)}. Weights are initial project assumptions, not universal ATS rules. A compatibility score is not a probability of being interviewed or hired.</p>`;
}

function renderChat() {
  const s = state.summary;
  const recs = s.recommendations;
  const isSkipped = (r) => r.kind === "gap" || r.status === "rejected" || r.status === "unsupported";
  const isAsk = (r) => !isSkipped(r) && !!r.question && (r.status === "needs_clarification" || !r.options.length);
  const ready = recs.filter((r) => !isSkipped(r) && !isAsk(r) && r.options.length);
  const asked = recs.filter(isAsk);
  const ask = asked.filter((r) => !r.requirement.example_only || state.answering[r.id])
    .sort((a, b) => (a.requirement.importance !== "required") - (b.requirement.importance !== "required"));
  const optional = asked.filter((r) => !ask.includes(r));
  const skipped = recs.filter(isSkipped);
  const intro = `
    <div class="msg bot"><div class="who">CareerCopilot</div>
      <p>I analyzed <strong>${esc(s.filename)}</strong>${s.job.title ? ` against <strong>${esc(s.job.title)}</strong>` : ""}.
      <strong>${ready.length}</strong> change${ready.length === 1 ? " is" : "s are"} ready to apply from what your resume already shows.
      ${ask.length ? `For <strong>${ask.length}</strong> other job keyword${ask.length === 1 ? "" : "s"} I need a quick yes or no – skip anything you haven't actually done; nothing is ever added without your experience behind it.` : ""}
      Nothing changes until you approve it.</p>
    </div>
    <div class="msg bot"><div class="who">Compatibility score</div>${scoreCard(s.score, s.document)}</div>`;
  const log = state.log.map((m) => m.html).join("");
  const group = (title, note, items, render = recCard) => items.length
    ? `<div class="msg bot rec-group"><div class="who">${title} · ${items.length}</div><p>${note}</p></div>` + items.map(render).join("") : "";
  const cards = recs.length
    ? group("Ready to apply", "Built only from what is already in your resume. Sorted by predicted score impact.", ready)
      + group("Do you have these?", "Keywords from the job that your resume doesn't show. Tap <strong>Yes</strong> only if you've really used it – we'll write the line from your words. Otherwise tap <strong>Skip</strong>.", ask,
              (r) => (state.answering[r.id] ? recCard(r) : askCard(r)))
      + (optional.length ? `<details class="skipped-group"><summary>Optional – the job accepts any one of these (${optional.length})</summary>
          <p class="muted" style="font-size:13px;margin:0">Listed as alternatives (e.g. “FastAPI, Flask, or Django”). Having one of them is enough, so leave the rest.</p>${optional.map(askCard).join("")}</details>` : "")
      + (skipped.length ? `<details class="skipped-group"><summary>Skipped – not added to your resume (${skipped.length})</summary>${skipped.map(recCard).join("")}</details>` : "")
    : `<div class="msg bot"><p>No missing or partially matched requirements remain. Download your resume above.</p></div>`;
  const chat = $("#chat");
  const atBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 40;
  const scroll = chat.scrollTop;
  chat.innerHTML = intro + log + cards;
  chat.scrollTop = atBottom && state.scrollToEnd ? chat.scrollHeight : scroll;
  if (state.scrollToLog) {
    const msgs = chat.querySelectorAll(".log-msg");
    msgs[msgs.length - 1]?.scrollIntoView({ block: "start", behavior: "smooth" });
  }
  state.scrollToEnd = false; state.scrollToLog = false;
  bindCards();
}

function pushLog(html, cls = "bot") {
  state.log.push({ html: `<div class="msg ${cls} log-msg">${html}</div>` });
  state.scrollToLog = true;
}

/* ---------------- recommendation cards ---------------- */
function currentOption(rec) {
  if (state.sim[rec.id]) return state.sim[rec.id];
  const sel = state.overrides[rec.id]?.selected ?? rec.selected ?? 0;
  return rec.options[sel];
}

function badge(f) {
  return `<span class="badge ${f.status}">${f.status === "unsupported" ? "✕" : f.status === "clarification" ? "?" : "✓"} ${esc(f.label)}</span>`;
}

function blockOptions(selectedId) {
  return state.summary.document.blocks
    .filter((b) => !["heading", "contact"].includes(b.kind) && /^p\d+$|^pg\d+-b\d+$/.test(b.id))
    .map((b) => `<option value="${esc(b.id)}" ${b.id === selectedId ? "selected" : ""}>${esc(b.section)} · ${esc(b.text.slice(0, 70))}</option>`).join("");
}

function recCard(rec) {
  const req = rec.requirement;
  const head = `
    <div class="rec-head">
      <span class="kw">${esc(rec.kind === "responsibility" ? "Responsibility" : rec.keyword)}</span>
      <span class="chip neutral">${esc(req.importance)}</span>
      <span class="chip ${rec.match_status === "missing" ? "missing" : "partial"}">${rec.kind === "gap" ? "gap" : esc(rec.match_status)}</span>
    </div>`;
  const reqBlock = `
    <div><div class="label">Job-description requirement</div><div class="quote">${esc(req.jd_text)}</div></div>
    ${rec.why ? `<div><div class="label">Why it matters</div>${esc(rec.why)}</div>` : ""}`;

  if (rec.kind === "gap") {
    return `<article class="rec" data-rec="${rec.id}">${head}<div class="rec-body">${reqBlock}
      <div class="learn">${esc(rec.gap)}</div>
      <div class="impact"><div><small>Estimated impact</small><b>0.0</b><small>no factual edit can close this gap</small></div></div></div></article>`;
  }
  if (rec.status === "rejected") {
    return `<article class="rec rejected" data-rec="${rec.id}">${head}<div class="rec-body">
      <p class="muted">Rejected. This recommendation will never be applied unless you restore it.</p>
      <div class="actions"><button class="btn small" data-act="unreject">Restore recommendation</button></div></div></article>`;
  }
  if (rec.status === "unsupported") {
    return `<article class="rec" data-rec="${rec.id}">${head}<div class="rec-body">${reqBlock}
      <span>${badge({ status: "unsupported", label: "Unsupported or contradicted" })}</span>
      <div class="learn"><strong>Not added.</strong> ${esc(rec.gap)}${rec.learning_project ? `<br><strong>Suggested learning project:</strong> ${esc(rec.learning_project)}` : ""}</div>
      <div class="impact"><div><small>Estimated impact</small><b>0.0</b><small>unsupported claims are never added</small></div></div>
      <div class="actions"><button class="btn small" data-act="reopen">I do have this experience</button></div></div></article>`;
  }

  let body = reqBlock;
  const q = rec.question;
  if (q && (rec.status === "needs_clarification" || !rec.options.length || state.answering[rec.id])) {
    const ans = state.answering[rec.id];
    body += `<div class="question"><div><span class="badge clarification">? Requires clarification</span></div>
      <div>${esc(q.text)}</div>
      ${ans ? `
        <label class="field"><span>${esc(q.detail_prompt)}</span>
        <textarea data-field="answer-text" rows="3" placeholder="e.g. Containerized our Flask services with Docker and Docker Compose for local development and CI.">${esc(ans.text || "")}</textarea></label>
        <p class="muted" style="font-size:13px;margin:0">We'll write it as a proper resume line and place it under the role or project your answer describes.</p>
        <div class="actions"><button class="btn primary small" data-act="answer-submit">Write it for me</button><button class="btn small" data-act="answer-cancel">Cancel</button></div>`
      : `<div class="actions"><button class="btn primary small" data-act="answer-yes">Yes, I have</button><button class="btn small" data-act="answer-no">No, I haven't</button>
         <button class="btn small danger" data-act="reject">Reject</button></div>`}
      </div>`;
  }

  const opt = rec.options.length ? currentOption(rec) : null;
  if (opt) {
    const ov = state.overrides[rec.id] || {};
    const sel = ov.selected ?? rec.selected ?? 0;
    const optLabel = (o) => `${o.mode === "replace" ? (o.action === "add" ? "Add to line" : "Replace") : "Add new line after"} · ${o.target_label}`;
    const editing = state.editing[rec.id];
    const failing = opt.grammar.checks.filter((c) => c.status !== "pass");
    const sc = opt.score;
    const comps = Object.values(sc.components).filter((c) => Math.abs(c.points) >= 0.05)
      .map((c) => `${esc(c.label)} ${signed(c.points)}`).join(" · ");
    body += `
      <div class="location"><div class="label">Best location in your resume</div>
        <select data-field="option">${rec.options.map((o, i) => `<option value="${i}" ${i === sel ? "selected" : ""}>${esc(optLabel(o))}</option>`).join("")}
        <option value="custom" ${ov.custom ? "selected" : ""}>Choose another location…</option></select>
        ${ov.custom ? `<div class="actions" style="margin-top:6px">
            <select data-field="mode" style="width:auto"><option value="insert_after" ${opt.mode === "insert_after" ? "selected" : ""}>Insert as new line after</option><option value="replace" ${opt.mode === "replace" ? "selected" : ""}>Replace</option></select>
            <select data-field="block" style="flex:1;min-width:0">${blockOptions(opt.block_id)}</select></div>` : ""}
      </div>
      ${opt.mode === "replace" ? `<div><div class="label">Original text</div><div class="textbox">${esc(opt.original_text)}</div></div>`
        : `<div class="muted" style="font-size:13px">Inserted after: “${esc((state.summary.document.blocks.find((b) => b.id === opt.block_id) || {}).text || "").slice(0, 90)}…”, inheriting that line's formatting.</div>`}
      <div><div class="label">Proposed text ${opt.edited_by_user ? "<span class='chip neutral'>edited by you</span>" : ""}</div>
        ${editing ? `<div class="edit-area"><textarea data-field="edit-text" rows="3">${esc(editing.text)}</textarea>
            <div class="actions" style="margin-top:6px"><button class="btn primary small" data-act="edit-save">Validate &amp; save</button><button class="btn small" data-act="edit-cancel">Cancel</button></div></div>`
        : `<div class="textbox proposed diff">${opt.mode === "replace" ? wordDiff(opt.original_text, opt.proposed_text) : esc(opt.proposed_text)}</div>`}
      </div>
      <div class="checks">
        ${failing.length ? failing.map((c) => `<span class="check ${c.status}">${c.status === "fail" ? "✕" : "!"} ${esc(c.message)}</span>`).join("")
          : `<span class="check pass">✓ Passed all ${opt.grammar.checks.length} grammar and writing checks</span>`}
        ${opt.grammar.fixed_text ? `<div><button class="btn small" data-act="apply-fixes">Use suggested fixes</button></div>` : ""}
      </div>
      <div>${badge(opt.factual)} ${opt.writer === "ai" ? `<span class="badge stylistic">✦ Written by ${esc(opt.writer_label || "AI")} · checked</span>` : ""}</div>
      ${opt.writer_note ? `<div class="comp-deltas">${esc(opt.writer_note)}</div>` : ""}
      <details class="evidence" ${state.openEvidence === rec.id ? "open" : ""} data-evidence="${rec.id}"><summary>View evidence</summary>
        <p style="font-size:13px;margin:6px 0 0">${esc(opt.rationale || "")}</p>
        <ul>${opt.factual.claims.map((c) => `<li><strong>${esc(c.claim)}</strong> (${esc(c.status)}): ${c.evidence.map(esc).join(" ")}</li>`).join("") || "<li>No new factual claims; wording change only.</li>"}</ul>
      </details>
      <div class="impact">
        <div><small>Current score</small><b>${fmt(sc.before)}</b><small>calculated</small></div>
        <div><small>Predicted after change</small><b>${fmt(sc.after)}</b><small>simulated, not applied</small></div>
        <div class="delta ${sc.points > 0 ? "pos" : "zero"}"><small>Estimated change</small><b>${signed(sc.points)} pts</b>
          <small>${sc.relative_percent !== null ? `relative: ${signed(sc.relative_percent)}% of current score` : ""}</small></div>
      </div>
      ${rec.kind === "wording" && sc.points > 0 ? `<div class="comp-deltas">Raises “Formatting and parsing quality” (language quality) – clean, correctly spelled lines are read more reliably by ATS parsers and recruiters.</div>`
        : sc.points === 0 && rec.kind === "wording" ? `<div class="comp-deltas">Readability fix with no measurable score effect.</div>`
        : sc.points === 0 ? `<div class="comp-deltas">0 points: this edit adds no new requirement coverage or stronger evidence, so the score would not change.</div>` : comps ? `<div class="comp-deltas">${comps}</div>` : ""}
      ${opt.can_apply ? "" : `<div class="blocked">${esc(opt.blocked_reason || "")}</div>`}
      <div class="actions">
        ${opt.mode === "insert_after" || opt.action === "add"
          ? `<button class="btn primary" data-act="do-add">Add to Existing Resume</button>`
          : `<button class="btn primary" data-act="do-replace">${rec.kind === "wording" ? "Apply fix" : "Replace Existing Text"}</button>`}
        <button class="btn" data-act="preview">Preview Change</button>
        <button class="btn" data-act="edit">Edit Suggestion</button>
        <button class="btn" data-act="claude" ${state.summary.llm?.configured ? "" : `disabled title="Add GROQ_API_KEY to .env and restart the server"`}>✦ Rewrite with ${esc(state.summary.llm?.provider || "AI")}</button>
        <button class="btn danger" data-act="reject">Reject</button>
      </div>`;
  }
  return `<article class="rec" data-rec="${rec.id}">${head}<div class="rec-body">${body}</div></article>`;
}

function askCard(rec) {
  const req = rec.requirement;
  const alts = req.example_only;
  return `<article class="rec compact" data-rec="${rec.id}">
    <div class="rec-head"><span class="kw">${esc(rec.keyword)}</span>
      ${alts ? `<span class="chip neutral" title="The job lists this as one option among several">optional · any one option</span>` : `<span class="chip neutral">${esc(req.importance)}</span>`}</div>
    <div class="rec-body">
      <div>${esc(rec.question.text)}</div>
      ${alts ? `<div class="muted" style="font-size:13px">${esc(rec.why)}</div>` : ""}
      <details class="evidence"><summary>Where the job asks for it</summary><div class="quote">${esc(req.jd_text)}</div></details>
      <div class="actions"><button class="btn primary small" data-act="answer-yes">Yes, I've used it</button>
        <button class="btn small" data-act="answer-no">Skip – I don't have it</button></div>
    </div></article>`;
}

function bindCards() {
  document.querySelectorAll(".rec").forEach((card) => {
    const id = card.dataset.rec;
    const rec = state.summary.recommendations.find((r) => r.id === id);
    card.querySelectorAll("[data-act]").forEach((btn) => btn.addEventListener("click", () => onAction(rec, btn.dataset.act, card, btn)));
    const optSel = card.querySelector('[data-field="option"]');
    if (optSel) optSel.addEventListener("change", () => {
      delete state.sim[id];
      if (optSel.value === "custom") { state.overrides[id] = { ...(state.overrides[id] || {}), custom: true }; renderChat(); return; }
      state.overrides[id] = { selected: Number(optSel.value) };
      renderChat();
    });
    const modeSel = card.querySelector('[data-field="mode"]'), blockSel = card.querySelector('[data-field="block"]');
    const custom = () => simulate(rec, { ...(state.overrides[id] || {}), mode: modeSel.value, block_id: blockSel.value, custom: true });
    if (modeSel) modeSel.addEventListener("change", custom);
    if (blockSel) blockSel.addEventListener("change", custom);
    const ev = card.querySelector("details.evidence");
    if (ev) ev.addEventListener("toggle", () => { state.openEvidence = ev.open ? id : null; });
  });
}

function overrideFor(rec) {
  const ov = { ...(state.overrides[rec.id] || {}) };
  const opt = currentOption(rec);
  const body = { selected: ov.selected ?? rec.selected ?? 0 };
  if (ov.mode) body.mode = ov.mode;
  if (ov.block_id) body.block_id = ov.block_id;
  if (opt && opt.edited_by_user) body.text = opt.proposed_text;
  if (ov.text) body.text = ov.text;
  return body;
}

async function simulate(rec, ov) {
  state.overrides[rec.id] = ov;
  try {
    const opt = await api(S(`/recommendations/${rec.id}/simulate`), { body: overrideFor(rec) });
    state.sim[rec.id] = opt;
    renderChat();
  } catch (e) { toast(e.message); }
}

async function onAction(rec, act, card, btn) {
  const id = rec.id;
  try {
    switch (act) {
      case "answer-yes": state.answering[id] = { text: "" }; renderChat(); break;
      case "answer-cancel": delete state.answering[id]; renderChat(); break;
      case "reopen": state.answering[id] = { text: "" }; rec.status = "needs_clarification"; renderChat(); break;
      case "answer-no": {
        pushLog(`I haven't used ${esc(rec.keyword)}.`, "user");
        await api(S(`/recommendations/${id}/answer`), { body: { yes: false } });
        pushLog(`<div class="who">CareerCopilot</div><p>Skipped. Nothing about ${esc(rec.keyword)} will be added – it's moved to “Skipped” at the bottom.</p>`);
        delete state.answering[id]; await refresh(); break;
      }
      case "answer-submit": {
        const text = card.querySelector('[data-field="answer-text"]').value.trim();
        const anchor = card.querySelector('[data-field="anchor"]')?.value || null;
        btn.disabled = true;
        if (text) pushLog(esc(text), "user");
        let after;
        try { after = await api(S(`/recommendations/${id}/answer`), { body: { yes: true, text, anchor_id: anchor } }); }
        catch (e) {   // keep the card open with the text so it can be clarified
          state.answering[id] = { text }; btn.disabled = false;
          pushLog(`<div class="who">CareerCopilot</div><p>${esc(e.message)}</p>`); renderChat(); break;
        }
        const ready = after.options?.length && after.status !== "unsupported";
        pushLog(`<div class="who">CareerCopilot</div><p>${ready
          ? `Thanks. I wrote a ${esc(rec.keyword)} line from your description and checked it. Review the card${after.options[0].score.points > 0 ? ` (${signed(after.options[0].score.points)} pts)` : ""} and approve it to add it to your resume.`
          : after.answer_error ? esc(after.answer_error)
          : `Thanks, ${esc(rec.keyword)} is confirmed. It is already counted in your score, so no new line is needed.`}</p>`);
        delete state.answering[id]; await refresh();
        if (ready) document.querySelector(`[data-rec="${id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
        break;
      }
      case "reject":
        await api(S(`/recommendations/${id}/reject`), { body: {} });
        toast(`Rejected: ${rec.keyword}. It will not be applied.`); await refresh(); break;
      case "unreject":
        await api(S(`/recommendations/${id}/unreject`), { body: {} }); await refresh(); break;
      case "edit": state.editing[id] = { text: currentOption(rec).proposed_text }; renderChat(); break;
      case "edit-cancel": delete state.editing[id]; renderChat(); break;
      case "edit-save":
      case "apply-fixes": {
        const text = act === "apply-fixes" ? currentOption(rec).grammar.fixed_text : card.querySelector('[data-field="edit-text"]').value;
        const body = { ...overrideFor(rec), text };
        const opt = await api(S(`/recommendations/${id}/edit`), { body });
        delete state.editing[id];
        state.overrides[id] = { ...(state.overrides[id] || {}), text: opt.proposed_text };
        state.sim[id] = opt;
        renderChat();
        toast(opt.can_apply ? "Saved. Validation passed." : "Saved. Some checks still need attention.");
        break;
      }
      case "preview": await openPreviewTab(rec); break;
      case "claude": {
        btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> Asking ${esc(state.summary.llm?.provider || "AI")}…`;
        const opt = await api(S(`/recommendations/${id}/claude`), { body: overrideFor(rec) });
        state.sim[id] = opt;
        if (opt.writer === "claude") state.overrides[id] = { ...(state.overrides[id] || {}), text: opt.proposed_text };
        renderChat();
        toast(opt.writer === "ai" ? `${opt.writer_label || "AI"}'s wording passed all checks. Review it before approving.` : (opt.writer_note || "Kept the built-in draft."));
        break;
      }
      case "do-add": await requestApproval(rec, "add"); break;
      case "do-replace": await requestApproval(rec, "replace"); break;
    }
  } catch (e) { toast(e.message); btn && (btn.disabled = false); }
}

/* Switch an option's mode when the user picks the other action button. */
async function ensureMode(rec, want) {
  const opt = currentOption(rec);
  const isAdd = opt.mode === "insert_after" || opt.action === "add";
  if ((want === "add") === isAdd) return opt;
  const ov = { ...(state.overrides[rec.id] || {}), mode: want === "add" ? "insert_after" : "replace", block_id: opt.block_id, custom: true };
  if (want === "replace" && opt.mode === "insert_after") {
    toast("Choose which existing line to replace, then press Replace Existing Text again.");
  }
  await simulate(rec, ov);
  return state.sim[rec.id];
}

async function openPreviewTab(rec) {
  const panel = $("#panel-preview");
  switchTab("preview");
  panel.innerHTML = `<p class="muted pad"><span class="spinner"></span> Rendering the proposed result (not saved)…</p>`;
  const p = await api(S(`/recommendations/${rec.id}/preview`), { body: overrideFor(rec) });
  panel.innerHTML = `<div class="report"><strong>Preview only. Your document has not been changed.</strong><br>
      Predicted score ${fmt(p.option.score.before)} → ${fmt(p.option.score.after)} (${signed(p.option.score.points)} pts)
      <div class="actions" style="margin-top:8px"><button class="btn primary small" id="pv-approve">Approve this change…</button></div></div>
    ${reportHtml(p.preservation)}${renderDoc(p)}`;
  $("#pv-approve").addEventListener("click", () => requestApproval(rec, p.option.mode === "replace" && p.option.action !== "add" ? "replace" : "add"));
}

function reportHtml(r) {
  if (!r) return "";
  const ok = r.exact;
  return `<div class="report"><strong>${ok ? "✓ Formatting verified as preserved" : "Formatting preservation report"}</strong>
    <span class="muted">· ${esc(r.method)}</span>
    ${ok ? "" : `<p style="margin:6px 0 0">Some properties could not be preserved exactly. They are listed below and nothing is claimed beyond what was verified.</p>`}
    <table>${r.properties.map((p) => `<tr><td class="st ${esc(p.status)}">${esc(p.status)}</td><td>${esc(p.property)}<br><span class="muted">${esc(p.detail)}</span></td></tr>`).join("")}</table></div>`;
}

function renderDoc(d) {
  if (d.format === "pdf") return `<p class="fidelity">${esc(d.fidelity)}</p>` + d.pages.map((src, i) => `<img class="page-img" alt="Page ${i + 1}" src="${src}">`).join("");
  requestAnimationFrame(fitPages);
  return `<p class="fidelity">${esc(d.fidelity)}</p><div class="page-fit">${d.html}</div>`;
}

/* Scale the true-size DOCX page down to the available width (never up). */
function fitPages() {
  document.querySelectorAll(".page-fit").forEach((box) => {
    const page = box.querySelector(".cc-docx-page");
    if (!page) return;
    page.style.transform = "none";
    const avail = box.parentElement.clientWidth - 4;
    const scale = Math.min(1, avail / page.offsetWidth);
    page.style.transform = `scale(${scale})`;
    box.style.width = `${page.offsetWidth * scale}px`;
    box.style.height = `${page.offsetHeight * scale}px`;
  });
}
window.addEventListener("resize", fitPages);

async function requestApproval(rec, want) {
  const opt = await ensureMode(rec, want);
  if (!opt) return;
  const dlg = $("#approve-dialog");
  $("#approve-title").textContent = want === "add" ? "Add to existing resume?" : "Replace existing text?";
  $("#approve-content").innerHTML = `<p class="muted"><span class="spinner"></span> Preparing a preview of the exact change…</p>`;
  $("#approve-btn").disabled = true;
  dlg.showModal();
  let p;
  try { p = await api(S(`/recommendations/${rec.id}/preview`), { body: overrideFor(rec) }); }
  catch (e) { $("#approve-content").innerHTML = `<p class="blocked">${esc(e.message)}</p>`; return; }
  const o = p.option;
  $("#approve-content").innerHTML = `
    <div class="approve-grid">
      <div>
        <div class="label">Location</div><p style="margin:0 0 8px">${esc(o.target_label)}</p>
        ${o.mode === "replace" ? `<div class="label">Original</div><div class="textbox">${esc(o.original_text)}</div>` : ""}
        <div class="label" style="margin-top:8px">${o.mode === "replace" ? "Replaced with" : "New line"}</div>
        <div class="textbox proposed diff">${o.mode === "replace" ? wordDiff(o.original_text, o.proposed_text) : esc(o.proposed_text)}</div>
        <p>${badge(o.factual)}</p>
        <div class="checks">${o.grammar.passed ? `<span class="check pass">✓ Grammar and writing checks passed</span>` : `<span class="check fail">✕ Writing checks failed</span>`}</div>
        <div class="impact" style="margin-top:10px">
          <div><small>Current</small><b>${fmt(o.score.before)}</b></div>
          <div><small>Predicted</small><b>${fmt(o.score.after)}</b></div>
          <div class="delta ${o.score.points > 0 ? "pos" : "zero"}"><small>Change</small><b>${signed(o.score.points)} pts</b></div>
        </div>
        ${o.can_apply ? "" : `<p class="blocked">${esc(o.blocked_reason)}</p>`}
        <div style="margin-top:10px">${reportHtml(p.preservation)}</div>
        <p class="muted" style="font-size:12px">Approving saves a new version. Your original upload stays untouched, and you can undo at any time.</p>
      </div>
      <div class="approve-preview">${renderDoc(p)}</div>
    </div>`;
  $("#approve-btn").disabled = !o.can_apply;
  dlg.onclose = async () => {
    if (dlg.returnValue !== "approve") return;
    await applyApproved(rec);
  };
}

async function applyApproved(rec) {
  toast("Applying the approved change…");
  try {
    const res = await api(S(`/recommendations/${rec.id}/apply`), {
      body: { override: overrideFor(rec), approved: true, version: state.summary.head },
    });
    const c = res.comparison;
    pushLog(`<div class="who">Change applied · version ${res.version}</div>
      <p><strong>${esc(rec.keyword)}</strong> was added to your existing resume and saved as a new version.</p>
      <div class="cmp">
        <div>Before<b>${fmt(c.current_before)}</b></div>
        <div>Predicted<b>${fmt(c.predicted_after)}</b>${signed(c.predicted_points)}</div>
        <div>Actual (recalculated)<b>${fmt(c.actual_after)}</b>${signed(c.actual_points)}</div>
        <div>Match<b>${c.match ? "✓" : "≠"}</b>${c.match ? "prediction confirmed" : "see note"}</div>
      </div>
      ${c.explanation ? `<p class="blocked">${esc(c.explanation)}</p>` : ""}
      <p style="font-size:13px">The actual score was recalculated independently by re-reading the saved file.
      Relative change: ${c.relative_percent !== null ? signed(c.relative_percent) + "%" : "–"} of the previous score (not a change in interview odds).</p>
      ${c.requirement_changes.length ? `<p style="font-size:13px">${c.requirement_changes.map((r) => `${esc(r.name)}: ${esc(r.from)} → <strong>${esc(r.to)}</strong>`).join(" · ")}</p>` : ""}
      <p style="font-size:13px">${res.preservation.exact ? "✓ Saved in your resume's own fonts and layout." : "Saved. Some formatting was approximated; see the report in Versions &amp; changes."}</p>
      <div class="actions"><button class="btn small" onclick="undoLast()">Undo</button>
        <a class="btn primary small" href="${S(`/download?version=${res.version}`)}">Download updated resume</a>
        <button class="btn small" onclick="switchTab('history')">View report</button></div>`);
    delete state.overrides[rec.id]; delete state.sim[rec.id];
    state.scrollToEnd = true;
    await refresh();
    switchTab("current", { load: false });
    $("#panel-current").innerHTML = renderDoc(res.preview);
    $("#panel-current .cc-hl, #panel-current img")?.scrollIntoView({ block: "center" });
  } catch (e) { toast(e.message); }
}

window.undoLast = async function () {
  try {
    const r = await api(S("/undo"), { body: {} });
    pushLog(`<div class="who">CareerCopilot</div><p>Undone. Version ${r.head} is active again. The undone version is kept in history and can be restored.</p>`);
    await refresh(); loadDocument();
  } catch (e) { toast(e.message); }
};

window.restoreVersion = async function (v) {
  try {
    await api(S("/restore"), { body: { version: v } });
    pushLog(`<div class="who">CareerCopilot</div><p>Restored version ${v}.</p>`);
    await refresh(); loadDocument(); renderHistory();
  } catch (e) { toast(e.message); }
};

/* ---------------- document column ---------------- */
function switchTab(tab, { load = true } = {}) {
  state.activeTab = tab;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  ["current", "preview", "history"].forEach((t) => ($(`#panel-${t}`).hidden = t !== tab));
  if (tab === "history") renderHistory();
  if (tab === "current" && load) loadDocument();
}
window.switchTab = switchTab;
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

async function loadDocument() {
  const panel = $("#panel-current");
  if (!panel.innerHTML) panel.innerHTML = `<p class="muted pad"><span class="spinner"></span> Rendering…</p>`;
  try { panel.innerHTML = renderDoc(await api(S(`/document?version=${state.summary.head}`))); }
  catch (e) { panel.innerHTML = `<p class="blocked">${esc(e.message)}</p>`; }
}

function renderHistory() {
  const s = state.summary;
  const changes = Object.fromEntries(s.applied.map((a) => [a.version, a.change]));
  const all = [...s.versions].reverse();
  $("#panel-history").innerHTML = `
    <div class="report">Your original upload (version 0) is stored read-only and never modified. Every approved change creates a new version.
      Undo returns to the previous version; any version can be restored or downloaded.</div>
    ${all.map((v) => {
      const ch = changes[v.id] || (v.id === s.head ? null : null);
      const full = ch || null;
      return `<div class="version ${v.id === s.head ? "head" : ""}">
        <div class="row"><strong>v${v.id}</strong><span class="grow">${esc(v.label)}${v.id === s.head ? " <span class='chip met'>active</span>" : ""}</span>
          ${v.id === s.head && v.parent !== null ? `<button class="btn small" onclick="undoLast()">Undo</button>` : ""}
          ${v.id !== s.head ? `<button class="btn small" onclick="restoreVersion(${v.id})">Restore</button>` : ""}
          <a class="btn small" href="${S(`/download?version=${v.id}`)}">Download</a></div>
        ${full ? `<div class="cmp"><div>Before<b>${fmt(full.comparison?.current_before)}</b></div><div>Predicted<b>${fmt(full.predicted.after)}</b></div>
          <div>Actual<b>${fmt(full.actual?.after)}</b></div><div>Status<b>${full.comparison?.match ? "✓" : "≠"}</b></div></div>
          <div style="font-size:13px">${full.original_text ? `<del>${esc(full.original_text)}</del><br>` : ""}<ins>${esc(full.new_text)}</ins>
          <br><span class="muted">${esc(full.target_label)} · ${esc(full.factual_status)}</span></div>
          <details style="margin-top:6px"><summary>Formatting report</summary>${reportHtml(full.preservation)}</details>` : ""}
      </div>`;
    }).join("")}`;
}

/* ---------------- chat ---------------- */
$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("#chat-text").value.trim();
  if (!text) return;
  $("#chat-text").value = "";
  pushLog(esc(text), "user");
  state.scrollToEnd = true; renderChat();
  try {
    const r = await api(S("/chat"), { body: { message: text } });
    pushLog(`<div class="who">CareerCopilot</div>${md(r.reply)}`);
    if (r.action === "show_history") switchTab("history");
  } catch (err) { pushLog(`<p>${esc(err.message)}</p>`); }
  state.scrollToEnd = true; renderChat();
});

/* Workspace entry point used by the router in pages.js */
window.openSession = async function (sid) {
  try { localStorage.setItem("cc_last_sid", sid); } catch { /* storage unavailable */ }
  if (state.sid === sid && state.summary && !$("#workspace").hidden) return;
  state.sid = sid;
  try {
    const summary = state.pendingSummary && state.pendingSummary.session_id === sid ? state.pendingSummary : await api(S(""));
    state.pendingSummary = null;
    enterWorkspace(summary);
  } catch (e) { toast(e.message); location.hash = "#/start"; }
};
