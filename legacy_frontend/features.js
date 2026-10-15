/* CareerCopilot AI – Resume editor (editable + draggable), ATS score explained, eligible jobs.
 * Depends on helpers from app.js ($, esc, api, toast, fmt). All numbers come from the backend engine. */
"use strict";

const SEC_LABEL = { contact: "Contact", summary: "Summary", experience: "Experience", projects: "Projects", skills: "Skills",
  education: "Education", certifications: "Certifications", other: "Additional information" };
const KIND_LABEL = { paragraph: "Paragraph", bullet: "Bullet", entry_header: "Title / date line", skills_line: "Skills line", contact: "Contact" };

async function activeSid(fromUrl) {
  if (fromUrl) { try { localStorage.setItem("cc_last_sid", fromUrl); } catch { /* storage unavailable */ } return fromUrl; }
  let sid = null;
  try { sid = localStorage.getItem("cc_last_sid"); } catch { /* ignore */ }
  const list = await api("/api/sessions");
  if (sid && list.some((s) => s.session_id === sid)) return sid;
  return list[0]?.session_id || null;
}
window.activeSid = activeSid;

function noSession() {
  return `<div class="panel empty-panel"><p>Upload a resume and job description first.</p><a class="btn primary" href="#/start">Tailor a resume</a></div>`;
}
window.noSession = noSession;

/* ============================== Editor ============================== */
const ed = { sid: null, draft: null, checks: {}, saveTimer: null, dragLine: null, dragSection: null };

async function openEditor(sid) {
  ed.sid = sid;
  $("#editor-body").innerHTML = `<p class="muted"><span class="spinner"></span> Loading your resume…</p>`;
  $("#ed-export-pdf").href = `/api/sessions/${sid}/editor/export?format=pdf`;
  $("#ed-export-docx").href = `/api/sessions/${sid}/editor/export?format=docx`;
  try { renderEditorPayload(await api(`/api/sessions/${sid}/editor`)); }
  catch (e) { $("#editor-body").innerHTML = `<p class="blocked">${esc(e.message)}</p>`; }
}
window.openEditor = openEditor;

function renderEditorPayload(p) {
  ed.draft = p.draft;
  ed.checks = p.checks || {};
  const sc = p.score;
  $("#editor-score").innerHTML = `<div class="score-big"><strong>${fmt(sc.draft)}</strong><span class="muted">/ 100 with these edits</span></div>
    <div class="muted" style="font-size:12px">Current document: ${fmt(sc.document)}</div>`;
  $("#editor-alert").innerHTML = p.stale
    ? `<p class="blocked">Your document has changed since this draft was created. <button class="btn small" onclick="resetEditor()">Reload from document</button></p>`
    : p.pending_original ? `<p class="comp-deltas">${p.pending_original} edited line(s) can be saved into your original file with its exact fonts and layout.</p>` : "";
  renderEditor();
}

function renderEditor() {
  const body = $("#editor-body");
  body.innerHTML = ed.draft.sections.map((s, si) => `
    <section class="ed-section" draggable="true" data-si="${si}" aria-label="${esc(s.title || SEC_LABEL[s.type] || s.type)}">
      <header class="ed-section-head">
        <span class="drag-handle" title="Drag to move this section" aria-hidden="true">⠿</span>
        <input class="ed-title" data-si="${si}" value="${esc(s.title || (s.type === "contact" ? "" : SEC_LABEL[s.type] || s.type))}"
          placeholder="${s.type === "contact" ? "Contact (no heading)" : "Section heading"}" aria-label="Section heading" ${s.type === "contact" ? "disabled" : ""}>
        <span class="chip neutral">${esc(SEC_LABEL[s.type] || s.type)}</span>
        <button class="btn small ghost" data-act="sec-up" data-si="${si}" aria-label="Move section up" ${si === 0 ? "disabled" : ""}>↑</button>
        <button class="btn small ghost" data-act="sec-down" data-si="${si}" aria-label="Move section down" ${si === ed.draft.sections.length - 1 ? "disabled" : ""}>↓</button>
      </header>
      <div class="ed-lines" data-si="${si}">
        ${s.lines.map((l, li) => lineHtml(si, li, l)).join("")}
        <div class="ed-drop-end" data-si="${si}" data-li="${s.lines.length}"></div>
      </div>
      <button class="btn small" data-act="add-line" data-si="${si}">+ Add line</button>
    </section>`).join("");
  bindEditor();
}

function lineHtml(si, li, l) {
  const chk = ed.checks[l.id];
  return `
    <div class="ed-line ${chk ? "has-issue" : ""}" draggable="true" data-si="${si}" data-li="${li}">
      <span class="drag-handle" title="Drag to move this line" aria-hidden="true">⠿</span>
      <div class="ed-line-main">
        <textarea class="ed-text" data-si="${si}" data-li="${li}" rows="${Math.max(1, Math.ceil(l.text.length / 95))}" aria-label="Resume line">${esc(l.text)}</textarea>
        ${chk ? `<div class="ed-issue">${chk.issues.map(esc).join(" · ")}
          ${chk.suggestion ? `<div class="ed-suggest"><span class="muted">Suggested:</span> ${esc(chk.suggestion)}
            <button class="btn small" data-act="use-suggestion" data-si="${si}" data-li="${li}">Use this</button></div>` : ""}</div>` : ""}
      </div>
      <div class="ed-line-tools">
        <select class="ed-kind" data-si="${si}" data-li="${li}" aria-label="Line type">
          ${Object.entries(KIND_LABEL).map(([k, v]) => `<option value="${k}" ${k === l.kind ? "selected" : ""}>${v}</option>`).join("")}
        </select>
        <button class="btn small" data-act="improve" data-si="${si}" data-li="${li}" title="Rewrite with professional, error-free wording">✦ Improve</button>
        <button class="btn small ghost" data-act="up" data-si="${si}" data-li="${li}" aria-label="Move line up">↑</button>
        <button class="btn small ghost" data-act="down" data-si="${si}" data-li="${li}" aria-label="Move line down">↓</button>
        <button class="btn small ghost danger" data-act="del" data-si="${si}" data-li="${li}" aria-label="Delete line">✕</button>
      </div>
    </div>`;
}

function scheduleSave() {
  $("#ed-status").textContent = "Saving…";
  clearTimeout(ed.saveTimer);
  ed.saveTimer = setTimeout(async () => {
    try {
      const p = await api(`/api/sessions/${ed.sid}/editor`, { method: "PUT", body: { draft: ed.draft } });
      ed.checks = p.checks || {};
      const focused = document.activeElement;
      const keep = focused && focused.classList.contains("ed-text") ? [focused.dataset.si, focused.dataset.li, focused.selectionStart] : null;
      renderEditorPayload(p);
      if (keep) {
        const t = document.querySelector(`.ed-text[data-si="${keep[0]}"][data-li="${keep[1]}"]`);
        if (t) { t.focus(); t.setSelectionRange(keep[2], keep[2]); }
      }
      $("#ed-status").textContent = "All changes saved";
    } catch (e) { $("#ed-status").textContent = ""; toast(e.message); }
  }, 700);
}

function moveLine(fromSi, fromLi, toSi, toLi) {
  const [line] = ed.draft.sections[fromSi].lines.splice(fromLi, 1);
  if (fromSi === toSi && toLi > fromLi) toLi -= 1;
  ed.draft.sections[toSi].lines.splice(toLi, 0, line);
  renderEditor(); scheduleSave();
}

function moveSection(from, to) {
  const [sec] = ed.draft.sections.splice(from, 1);
  ed.draft.sections.splice(to, 0, sec);
  renderEditor(); scheduleSave();
}

function bindEditor() {
  const body = $("#editor-body");
  body.querySelectorAll(".ed-text").forEach((t) => t.addEventListener("input", () => {
    ed.draft.sections[t.dataset.si].lines[t.dataset.li].text = t.value;
    t.rows = Math.max(1, Math.ceil(t.value.length / 95));
    scheduleSave();
  }));
  body.querySelectorAll(".ed-title").forEach((t) => t.addEventListener("input", () => { ed.draft.sections[t.dataset.si].title = t.value; scheduleSave(); }));
  body.querySelectorAll(".ed-kind").forEach((k) => k.addEventListener("change", () => { ed.draft.sections[k.dataset.si].lines[k.dataset.li].kind = k.value; scheduleSave(); }));
  body.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    const si = Number(b.dataset.si), li = Number(b.dataset.li);
    const lines = ed.draft.sections[si]?.lines;
    switch (b.dataset.act) {
      case "add-line": lines.push({ id: Math.random().toString(16).slice(2, 10), text: "", kind: lines.at(-1)?.kind || "bullet", block_id: null, original: "" }); renderEditor(); break;
      case "del": lines.splice(li, 1); renderEditor(); scheduleSave(); break;
      case "up": if (li > 0) moveLine(si, li, si, li - 1); else if (si > 0) moveLine(si, li, si - 1, ed.draft.sections[si - 1].lines.length); break;
      case "down": if (li < lines.length - 1) moveLine(si, li, si, li + 2); else if (si < ed.draft.sections.length - 1) moveLine(si, li, si + 1, 0); break;
      case "sec-up": moveSection(si, si - 1); break;
      case "sec-down": moveSection(si, si + 1); break;
      case "use-suggestion": lines[li].text = ed.checks[lines[li].id].suggestion; renderEditor(); scheduleSave(); break;
      case "improve": {
        b.disabled = true; b.innerHTML = '<span class="spinner"></span>';
        try {
          const r = await api(`/api/sessions/${ed.sid}/editor/improve`, { body: { text: lines[li].text, kind: lines[li].kind } });
          if (r.changed) { lines[li].text = r.text; renderEditor(); scheduleSave(); toast("Line improved – review it before exporting."); }
          else { toast("This line already reads professionally."); renderEditor(); }
        } catch (e) { toast(e.message); renderEditor(); }
        break;
      }
    }
  }));

  // ---- drag & drop: lines (within and across sections) and whole sections
  body.querySelectorAll(".ed-line").forEach((row) => {
    row.addEventListener("dragstart", (e) => {
      if (e.target.closest("textarea, select, button")) return e.preventDefault();
      e.stopPropagation();
      ed.dragLine = [Number(row.dataset.si), Number(row.dataset.li)];
      ed.dragSection = null;
      row.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", "line");
    });
    row.addEventListener("dragend", () => row.classList.remove("dragging"));
  });
  body.querySelectorAll(".ed-line, .ed-drop-end").forEach((target) => {
    target.addEventListener("dragover", (e) => { if (ed.dragLine) { e.preventDefault(); target.classList.add("drop-before"); } });
    target.addEventListener("dragleave", () => target.classList.remove("drop-before"));
    target.addEventListener("drop", (e) => {
      if (!ed.dragLine) return;
      e.preventDefault(); e.stopPropagation();
      target.classList.remove("drop-before");
      const [fs, fl] = ed.dragLine; ed.dragLine = null;
      moveLine(fs, fl, Number(target.dataset.si), Number(target.dataset.li));
    });
  });
  body.querySelectorAll(".ed-section").forEach((sec) => {
    sec.addEventListener("dragstart", (e) => {
      if (ed.dragLine || !e.target.classList.contains("ed-section")) return;
      ed.dragSection = Number(sec.dataset.si);
      sec.classList.add("dragging");
      e.dataTransfer.setData("text/plain", "section");
    });
    sec.addEventListener("dragend", () => sec.classList.remove("dragging"));
    sec.addEventListener("dragover", (e) => { if (ed.dragSection !== null) { e.preventDefault(); sec.classList.add("drop-before"); } });
    sec.addEventListener("dragleave", () => sec.classList.remove("drop-before"));
    sec.addEventListener("drop", (e) => {
      if (ed.dragSection === null) return;
      e.preventDefault();
      sec.classList.remove("drop-before");
      const from = ed.dragSection; ed.dragSection = null;
      const to = Number(sec.dataset.si);
      if (from !== to) moveSection(from, to);
    });
  });
}

window.resetEditor = async function () {
  if (!confirm("Discard your editor changes and reload from the current document?")) return;
  renderEditorPayload(await api(`/api/sessions/${ed.sid}/editor/reset`, { body: {} }));
};
$("#ed-reset").addEventListener("click", () => window.resetEditor());
$("#ed-apply").addEventListener("click", async () => {
  const btn = $("#ed-apply");
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Saving into your file…';
  try {
    clearTimeout(ed.saveTimer);
    await api(`/api/sessions/${ed.sid}/editor`, { method: "PUT", body: { draft: ed.draft } });
    const r = await api(`/api/sessions/${ed.sid}/editor/apply-to-original`, { body: {} });
    renderEditorPayload(r);
    toast(r.applied.length ? `Saved ${r.applied.length} line(s) into your original file (new version v${r.head}).` : "No edited text to save into the original file.");
    if (r.skipped.length) $("#editor-alert").innerHTML += `<p class="blocked">Not saved: ${r.skipped.map((x) => `“${esc(x.text.slice(0, 60))}…” – ${esc(x.reason)}`).join("<br>")}</p>`;
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; btn.textContent = "Save text edits into my original file"; }
});

/* ============================== ATS explained ============================== */
async function openAts(sid) {
  const box = $("#ats-body");
  box.innerHTML = `<p class="muted"><span class="spinner"></span> Calculating…</p>`;
  let d;
  try { d = await api(`/api/sessions/${sid}/ats-explained`); } catch (e) { box.innerHTML = `<p class="blocked">${esc(e.message)}</p>`; return; }
  const sc = d.score;
  const comps = Object.values(sc.components).filter((c) => c.applicable);
  const tw = comps.reduce((a, c) => a + c.weight, 0);
  const rows = comps.map((c) => `<tr><td>${esc(c.label)}</td><td>${(100 * c.score).toFixed(1)}</td><td>${Math.round(100 * c.weight / tw)}%</td>
    <td><strong>${(100 * c.score * c.weight / tw).toFixed(1)}</strong></td></tr>`).join("");
  const lvl = { demonstrated: "Shown in experience/projects", contextual: "Summary or tech line", implied: "Implied by a related skill", listed: "Skills list only", none: "Not found" };
  box.innerHTML = `
    <div class="ats-grid">
      <section class="panel">
        <h2>1. Your score: ${fmt(sc.overall)} / 100</h2>
        <p>Overall = Σ (component score × weight). Each component is scored 0–100, then weighted:</p>
        <table class="ats-table"><thead><tr><th>Component</th><th>Score</th><th>Weight</th><th>Points</th></tr></thead>
          <tbody>${rows}</tbody><tfoot><tr><td colspan="3"><strong>Total</strong></td><td><strong>${fmt(sc.overall)}</strong></td></tr></tfoot></table>
        <p class="muted" style="font-size:12px">Methodology ${esc(sc.methodology)}. If the job description has no education or experience requirements, that component is left out and the others are re-weighted.</p>
      </section>
      <section class="panel">
        <h2>2. Keywords from this job</h2>
        <p>A skill earns credit <strong>once</strong>, at its strongest evidence. Repeating a keyword never adds points. Required skills weigh ${d.importance_weights.required}, preferred ${d.importance_weights.preferred}.</p>
        <table class="ats-table"><thead><tr><th>Skill</th><th>Importance</th><th>Where it appears</th><th>Credit</th></tr></thead><tbody>
        ${d.skills.map((s) => `<tr><td>${esc(s.name)}${s.generic ? ' <span class="chip neutral">general</span>' : ""}</td><td>${esc(s.importance)}</td>
          <td><span class="chip ${s.status}">${esc(lvl[s.evidence_level] || s.evidence_level)}</span>${s.where ? `<div class="muted" style="font-size:12px">“${esc(s.where)}”</div>` : ""}</td>
          <td>${Math.round(100 * s.credit)}%</td></tr>`).join("") || `<tr><td colspan="4" class="muted">No specific skills were found in this job description.</td></tr>`}
        </tbody></table>
      </section>
      <section class="panel">
        <h2>3. Format & parsing check</h2>
        <p>${d.format.ats_friendly ? "✓ Your file is ATS-friendly." : `${d.format.errors} problem(s) and ${d.format.warnings} warning(s) could stop an ATS from reading your resume correctly.`}</p>
        <ul class="audit">${d.format.findings.map((f) => `<li class="sev-${f.severity}"><strong>${f.severity === "pass" ? "✓" : f.severity === "info" ? "ℹ" : "!"} ${esc(f.title)}</strong> – ${esc(f.detail)}${f.fix ? `<div class="muted">${esc(f.fix)}</div>` : ""}</li>`).join("")}</ul>
      </section>
      <section class="panel">
        <h2>4. How employers' ATS actually work</h2>
        <ul class="explain">
          <li><strong>There is no single universal “ATS score”.</strong> Employers use systems such as Workday, Greenhouse, Lever, iCIMS, Taleo and SuccessFactors. Each is configured differently by each company.</li>
          <li><strong>Parsing comes first.</strong> The ATS extracts your text into fields (contact, jobs, dates, education, skills). Columns, tables, text boxes, headers/footers, images and icons can be skipped or read in the wrong order. That is why the format check above matters.</li>
          <li><strong>Recruiters search and filter.</strong> Recruiters typically search for keywords, job titles, skills, locations and degrees. If an exact term (e.g. “REST API”, “Software testing”) is missing, your profile may not appear in their search, even if you have the skill.</li>
          <li><strong>Some systems rank or match.</strong> Certain ATS and add-ons show a match percentage based on how your resume's skills and titles compare with the job. The exact formula is proprietary and differs between vendors.</li>
          <li><strong>Knock-out questions.</strong> Application questions (work authorisation, location, notice period, minimum qualifications such as degree or CGPA) can filter candidates before anyone reads the resume.</li>
          <li><strong>A person decides.</strong> The ATS organises applications; recruiters and hiring managers make the decisions. Clear achievements and honest, specific evidence matter more than keyword stuffing, which many recruiters penalise.</li>
          <li><strong>What this means for you:</strong> use the job's exact wording for skills you really have, keep a simple single-column layout with standard headings, and show skills inside achievements, not only in a skills list.</li>
        </ul>
        <p class="muted" style="font-size:12px">This app's score is an estimate of keyword and evidence alignment using the documented method above. It is not any employer's score and does not predict interviews or offers.</p>
      </section>
    </div>`;
}
window.openAts = openAts;

/* ============================== Eligible jobs ============================== */
async function openJobs(sid) {
  const box = $("#jobs-body");
  box.innerHTML = `<p class="muted"><span class="spinner"></span> Matching roles…</p>`;
  let jobs;
  try { jobs = await api(`/api/sessions/${sid}/eligible-jobs`); } catch (e) { box.innerHTML = `<p class="blocked">${esc(e.message)}</p>`; return; }
  const card = (j) => `
    <article class="job-card ${j.eligible ? "eligible" : ""}">
      <div class="row"><h3>${esc(j.title)}</h3><span class="score-pill small">${fmt(j.score)}</span></div>
      <p class="muted" style="margin:0">${esc(j.level)} · ${j.required_coverage}% of core requirements met${j.degree_ok ? "" : " · degree requirement not met"}</p>
      ${j.matched.length ? `<p class="skills-ok">✓ ${j.matched.map(esc).join(", ")}</p>` : ""}
      ${j.missing.length ? `<p class="skills-miss">Missing: ${j.missing.map(esc).join(", ")}</p>` : ""}
      <div class="actions">${Object.entries(j.search).map(([k, u]) => `<a class="btn small" href="${esc(u)}" target="_blank" rel="noopener noreferrer">Search on ${esc(k)} ↗</a>`).join("")}
        <button class="btn small" data-track="${esc(j.title)}">Add to tracker</button></div>
    </article>`;
  const eligible = jobs.filter((j) => j.eligible), stretch = jobs.filter((j) => !j.eligible);
  box.innerHTML = `
    <h2 class="sub-h">You are eligible for (${eligible.length})</h2>
    <div class="job-grid">${eligible.map(card).join("") || `<p class="muted">No strong matches yet. See the stretch roles below.</p>`}</div>
    <h2 class="sub-h">Stretch roles: learn the missing skills first (${stretch.length})</h2>
    <div class="job-grid">${stretch.map(card).join("")}</div>
    <p class="muted" style="font-size:12px">“Eligible” means at least 60% of a role's core skill requirements and its degree requirement are met by your resume. Always check each posting's own criteria (e.g. CGPA, graduation year, location).</p>`;
  box.querySelectorAll("[data-track]").forEach((b) => b.addEventListener("click", async () => {
    try { await api("/api/jobs", { body: { title: b.dataset.track, company: "", status: "saved" } }); toast("Added to your tracker"); b.disabled = true; }
    catch (e) { toast(e.message); }
  }));
}
window.openJobs = openJobs;
