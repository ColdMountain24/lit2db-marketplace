"use strict";
// lit2db's page (D-211). Every step reads the project's state from the server and writes back
// only through it; nothing here decides what the database is. The researcher's ticks are sent
// as they are, and the server turns them into the ledger.

const STEPS = [
  ["question", "Your question"], ["collect", "What to collect"], ["papers", "Which papers"],
  ["reading", "Reading"], ["checking", "Checking"], ["spreadsheets", "Spreadsheets"]];
const LABEL = Object.fromEntries(STEPS);
const ORDER = STEPS.map(s => s[0]);
const KINDS = [["name", "a name, as printed"], ["number", "a number"], ["count", "a whole number"],
  ["category", "one of a short list"], ["text", "a sentence"]];

const S = { pid: null, st: null, view: "home", R: null, editing: -1, timers: [],
  showIdentity: false, showNormalized: false, query: null, editQuery: false, abstracts: null,
  limit: 20, mode: null, audit: null, reviewUrl: null, job: null, exported: false };

const $ = (sel, root = document) => root.querySelector(sel);
const main = () => $("#main");
const esc = s => String(s ?? "").replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Europe PMC titles arrive with markup, sometimes encoded twice ("&lt;sup&gt;"). DOMParser makes an
// inert document, so nothing in a title can run; two passes take both layers off.
const plain = s => [0, 1].reduce(t => new DOMParser().parseFromString(t, "text/html")
  .documentElement.textContent, String(s ?? ""));

async function api(path, body) {
  const opt = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let r;
  try { r = await fetch(path, opt); } catch (e) {
    throw new Error("lit2db is not answering. Is the Terminal window that started it still open?");
  }
  let j;
  try { j = await r.json(); } catch (e) { throw new Error("lit2db gave an answer it could not read."); }
  if (!j.ok) throw new Error(j.error || "Something went wrong.");
  return j;
}

function clearTimers() { S.timers.forEach(clearInterval); S.timers = []; }
function every(ms, fn) { const t = setInterval(fn, ms); S.timers.push(t); return t; }

function showError(msg) {
  const old = $("#err"); if (old) old.remove();
  const d = document.createElement("div");
  d.id = "err"; d.className = "notice bad"; d.textContent = msg;
  main().prepend(d); d.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ---------- text size ----------
function setSize(sz) {
  document.documentElement.dataset.size = sz;
  try { localStorage.setItem("lit2db.size", sz); } catch (e) { /* private window: fine */ }
  document.querySelectorAll(".size button").forEach(b => b.classList.toggle("on", b.dataset.size === sz));
}

// ---------- routing ----------
function go(step) { location.hash = `#/p/${encodeURIComponent(S.pid)}/${step}`; }
function idx(s) { return ORDER.indexOf(s); }
function reachable(s) {
  // Checking and Spreadsheets open as soon as one paper is read: a partial database is worth
  // looking at, and a long run should not hold the researcher's first look hostage.
  const partial = (s === "checking" || s === "spreadsheets") && S.st.progress.done > 0;
  return idx(s) <= idx(S.st.current) || S.st.steps[s] || partial;
}

async function refresh() { S.st = await api(`/api/state?p=${encodeURIComponent(S.pid)}`); }

async function route() {
  clearTimers();
  const m = location.hash.match(/^#\/p\/([^/]+)\/?(\w+)?/);
  if (!m) return showHome();
  const pid = decodeURIComponent(m[1]);
  if (pid !== S.pid) Object.assign(S, { R: null, query: null, abstracts: null, mode: null,
    audit: null, reviewUrl: null, exported: false, editing: -1, editQuery: false });
  S.pid = pid;
  try { await refresh(); } catch (e) { location.hash = "#/"; return; }
  const step = m[2] && LABEL[m[2]] ? m[2] : S.st.current;
  if (!reachable(step)) return go(S.st.current);
  if (S.view !== step) window.scrollTo(0, 0);
  S.view = step;
  render();
}

function render() {
  $("#crumb").innerHTML = S.pid ? `· <b>${esc(S.st.name)}</b>` : "";
  const nav = $("#progress");
  nav.hidden = !S.pid;
  if (S.pid) {
    nav.innerHTML = "<ol>" + STEPS.map(([k, l]) => {
      const cls = k === S.view ? "now" : (S.st.steps[k] || idx(k) < idx(S.st.current)) ? "done" : "";
      return `<li class="${cls}"><button data-go="${k}" ${reachable(k) ? "" : "disabled"}>${l}</button></li>`;
    }).join("") + "</ol>";
  }
  const views = { question: vQuestion, collect: vCollect, papers: vPapers, reading: vReading,
    checking: vChecking, spreadsheets: vSheets };
  main().onchange = null;
  main().innerHTML = views[S.view]();
  const after = { question: aQuestion, papers: aPapers, reading: aReading, checking: aChecking,
    spreadsheets: aSheets }[S.view];
  if (after) after();
}

function stepHead(n, title, lede) {
  return `<p class="step">Step ${n} of 6</p><h1>${title}</h1>${lede ? `<p class="lede">${lede}</p>` : ""}`;
}

function waitCard(title, hint) {
  return `<div class="card"><div class="wait"><span class="spinner"></span><div><b>${title}</b>
    <div class="hint">${hint} <span class="elapsed">0</span>s so far.</div></div></div></div>`;
}

function pollJob(id, done, fail) {
  const t = every(1200, async () => {
    let j;
    try { j = await api(`/api/job?id=${id}`); } catch (e) { clearInterval(t); return fail(e.message); }
    document.querySelectorAll(".elapsed").forEach(x => { x.textContent = j.elapsed; });
    if (j.status === "done") { clearInterval(t); S.job = null; done(j.result); }
    if (j.status === "failed") { clearInterval(t); S.job = null; fail(j.error); }
  });
}

// ---------- home ----------
async function showHome() {
  S.pid = null; S.st = null; S.view = "home";
  let h;
  try { h = await api("/api/home"); } catch (e) { main().innerHTML = ""; return showError(e.message); }
  let opened = false;
  try { opened = sessionStorage.getItem("lit2db.here") === "1"; } catch (e) { /* fine */ }
  if (h.here && !opened) {
    try { sessionStorage.setItem("lit2db.here", "1"); } catch (e) { /* fine */ }
    S.pid = h.here; return go("");
  }
  $("#crumb").innerHTML = ""; $("#progress").hidden = true;
  const list = h.projects.map(p => `<button class="proj" data-open="${esc(p.id)}">
      <span class="pn">${esc(p.name)}</span>
      <span class="pq">${esc(p.question || "No question yet")}</span>
      <span class="pnext">Next: ${esc(LABEL[p.current] || "")} →</span></button>`).join("");
  main().innerHTML = `
    <h1>Your databases</h1>
    <p class="lede">Each one starts from a question and ends as spreadsheets, where every value
      carries the sentence it came from.</p>
    ${list ? `<div class="card">${list}</div>` : ""}
    <div class="card">
      <h2>Start a new database</h2>
      <p class="sub">Give it a short name you will recognise. You can describe it properly next.</p>
      <div class="newrow"><input type="text" id="newname" placeholder="For example: Antibodies and the brain">
        <button class="yes" id="newgo">Start</button></div>
    </div>
    <div class="reassure"><span class="i">i</span><span>Kept on this Mac, in ${esc(h.home)}.</span></div>`;
  const start = async () => {
    try {
      const r = await api("/api/new", { name: $("#newname").value });
      S.pid = r.id; go("question");
    } catch (e) { showError(e.message); }
  };
  $("#newgo").onclick = start;
  $("#newname").onkeydown = e => { if (e.key === "Enter") start(); };
}

// ---------- 1. question ----------
function vQuestion() {
  const it = S.st.intake;
  const has = it.proposal && it.proposal_meta && it.proposal_meta.for_question === it.question;
  return `${stepHead(1, "What do you want to find out?",
    "Write it the way you would say it to a colleague. lit2db drafts a table from it, and you decide what goes in.")}
    <div class="card">
      <label class="field" for="q">Your question</label>
      <textarea id="q" class="big" placeholder="For example: how well do large antibodies get into the brain, and what helps them get across?">${esc(it.question)}</textarea>
      <details class="why" style="margin-top:1rem" ${it.seed ? "open" : ""}><summary>Have something to start from? (optional)</summary>
        <div style="margin-top:.6rem"><textarea id="seed" placeholder="A review you trust, a list of DOIs, or a database you would like to match.">${esc(it.seed)}</textarea>
        <p class="hint">lit2db treats it as the clearest statement of what you mean.</p></div>
      </details>
    </div>
    <div id="draftbox"></div>
    <div class="answer">
      <button class="yes" id="draft">${has ? "Draft it again" : "Draft my table"}</button>
      ${has ? `<button class="second" data-go="collect">Keep my current draft</button>` : ""}
    </div>
    <div class="reassure"><span class="i">i</span><span>Drafting takes about a minute and uses a little of your Claude plan. Nothing is searched or downloaded yet.</span></div>`;
}

function aQuestion() {
  $("#draft").onclick = async () => {
    const question = $("#q").value.trim();
    if (!question) return showError("Write your question first.");
    try {
      await api("/api/question", { p: S.pid, question, seed: $("#seed").value });
      const r = await api("/api/draft", { p: S.pid });
      $("#draft").disabled = true;
      $("#draftbox").innerHTML = waitCard("Drafting your table…",
        "Reading your question and choosing columns. Usually about a minute.");
      pollJob(r.job, async () => { await refresh(); S.R = null; go("collect"); },
        msg => { $("#draft").disabled = false; $("#draftbox").innerHTML = ""; showError(msg); });
    } catch (e) { showError(e.message); }
  };
}

// ---------- 2. what to collect ----------
function rulings() {
  if (!S.R) S.R = JSON.parse(JSON.stringify(S.st.intake.rulings));
  return S.R;
}
const labelOf = n => (rulings().columns.find(c => c.name === n) || {}).label || n;
const ticked = () => rulings().columns.filter(c => c.on && c.kind !== "spread");

let saveT = null;
function save(confirm = false) {
  clearTimeout(saveT);
  const R = rulings();
  const body = JSON.parse(JSON.stringify(R));
  if (body.row.choice !== "own") body.row.own = "";
  const send = () => api("/api/rulings", { p: S.pid, rulings: body, confirm });
  if (confirm) return send();
  saveT = setTimeout(() => send().catch(e => showError(e.message)), 500);
  return Promise.resolve();
}

function vCollect() {
  const it = S.st.intake, P = it.proposal, R = rulings();
  const rows = P.row_options.map((o, i) => `
    <label class="opt"><input type="radio" name="row" value="${i}" ${R.row.choice === i ? "checked" : ""}><span class="radio"></span>
      <span><span class="t">${esc(o.label)}</span>${o.suggested ? '<span class="tag">Suggested</span>' : ""}
      <div class="d">${esc(o.description)}${o.suggested && o.why ? " " + esc(o.why) : ""}</div></span></label>`).join("");
  const own = `<label class="opt"><input type="radio" name="row" value="own" ${R.row.choice === "own" ? "checked" : ""}><span class="radio"></span>
      <span><span class="t">Something else</span><input type="text" class="own" id="ownrow" placeholder="Describe one row in your own words" value="${esc(R.row.own)}"></span></label>`;
  const on = ticked();
  const must = on.filter(c => c.required).length;
  return `${stepHead(2, "What should each row of your table hold?",
    "lit2db drafted a table from your question. Tick what you want, change anything that is not quite right, and add what is missing. Nothing has been searched or downloaded yet.")}
    <div class="card"><div class="said"><div class="qm">“</div><div>
      <div class="k">Your question</div><div class="q">${esc(it.question)}</div>
      <button class="linkish" data-go="question">Change the question</button></div></div></div>
    <div class="card"><h2>One row of your table is…</h2><p class="sub">This decides what counts as one entry.</p>${rows}${own}</div>
    <div class="card"><h2>For each row, collect…</h2>
      <p class="sub">Green came from your question. Clay is a suggestion, with its reason one click away.</p>
      ${R.columns.map(colCard).join("")}
      <div class="addcol"><input type="text" id="newcol" placeholder="Add a column in your own words">
        <select id="newkind">${KINDS.map(([k, l]) => `<option value="${k}">${l}</option>`).join("")}</select>
        <button class="second" id="addcol">Add</button></div>
      <div id="newvals" hidden style="margin-top:.6rem"><input type="text" id="newvalues" placeholder="The choices, separated by commas"></div>
    </div>
    ${decidedCard(P, R)}
    <div class="answer">
      <button class="yes" id="confirm">Yes, these are right</button>
      <button class="backlink" data-go="question">Go back</button>
      <div class="tally">You picked <b>${on.length} ${on.length === 1 ? "column" : "columns"}</b>, and <b>${must}</b> that every row must have.</div>
    </div>
    <div class="reassure"><span class="i">i</span><span>You can change any of this until reading starts. Everything stays on this Mac.</span></div>`;
}

function colCard(c, i) {
  const src = c.added ? '<span class="src added">You added this</span>'
    : c.from_question ? '<span class="src theirs">From your question</span>'
    : '<span class="src ours">Suggested</span>';
  const vals = c.kind === "category" && c.values && c.values.length
    ? `<div class="values">One of: ${c.values.map(esc).join(", ")}</div>` : "";
  const note = c.kind === "text" ? `<div class="values">You check this one by eye: a sentence is worded differently every time it is read.</div>`
    : c.kind === "spread" ? `<div class="values">Goes beside ${esc(labelOf(c.qualifies))}.</div>` : "";
  const words = c.question_words ? `<div class="eg">Your words: “${esc(c.question_words)}”</div>` : "";
  const edit = S.editing === i ? `<div class="edit">
      <input type="text" id="e-label" value="${esc(c.label)}" aria-label="Name">
      <textarea id="e-def" aria-label="What goes in it">${esc(c.definition)}</textarea>
      ${c.kind === "category" ? `<input type="text" id="e-vals" value="${esc((c.values || []).join(", "))}" aria-label="Choices">` : ""}
      <div class="btns"><button class="second" data-act="esave">Save</button><button class="backlink" data-act="ecancel">Cancel</button></div></div>` : "";
  return `<div class="col ${c.on ? "on" : "off"}" data-i="${i}">
    <div class="row1">
      <label><input class="pick" type="checkbox" data-act="on" ${c.on ? "checked" : ""} aria-label="${esc(c.label)}"><span class="check"></span></label>
      <div><div class="nm">${esc(c.label)}</div><div class="def">${esc(c.definition)}</div>
        ${c.example ? `<div class="eg">For example <b>${esc(c.example)}</b></div>` : ""}${vals}${note}${words}</div>
      ${src}
    </div>
    <div class="row2">
      ${c.kind !== "spread" ? `<label class="must"><input type="checkbox" data-act="req" ${c.required ? "checked" : ""}> Every row must have this</label>` : ""}
      ${c.why ? `<details class="why"><summary>Why?</summary><div class="whytext">${esc(c.why)}</div></details>` : ""}
      <button class="mini" data-act="edit">Edit wording</button>
      ${c.added ? `<button class="mini bad" data-act="remove">Remove</button>` : ""}
    </div>${edit}</div>`;
}

function decidedCard(P, R) {
  const on = ticked();
  const idn = R.identity.filter(n => on.some(c => c.name === n));
  const nrm = R.normalized.filter(n => on.some(c => c.name === n));
  const chips = (list, attr) => `<div class="chips">${on.map(c =>
    `<button class="chip ${list.includes(c.name) ? "on" : ""}" ${attr}="${esc(c.name)}">${esc(c.label)}</button>`).join("")}</div>`;
  const ex = R.exclusions.map((x, i) => `<div class="d"><span class="${x.on ? "" : "off"}">Leave out ${esc(x.text.charAt(0).toLowerCase() + x.text.slice(1))}.</span>
      <label class="toggle"><input type="checkbox" data-ex="${i}" ${x.on ? "checked" : ""}> ${x.on ? "On" : "Off"}</label></div>`).join("");
  return `<div class="card defaults"><h2>We decided these for you</h2><p class="sub">Sensible defaults. Change any of them.</p>
    <div class="d"><span>Two rows count as the same when <b>${idn.map(n => esc(labelOf(n))).join(", ") || "nothing"}</b> all match.
      ${S.showIdentity ? chips(R.identity, "data-idn") : ""}</span>
      <button class="mini" data-act="showidn">${S.showIdentity ? "Done" : "Change"}</button></div>
    <div class="d"><span>Written in a standard form rather than copied word for word, so “mice” can be stored as “mouse”: <b>${nrm.map(n => esc(labelOf(n))).join(", ") || "none"}</b>.
      ${S.showNormalized ? chips(R.normalized, "data-nrm") : ""}</span>
      <button class="mini" data-act="shownrm">${S.showNormalized ? "Done" : "Change"}</button></div>
    ${ex}
    <div class="d"><span><input type="text" id="newex" placeholder="Leave out another kind of paper, for example “studies in cell culture only”"></span>
      <button class="mini" id="addex">Add</button></div>
    <div class="d"><span>What the table is for: ${esc(P.purpose.plain || P.purpose.ml_task)}</span><span class="lock">From the draft</span></div>
    <div class="d"><span>When a paper reports nothing: ${esc(P.negative_data)}</span><span class="lock">From the draft</span></div>
    <div class="d"><span>Every value keeps the exact sentence it came from.</span><span class="lock">Always on</span></div>
  </div>`;
}

function slugify(t) {
  let s = t.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "column";
  if (!/^[a-z]/.test(s)) s = "c_" + s;
  const names = new Set(rulings().columns.map(c => c.name));
  let out = s, n = 2;
  while (names.has(out)) out = `${s}_${n++}`;
  return out;
}

function onCollectClick(e) {
  const t = e.target, R = rulings();
  const col = t.closest(".col"); const i = col ? +col.dataset.i : -1;
  const act = t.dataset.act;
  if (act === "edit") { S.editing = i; return render(); }
  if (act === "ecancel") { S.editing = -1; return render(); }
  if (act === "esave") {
    const c = R.columns[i];
    c.label = $("#e-label").value.trim() || c.label;
    c.definition = $("#e-def").value.trim();
    if ($("#e-vals")) c.values = $("#e-vals").value.split(",").map(x => x.trim()).filter(Boolean);
    S.editing = -1; save(); return render();
  }
  if (act === "remove") { R.columns.splice(i, 1); S.editing = -1; save(); return render(); }
  if (act === "showidn") { S.showIdentity = !S.showIdentity; return render(); }
  if (act === "shownrm") { S.showNormalized = !S.showNormalized; return render(); }
  if (t.dataset.idn) {
    const n = t.dataset.idn;
    R.identity = R.identity.includes(n) ? R.identity.filter(x => x !== n) : [...R.identity, n];
    save(); return render();
  }
  if (t.dataset.nrm) {
    const n = t.dataset.nrm;
    R.normalized = R.normalized.includes(n) ? R.normalized.filter(x => x !== n) : [...R.normalized, n];
    save(); return render();
  }
  if (t.id === "addcol") {
    const text = $("#newcol").value.trim(); const kind = $("#newkind").value;
    if (!text) return showError("Write the new column in your own words first.");
    const values = kind === "category" ? $("#newvalues").value.split(",").map(x => x.trim()).filter(Boolean) : [];
    if (kind === "category" && values.length < 2) return showError("List at least two choices, separated by commas.");
    const label = text.length > 40 ? text.slice(0, 40).replace(/\s+\S*$/, "") : text;
    R.columns.push({ name: slugify(text), label: label.charAt(0).toUpperCase() + label.slice(1),
      definition: text, example: "", kind, unit: null, values, from_question: false,
      question_words: "", why: "", suggest_on: true, suggest_required: false, normalized: false,
      qualifies: null, on: true, required: false, added: true, verbatim: text });
    save(); return render();
  }
  if (t.id === "addex") {
    const text = $("#newex").value.trim();
    if (!text) return;
    R.exclusions.push({ text, on: true, added: true }); save(); return render();
  }
  if (t.id === "confirm") return confirmCollect();
}

function onCollectChange(e) {
  const t = e.target, R = rulings();
  const col = t.closest(".col"); const i = col ? +col.dataset.i : -1;
  if (t.name === "row") { R.row.choice = t.value === "own" ? "own" : +t.value; save(); return; }
  if (t.dataset.act === "on") {
    R.columns[i].on = t.checked;
    if (!t.checked) R.columns[i].required = false;
    save(); return render();
  }
  if (t.dataset.act === "req") { R.columns[i].required = t.checked; save(); return render(); }
  if (t.dataset.ex !== undefined) { R.exclusions[+t.dataset.ex].on = t.checked; save(); return render(); }
  if (t.id === "newkind") { $("#newvals").hidden = t.value !== "category"; }
}

function onCollectInput(e) {
  if (e.target.id === "ownrow") {
    const R = rulings(); R.row.own = e.target.value; R.row.choice = "own";
    const radio = document.querySelector('input[name=row][value=own]'); if (radio) radio.checked = true;
    save();
  }
}

async function confirmCollect() {
  const R = rulings();
  if (!ticked().length) return showError("Tick at least one column.");
  if (R.row.choice === "own" && !R.row.own.trim()) return showError("Describe one row in your own words, or pick one of the three.");
  const lost = R.columns.filter(c => c.on && c.kind === "spread" && !ticked().some(x => x.name === c.qualifies));
  if (lost.length) return showError(`“${lost[0].label}” goes beside ${labelOf(lost[0].qualifies)}, which is not ticked.`);
  try { await save(true); await refresh(); go("papers"); } catch (e) { showError(e.message); }
}

// ---------- 3. which papers ----------
function vPapers() {
  const it = S.st.intake, P = it.proposal, pp = it.papers || {};
  const query = S.query ?? pp.query ?? P.query;
  const pv = pp.preview && pp.preview.query === query ? pp.preview : null;
  const c = S.st.search.counts;
  if (S.abstracts === null && typeof pp.abstracts === "boolean") S.abstracts = pp.abstracts;
  if (pp.limit) S.limit = pp.limit;
  const fetched = S.st.spec.frozen && c && pp.approved && !S.editQuery && S.query === null;
  if (fetched) {
    const read = S.st.readable;
    return `${stepHead(3, "Your papers are saved")}
      <div class="card"><div class="big-num">${read}</div><p>papers ready to read.</p>
        <div class="stats">
          <div class="stat"><div class="n">${c.full_text || 0}</div><div class="l">in full</div></div>
          <div class="stat"><div class="n">${c.abstract_only || 0}</div><div class="l">as an abstract</div></div>
          <div class="stat"><div class="n">${c.unreachable || 0}</div><div class="l">not free to read</div></div>
          ${c.retracted ? `<div class="stat"><div class="n">${c.retracted}</div><div class="l">retracted, not read</div></div>` : ""}
        </div>
        <p class="hint">Every paper the search found, and what happened to it, goes in your Papers spreadsheet.</p></div>
      ${read ? "" : `<div class="notice warn">None of these could be read. Change the search, or allow abstracts.</div>`}
      <div class="answer"><button class="yes" data-go="reading" ${read ? "" : "disabled"}>Continue to Reading</button>
        ${S.st.progress.done ? "" : `<button class="backlink" id="research">Change the search</button>`}</div>`;
  }
  const preview = S.job === "preview" ? waitCard("Searching Europe PMC…", "Counting what the search finds. Nothing is downloaded.")
    : pv ? `<div class="card"><h2>${pv.hits.toLocaleString()} papers match</h2>
        <p class="sub">Of the first ${pv.examined}:</p>
        <div class="stats"><div class="stat"><div class="n">${pv.readable_full_text}</div><div class="l">free to read in full</div></div>
          <div class="stat"><div class="n">${pv.abstract_available}</div><div class="l">with a free abstract</div></div>
          ${pv.retracted ? `<div class="stat"><div class="n">${pv.retracted}</div><div class="l">retracted</div></div>` : ""}</div>
        ${pv.hits ? `<p class="k" style="margin-top:1rem">A few of them</p><ol class="titles">${pv.sample_titles.map(t => `<li>${esc(plain(t))}</li>`).join("")}</ol>
          <p class="hint">Do these look like your field? If not, change the search.</p>` : `<div class="notice warn">Nothing matches. Change the search.</div>`}</div>` : "";
  const abs = [[true, "Yes, read the abstract", "More papers, but an abstract says less than the paper."],
    [false, "No, full papers only", "Fewer papers, each read in full."]].map(([v, t, d]) => `
    <label class="opt"><input type="radio" name="abs" value="${v}" ${S.abstracts === v ? "checked" : ""}><span class="radio"></span><span><span class="t">${t}</span><div class="d">${d}</div></span></label>`).join("");
  const lim = [10, 20, 50, 100].map(n => `
    <label class="opt"><input type="radio" name="lim" value="${n}" ${S.limit === n ? "checked" : ""}><span class="radio"></span><span><span class="t">${n}</span>${n === 20 ? '<span class="tag">Suggested</span>' : ""}</span></label>`).join("");
  return `${stepHead(3, "Which papers should it read?", esc(P.query_plain || ""))}
    <div class="card"><h2>The search</h2><p class="sub">Run on Europe PMC, which holds most biomedical papers.</p>
      ${S.editQuery ? `<textarea class="mono" id="qedit">${esc(query)}</textarea>
        <div class="answer" style="margin-top:.8rem"><button class="second" id="qrun">Search again</button><button class="backlink" id="qcancel">Cancel</button></div>`
      : `<div class="query">${esc(query)}</div><button class="linkish" id="qchange">Change the search</button>`}</div>
    <div id="pv">${preview}</div>
    <div class="card"><h2>When only the abstract is free, should lit2db read it?</h2><p class="sub">Your call; it goes on the record of how this database was made.</p>${abs}</div>
    <div class="card"><h2>How many papers for this first database?</h2><p class="sub">Start small. You can add more later.</p><div class="opts-row">${lim}</div></div>
    <div id="fetchbox"></div>
    <div class="answer"><button class="yes" id="approve" ${pv && pv.hits && S.abstracts !== null ? "" : "disabled"}>Use these papers</button>
      <button class="backlink" data-go="collect">Go back</button></div>`;
}

function aPapers() {
  const it = S.st.intake, pp = it.papers || {};
  const query = S.query ?? pp.query ?? it.proposal.query;
  const pv = pp.preview && pp.preview.query === query;
  const runPreview = async q => {
    try {
      const r = await api("/api/preview", { p: S.pid, query: q });
      S.job = "preview"; S.query = q; S.editQuery = false; render();
      pollJob(r.job, async () => { await refresh(); render(); }, msg => { render(); showError(msg); });
    } catch (e) { showError(e.message); }
  };
  if ($("#research")) $("#research").onclick = () => { S.editQuery = true; S.query = query; render(); };
  if (!$("#approve")) return;
  if (!pv && S.job !== "preview" && !S.editQuery) runPreview(query);
  if ($("#qchange")) $("#qchange").onclick = () => { S.editQuery = true; render(); };
  if ($("#qcancel")) $("#qcancel").onclick = () => { S.editQuery = false; render(); };
  if ($("#qrun")) $("#qrun").onclick = () => runPreview($("#qedit").value.trim());
  main().onchange = e => {
    if (e.target.name === "abs") { S.abstracts = e.target.value === "true"; render(); }
    if (e.target.name === "lim") { S.limit = +e.target.value; }
  };
  $("#approve").onclick = async () => {
    try {
      const r = await api("/api/approve", { p: S.pid, query, abstracts: S.abstracts, limit: S.limit });
      $("#approve").disabled = true;
      $("#fetchbox").innerHTML = waitCard("Saving the papers…",
        `Downloading what is free to read and storing it on this Mac. About a minute for ${S.limit}.`);
      $("#fetchbox").scrollIntoView({ behavior: "smooth", block: "center" });
      pollJob(r.job, async () => { await refresh(); S.query = null; render(); },
        msg => { $("#fetchbox").innerHTML = ""; $("#approve").disabled = false; showError(msg); });
    } catch (e) { showError(e.message); }
  };
}

// ---------- 4. reading ----------
function ago(iso) {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "just now" : m === 1 ? "a minute ago" : m < 90 ? `${m} minutes ago` : `${Math.round(m / 60)} hours ago`;
}

function vReading() {
  const pg = S.st.progress, n = S.st.readable;
  if (pg.running || pg.done || pg.finished) {
    const pct = pg.total ? Math.round(100 * pg.done / pg.total) : 0;
    const head = pg.finished ? stepHead(4, `All ${pg.total} papers read`)
      : pg.running ? stepHead(4, "Reading your papers", "You can close this page, and even Terminal. Reading carries on; type lit2db to look in.")
      : stepHead(4, `Paused after ${pg.done} of ${pg.total} papers`);
    return `${head}
      <div class="card"><div class="big-num">${pg.done} <span style="font-size:1.2rem;color:var(--text-3)">of ${pg.total}</span></div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <p>${pg.running ? `Now reading ${esc(pg.now || "the first paper")}. Started ${ago(pg.started)}.` : pg.finished ? "Every paper has been read and checked." : "Nothing is lost: carrying on picks up at the next paper."}</p>
        ${pg.questions ? `<p class="hint">lit2db noted ${pg.questions} things worth your eye along the way. They come up in Checking.</p>` : ""}
        <details class="why" style="margin-top:.6rem"><summary>What it is doing</summary><div class="log">${esc(pg.log.join("\n") || "Starting…")}</div></details></div>
      <div class="answer">
        ${pg.finished ? `<button class="yes" data-go="checking">Continue to Checking</button>`
          : pg.running ? `<button class="second" id="pause">Pause</button>`
          : `<button class="yes" id="resume">Carry on reading</button>`}
        ${!pg.finished && pg.done ? `<button class="second" data-go="checking">Check what is read so far</button>` : ""}
      </div>`;
  }
  const modes = [["careful", "Careful", `Three separate readings that have to agree, then a fact-check against the paper. About 4 minutes a paper, so roughly ${Math.max(1, Math.round(n * 4 / 5) * 5)} minutes for ${n}. Uses a lot of your Claude plan.`],
    ["quick", "Quick", "One reading, then one fact-check that also looks for anything in the paper that disagrees. Faster, and uses much less of your plan. Newer, and not yet measured against Careful."]]
    .map(([v, t, d]) => `<label class="opt"><input type="radio" name="mode" value="${v}" ${S.mode === v ? "checked" : ""}><span class="radio"></span><span><span class="t">${t}</span><div class="d">${d}</div></span></label>`).join("");
  const audits = [[1, "All of them", "Most thorough. Shows exactly how often a good value is turned down."],
    [0.2, "One in five", "A sample: enough to see how often a good value is turned down."],
    [0, "None", "Fastest. You will not know how often good values are turned down."]]
    .map(([v, t, d]) => `<label class="opt"><input type="radio" name="audit" value="${v}" ${S.audit === v ? "checked" : ""}><span class="radio"></span><span><span class="t">${t}</span><div class="d">${d}</div></span></label>`).join("");
  const ready = S.mode === "quick" || (S.mode === "careful" && S.audit !== null);
  return `${stepHead(4, `Ready to read ${n} papers`, "lit2db reads each paper on this Mac, with your own Claude plan, and keeps the exact sentence behind every value.")}
    <div class="card"><h2>How carefully?</h2><p class="sub">Both keep every value tied to its sentence. They differ in how many times each paper is read.</p>${modes}</div>
    ${S.mode === "careful" ? `<div class="card"><h2>When lit2db turns a value down, should the fact-check still read it?</h2>
      <p class="sub">This is how you find out whether the bar is too strict. There is no right answer, so lit2db does not pick one.</p>${audits}</div>` : ""}
    <div class="answer"><button class="yes" id="start" ${ready ? "" : "disabled"}>Start reading</button>
      <button class="backlink" data-go="papers">Go back</button></div>
    <div class="reassure"><span class="i">i</span><span>If your plan's usage limit is reached, lit2db waits for it to reset and carries on by itself.</span></div>`;
}

function aReading() {
  const pg = S.st.progress;
  main().onchange = e => {
    if (e.target.name === "mode") { S.mode = e.target.value; render(); }
    if (e.target.name === "audit") { S.audit = +e.target.value; render(); }
  };
  const start = async (mode, audit) => {
    try {
      $("#main").querySelectorAll("button").forEach(b => { b.disabled = true; });
      await api("/api/start", { p: S.pid, mode, audit });
      await refresh(); render();
    } catch (e) { render(); showError(e.message); }
  };
  if ($("#start")) $("#start").onclick = () => start(S.mode, S.audit);
  if ($("#resume")) $("#resume").onclick = () => start(pg.mode || "careful", 1);
  if ($("#pause")) $("#pause").onclick = async () => {
    try { await api("/api/pause", { p: S.pid }); setTimeout(async () => { await refresh(); render(); }, 1200); }
    catch (e) { showError(e.message); }
  };
  if (pg.running) every(4000, async () => {
    try { await refresh(); if (S.view === "reading") render(); } catch (e) { /* next tick */ }
  });
}

// ---------- 5. checking ----------
function vChecking() {
  const r = S.st.results;
  const acc = r.accepted ? r.accepted.rows : 0, cand = r.candidates ? r.candidates.rows : 0;
  return `${stepHead(5, "Check what lit2db was not sure about",
    "Values that passed every check are already in your table. The rest were held back, each with its reason. Your verdicts are what let the bar be set from your own judgement rather than a guess.")}
    <div class="card"><div class="stats">
      <div class="stat"><div class="n">${acc}</div><div class="l">accepted</div></div>
      <div class="stat"><div class="n">${cand}</div><div class="l">held for you</div></div></div>
      ${acc === 0 && cand ? `<p class="hint" style="margin-top:.8rem">An empty accepted list is the normal first result. The bar starts high on purpose, so values wait for you rather than entering unchecked.</p>` : ""}</div>
    ${S.reviewUrl ? `<iframe class="review" src="${esc(S.reviewUrl)}" title="Checking page"></iframe>
      <p class="hint"><a href="${esc(S.reviewUrl)}" target="_blank" rel="noopener">Open the checking page in its own tab</a></p>`
      : `<div class="card"><h2>The checking page</h2><p>Each held value sits beside the paper it came from, with the sentence highlighted. Say whether it is right, wrong, or cannot be told.</p>
        <div class="answer"><button class="yes" id="openreview" ${cand ? "" : "disabled"}>Open the checking page</button></div></div>`}
    <div class="answer"><button class="second" id="donecheck">I am done checking for now</button></div>`;
}

function aChecking() {
  if (!S.st.results.candidates && !S.exported) {
    S.exported = true;
    api("/api/export", { p: S.pid }).then(async () => { await refresh(); render(); }).catch(e => showError(e.message));
  }
  if ($("#openreview")) $("#openreview").onclick = async () => {
    try { const r = await api("/api/review", { p: S.pid }); S.reviewUrl = r.url; render(); }
    catch (e) { showError(e.message); }
  };
  $("#donecheck").onclick = async () => {
    try { await api("/api/checked", { p: S.pid }); await refresh(); go("spreadsheets"); }
    catch (e) { showError(e.message); }
  };
}

// ---------- 6. spreadsheets ----------
function vSheets() {
  const r = S.st.results;
  const file = (k, t, d) => r[k] ? `<div class="card"><div class="sheet"><div style="flex:1"><h2>${t}</h2>
      <p class="sub" style="margin:0">${d}</p></div><div class="stat"><div class="n">${r[k].rows}</div><div class="l">rows</div></div>
      <button class="second" data-open="${k}">Open</button></div></div>` : "";
  return `${stepHead(6, "Your spreadsheets", "Three files, each made from the database as it stands now, including your verdicts.")}
    ${file("accepted", "Accepted", "Values that passed every check, each with the sentence it came from.")}
    ${file("candidates", "Held for checking", "Values lit2db found but was not sure enough to accept, each with its quote, the reason, and your verdict.")}
    ${file("papers", "Papers", "Every paper the search found, and what happened to it.")}
    <div class="answer"><button class="second" data-open="folder">Show the folder</button>
      <button class="backlink" id="remake">Make them again</button></div>
    <div class="reassure"><span class="i">i</span><span>Accepted will be short at first. That is on purpose: lit2db would rather hold a value back for you than let a wrong one in.</span></div>`;
}

function aSheets() {
  if (!S.exported) {
    S.exported = true;
    api("/api/export", { p: S.pid }).then(async () => { await refresh(); render(); }).catch(e => showError(e.message));
  }
  $("#remake").onclick = async () => {
    try { await api("/api/export", { p: S.pid }); await refresh(); render(); } catch (e) { showError(e.message); }
  };
}

// ---------- wiring ----------
document.addEventListener("click", e => {
  const g = e.target.closest("[data-go]");
  if (g && !g.disabled) {
    e.preventDefault();
    if (g.dataset.go === "home") { location.hash = "#/"; return; }
    return go(g.dataset.go);
  }
  const o = e.target.closest("[data-open]");
  if (o && o.classList.contains("proj")) { S.pid = o.dataset.open; return go(""); }
  if (o && S.pid) return api("/api/reveal", { p: S.pid, which: o.dataset.open }).catch(x => showError(x.message));
  const sz = e.target.closest(".size button");
  if (sz) return setSize(sz.dataset.size);
  if (S.view === "collect" && main().contains(e.target)) onCollectClick(e);
});
document.addEventListener("change", e => { if (S.view === "collect") onCollectChange(e); });
document.addEventListener("input", e => { if (S.view === "collect") onCollectInput(e); });
$("#help").onclick = () => { $("#helpbox").hidden = false; };
$("#helpclose").onclick = () => { $("#helpbox").hidden = true; };
$("#helpbox").onclick = e => { if (e.target.id === "helpbox") $("#helpbox").hidden = true; };
window.addEventListener("hashchange", route);

let size = "small";
try { size = localStorage.getItem("lit2db.size") || "small"; } catch (e) { /* fine */ }
setSize(size);
route();
