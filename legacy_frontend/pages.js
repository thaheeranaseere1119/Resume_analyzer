/* CareerCopilot AI – router, cover letters and application tracker. Depends on helpers from app.js. */
"use strict";

const VIEWS = ["home", "landing", "workspace", "letters", "tracker", "editor", "ats", "jobs"];
const STATUS_LABELS = { saved: "Saved", applied: "Applied", interview: "Interview", offer: "Offer", rejected: "Rejected" };
const page = { meta: null, sessions: [], jobs: [], letterSid: null, letters: [], letterId: null, editJob: null };

function showView(name) {
  VIEWS.forEach((v) => ($(`#${v}`).hidden = v !== name));
  const nav = ["letters", "tracker", "editor", "ats", "jobs"].includes(name) ? name : name === "home" ? "" : "tailor";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
  if (name !== "workspace") $("#topbar-right").hidden = true;
  window.scrollTo(0, 0);
}
window.showView = showView;

const fmtDate = (t) => (t ? new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "");
const sessionLabel = (s) => `${s.company || "Untitled company"} · ${s.role || s.filename}`;

async function loadMeta() {
  if (!page.meta) page.meta = await api("/api/meta");
  return page.meta;
}

/* ---------------- router ---------------- */
async function route() {
  const h = location.hash;
  const m = h.match(/^#s=([\w-]+)/);
  try {
    if (m) return window.openSession(m[1]);
    if (h.startsWith("#/letters")) {
      showView("letters");
      return openLetters(new URLSearchParams(h.split("?")[1] || "").get("s"));
    }
    if (h.startsWith("#/tracker")) { showView("tracker"); return loadBoard(); }
    for (const v of ["editor", "ats", "jobs"]) {
      if (h.startsWith(`#/${v}`)) {
        showView(v);
        const sid = await activeSid(new URLSearchParams(h.split("?")[1] || "").get("s"));
        if (!sid) { $(`#${v === "editor" ? "editor-body" : v + "-body"}`).innerHTML = noSession(); return; }
        return { editor: openEditor, ats: openAts, jobs: openJobs }[v](sid);
      }
    }
    if (h.startsWith("#/start")) { showView("landing"); renderRecent(); return; }
    showView("home");
    window.startRobot && window.startRobot();
  } catch (e) { toast(e.message); }
}
window.addEventListener("hashchange", route);

/* ---------------- landing: recent sessions ---------------- */
async function renderRecent() {
  page.sessions = await api("/api/sessions");
  $("#recent").hidden = !page.sessions.length;
  $("#recent-list").innerHTML = page.sessions.slice(0, 8).map((s) => `
    <a class="recent-item" href="#s=${esc(s.session_id)}">
      <strong>${esc(sessionLabel(s))}</strong>
      <span class="muted">${esc(s.filename)} · v${s.head} · ${fmtDate(s.created)}</span>
      ${s.score !== null && s.score !== undefined ? `<span class="score-pill small">${Number(s.score).toFixed(1)}</span>` : ""}
    </a>`).join("");
}

/* ---------------- cover letters ---------------- */
async function openLetters(sid) {
  const meta = await loadMeta();
  $("#writer-chip").innerHTML = meta.llm.configured
    ? `✦ Writer: ${esc(meta.llm.provider)} (${esc(meta.llm.model)}), fact-checked`
    : `Writer: built-in templates <span class="muted">· add GROQ_API_KEY to .env to write with Groq</span>`;
  page.sessions = await api("/api/sessions");
  const sel = $("#letter-session");
  if (!page.sessions.length) {
    sel.innerHTML = "";
    $("#letter-editor").innerHTML = `<div class="empty"><p>Tailor a resume first. Cover letters are drafted from a resume and job description.</p><a class="btn primary" href="#/start">Tailor a resume</a></div>`;
    $("#letter-list").innerHTML = "";
    return;
  }
  sel.innerHTML = page.sessions.map((s) => `<option value="${esc(s.session_id)}">${esc(sessionLabel(s))}</option>`).join("");
  page.letterSid = sid && page.sessions.some((s) => s.session_id === sid) ? sid : page.sessions[0].session_id;
  sel.value = page.letterSid;
  await loadLetters();
}

$("#letter-session").addEventListener("change", (e) => {
  history.replaceState(null, "", `#/letters?s=${e.target.value}`);
  page.letterSid = e.target.value; page.letterId = null; loadLetters();
});

async function loadLetters() {
  page.letters = await api(`/api/sessions/${page.letterSid}/letters`);
  if (!page.letters.some((l) => l.id === page.letterId)) page.letterId = page.letters[0]?.id || null;
  renderLetterList();
  renderLetterEditor();
}

function renderLetterList() {
  $("#letter-list").innerHTML = page.letters.length ? page.letters.map((l) => `
    <button class="letter-item ${l.id === page.letterId ? "active" : ""}" data-lid="${esc(l.id)}">
      <span>${esc(l.tone)} · ${l.validation.word_count} words</span>
      <small class="muted">${fmtDate(l.updated)} · ${l.writer === "ai" || l.writer === "claude" ? esc(l.writer_label || "Claude") : "template"} · ${l.validation.passed ? "✓ checked" : "needs edits"}</small>
    </button>`).join("") : `<p class="muted" style="font-size:13px">No letters yet for this job.</p>`;
  document.querySelectorAll(".letter-item").forEach((b) => b.addEventListener("click", () => {
    page.letterId = b.dataset.lid; renderLetterList(); renderLetterEditor();
  }));
}

function renderLetterEditor() {
  const l = page.letters.find((x) => x.id === page.letterId);
  const ed = $("#letter-editor");
  if (!l) { ed.innerHTML = `<div class="empty"><p class="muted">Draft a letter for this job. You'll be able to edit every paragraph before downloading.</p></div>`; return; }
  const v = l.validation;
  const byPara = {};
  v.issues.forEach((i) => (byPara[i.paragraph] = byPara[i.paragraph] || []).push(i));
  const base = `/api/sessions/${page.letterSid}/letters/${l.id}`;
  ed.innerHTML = `
    <div class="letter-status ${v.passed ? "ok" : "bad"}">
      ${v.passed ? "✓ Every claim is supported by your resume or notes, and the writing checks passed." : `✕ ${v.issues.filter((i) => i.severity === "fail").length} issue(s) to fix before downloading.`}
      <span class="muted">· ${v.word_count} words · ${l.writer === "ai" || l.writer === "claude" ? `drafted by ${esc(l.writer_label || "Claude")}` : "built-in draft"}</span>
    </div>
    ${l.writer_note ? `<p class="comp-deltas">${esc(l.writer_note)}</p>` : ""}
    <label class="field"><span>Greeting</span><input type="text" id="lt-greeting" value="${esc(l.greeting)}"></label>
    ${l.paragraphs.map((p, i) => `
      <label class="field"><span>Paragraph ${i + 1}</span>
        <textarea class="lt-para ${byPara[i]?.some((x) => x.severity === "fail") ? "flagged" : ""}" rows="${Math.max(3, Math.ceil(p.length / 90))}">${esc(p)}</textarea></label>
      ${(byPara[i] || []).map((x) => `<div class="check ${x.severity}">${x.severity === "fail" ? "✕" : "!"} ${esc(x.message)}${x.sentence ? ` <span class="muted">“${esc(x.sentence)}”</span>` : ""}</div>`).join("")}`).join("")}
    <button class="btn small" id="lt-add">+ Add paragraph</button>
    <label class="field"><span>Closing</span><textarea id="lt-closing" rows="2">${esc(l.closing)}</textarea></label>
    <div class="actions">
      <button class="btn primary" id="lt-save">Save &amp; re-check</button>
      <a class="btn ${v.passed ? "" : "disabled-link"}" ${v.passed ? `href="${base}/download?format=docx"` : 'aria-disabled="true"'}>Download DOCX</a>
      <a class="btn ${v.passed ? "" : "disabled-link"}" ${v.passed ? `href="${base}/download?format=txt"` : 'aria-disabled="true"'}>Download TXT</a>
      <button class="btn" id="lt-copy">Copy text</button>
      <span class="spacer"></span>
      <button class="btn danger" id="lt-delete">Delete</button>
    </div>`;
  const collect = () => ({
    greeting: $("#lt-greeting").value,
    paragraphs: [...document.querySelectorAll(".lt-para")].map((t) => t.value),
    closing: $("#lt-closing").value,
  });
  $("#lt-add").addEventListener("click", () => { Object.assign(l, collect()); l.paragraphs.push(""); renderLetterEditor(); });
  $("#lt-save").addEventListener("click", async () => {
    try {
      const upd = await api(base, { method: "PUT", body: collect() });
      Object.assign(l, upd); renderLetterList(); renderLetterEditor();
      toast(upd.validation.passed ? "Saved. All checks passed." : "Saved. Some sentences still need attention.");
    } catch (e) { toast(e.message); }
  });
  $("#lt-copy").addEventListener("click", async () => {
    const c = collect();
    try { await navigator.clipboard.writeText([c.greeting, ...c.paragraphs, c.closing].join("\n\n")); toast("Copied to clipboard"); }
    catch { toast("Copy failed. Select the text manually."); }
  });
  $("#lt-delete").addEventListener("click", async () => {
    if (!confirm("Delete this cover letter? This cannot be undone.")) return;
    await api(base, { method: "DELETE" }); page.letterId = null; loadLetters();
  });
}

$("#letter-generate").addEventListener("click", async () => {
  const btn = $("#letter-generate");
  $("#letter-error").textContent = "";
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Drafting…';
  try {
    const l = await api(`/api/sessions/${page.letterSid}/letters`, {
      body: { tone: $("#letter-tone").value, length: $("#letter-length").value,
              hiring_manager: $("#letter-manager").value, notes: $("#letter-notes").value },
    });
    page.letterId = l.id;
    await loadLetters();
  } catch (e) { $("#letter-error").textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = "Draft cover letter"; }
});

/* ---------------- tracker ---------------- */
async function loadBoard() {
  const meta = await loadMeta();
  page.jobs = await api("/api/jobs");
  const board = $("#board");
  board.innerHTML = meta.statuses.map((st) => {
    const jobs = page.jobs.filter((j) => j.status === st);
    return `<section class="col" data-status="${st}" aria-label="${STATUS_LABELS[st]}">
      <header><span class="dot ${st}"></span>${STATUS_LABELS[st]}<span class="count">${jobs.length}</span></header>
      <div class="col-body">${jobs.map(jobCard).join("") || `<p class="col-empty">Drop a job here</p>`}</div>
    </section>`;
  }).join("");
  bindBoard();
}

function jobCard(j) {
  const s = j.session;
  const overdue = j.deadline && new Date(j.deadline) < new Date(new Date().toDateString());
  return `<article class="job" draggable="true" data-id="${esc(j.id)}">
    <div class="job-top"><strong>${esc(j.company || "Untitled company")}</strong>
      ${s && s.score !== null && s.score !== undefined ? `<span class="score-pill small" title="Current resume compatibility score">${Number(s.score).toFixed(1)}</span>` : ""}</div>
    <div>${esc(j.title)}</div>
    <div class="job-meta muted">${[j.location, j.applied_on && `applied ${esc(j.applied_on)}`].filter(Boolean).map(esc).join(" · ")}</div>
    ${j.deadline ? `<div class="job-meta ${overdue ? "overdue" : ""}">Next step: ${esc(j.deadline)}</div>` : ""}
    <div class="job-links">
      ${s ? `<a href="#s=${esc(s.session_id)}">Resume v${s.head}</a><a href="#/letters?s=${esc(s.session_id)}">Letters (${s.letters})</a>` : `<a href="#/start">Tailor resume</a>`}
      ${/^https?:\/\//.test(j.url) ? `<a href="${esc(j.url)}" target="_blank" rel="noopener noreferrer">Posting ↗</a>` : ""}
    </div>
    <label class="sr-only" for="st-${esc(j.id)}">Status</label>
    <select class="job-status" id="st-${esc(j.id)}" data-id="${esc(j.id)}">${Object.entries(STATUS_LABELS).map(([k, v]) => `<option value="${k}" ${k === j.status ? "selected" : ""}>${v}</option>`).join("")}</select>
  </article>`;
}

async function setStatus(id, status) {
  try { await api(`/api/jobs/${id}`, { method: "PATCH", body: { status } }); await loadBoard(); }
  catch (e) { toast(e.message); }
}

function bindBoard() {
  document.querySelectorAll(".job").forEach((card) => {
    card.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/plain", card.dataset.id); card.classList.add("dragging"); });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("click", (e) => {
      if (e.target.closest("a, select")) return;
      openJobDialog(page.jobs.find((j) => j.id === card.dataset.id));
    });
  });
  document.querySelectorAll(".job-status").forEach((sel) => sel.addEventListener("change", () => setStatus(sel.dataset.id, sel.value)));
  document.querySelectorAll(".col").forEach((col) => {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("over"); });
    col.addEventListener("dragleave", () => col.classList.remove("over"));
    col.addEventListener("drop", (e) => {
      e.preventDefault(); col.classList.remove("over");
      const id = e.dataTransfer.getData("text/plain");
      const job = page.jobs.find((j) => j.id === id);
      if (job && job.status !== col.dataset.status) setStatus(id, col.dataset.status);
    });
  });
}

function openJobDialog(job) {
  page.editJob = job || null;
  const form = $("#job-form");
  form.reset();
  const F = (k) => form.elements.namedItem(k);
  F("status").innerHTML = Object.entries(STATUS_LABELS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
  $("#job-dialog-title").textContent = job ? "Edit job" : "Add job";
  $("#job-delete").hidden = !job;
  for (const k of ["company", "title", "status", "location", "url", "applied_on", "deadline", "salary", "contact", "notes"]) {
    F(k).value = job ? job[k] || "" : k === "status" ? "saved" : "";
  }
  $("#job-history").innerHTML = job ? "History: " + job.history.map((h) => `${STATUS_LABELS[h.status]} ${fmtDate(h.at)}`).join(" → ") : "";
  const dlg = $("#job-dialog");
  dlg.returnValue = "";
  dlg.showModal();
  dlg.onclose = async () => {
    const action = dlg.returnValue;
    if (action === "delete" && page.editJob) {
      if (!confirm(`Delete ${page.editJob.company || "this job"} from the tracker? Its resume versions and letters are kept.`)) return;
      await api(`/api/jobs/${page.editJob.id}`, { method: "DELETE" });
    } else if (action === "save") {
      const body = Object.fromEntries(["company", "title", "status", "location", "url", "applied_on", "deadline", "salary", "contact", "notes"].map((k) => [k, F(k).value]));
      try {
        if (page.editJob) await api(`/api/jobs/${page.editJob.id}`, { method: "PATCH", body });
        else await api("/api/jobs", { body });
      } catch (e) { toast(e.message); }
    }
    loadBoard();
  };
}
$("#add-job").addEventListener("click", () => openJobDialog(null));

route();
