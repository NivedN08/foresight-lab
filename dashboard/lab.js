// Lab view: the research lab itself, in two parts.
//
// One run: how the current best forecaster's loss moved over generations, every
// change it tested, its source, and what the run cost. Reads /api/lab/runs,
// /api/lab/runs/<problem>/<run_id> and, for experiment runs, /api/exp/<matrix>/<arm>/<seed>.
//
// Experiments: one matrix of runs at a time, drawn from its report.json only
// (/api/exp and /api/exp/<matrix>); per-seed dots are the runs' own final losses.
//
// Colours: ink for measured losses, blue for baselines and reference lines,
// --miss for rejected changes and partial results. Magenta stays reserved for sealed forecasts.

import { fmtWhen, fmtInt, fmtAge, esc, showTip, hideTip, capital } from "./seal.js";

const d3 = window.d3;
const START_CMD = ".venv/bin/python -m lab.swarm.run --problem coast_water --generations 5 --proposals 4";
const STATUS_WORD = {
  running: "running", finished: "finished", stopped_cost_cap: "stopped at its cost cap",
  failed: "failed", unreadable: "unreadable", starting: "starting", queued: "queued",
  crashed: "crashed", skipped: "skipped", blocked: "waiting for another run",
};
const ROLE_WORD = { analyst: "analyst", closed_book: "closed book", scout: "scout", judge: "judge",
  engineer: "engineer", playbook: "playbook", single_agent: "single agent", researcher: "researcher",
  anticipate: "anticipate", scout_queries: "scout queries", scout_board: "scout board", board_judge: "board judge" };
const QUESTION = {
  claim1_ladder_coast: "Does the lab beat a single agent and random changes, and what do the notebook and search add?",
  claim4_memory_coast: "Does a shared notebook help, and which kind?",
  claim3_search_coast: "Does search help, and which search?",
  claim2_transfer_turb_v2: "Is a lab that already worked on the coast faster on turbulence?",
  claim1_coast: "First design: three-role pipeline against a single agent",
};
const ORIGIN_WORD = { own: "own idea", notebook: "from the notebook", search: "from search", unstated: "not stated",
  closed_book: "closed book", single_agent: "single agent", random_mutation: "random change" };
const FINISHED = ["finished", "stopped_cost_cap"];

const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const NA = "n/a";
const fmtLoss = (v) => (num(v) == null ? NA : v.toFixed(2));
const fmt3 = (v) => (num(v) == null ? NA : v.toFixed(3));
const fmtDelta = (v) => (num(v) == null ? NA : `${v < 0 ? "−" : "+"}${Math.abs(v).toFixed(3)}`);
const fmtUsd = (v) => (num(v) == null ? NA : `$${v < 10 ? v.toFixed(2) : v.toFixed(1)}`);
const pct = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : NA);
const statusWord = (s) => STATUS_WORD[s] || (s ? String(s).replace(/_/g, " ") : "unknown");
const isFinished = (s) => FINISHED.includes(s);
const mr = (d, f = fmt3) => (!d || num(d.mean) == null ? NA
  : d.n > 1 && d.min !== d.max ? `${f(d.mean)} (${f(d.min)} to ${f(d.max)})` : f(d.mean));

// Loss name and unit, only if the run says so ("absolute error, cm").
function lossName(detail) {
  const c = (detail && detail.config) || {};
  const s = (detail && detail.summary) || {};
  for (const v of [s.loss_name, c.loss_name, c.loss_unit, c.unit, s.loss_unit, s.unit, c.loss, s.loss]) {
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return null;
}
const shortUnit = (u) => (u && u.includes(",") ? u.split(",").pop().trim() : u && u.split(" ").length === 1 ? u : "");

// Outcome of one finding, from its own fields only.
function outcome(f) {
  const dup = f.duplicate_of ? " (duplicate)" : "";
  if (f.accepted === true) {
    if (f.became_incumbent === true) return { key: "adopted", text: "adopted" + dup };
    if (f.became_incumbent === false) return { key: "passed", text: "passed, not adopted" + dup };
    return { key: "passed-na", text: `passed; adoption ${NA}${dup}` };
  }
  if (f.accepted === false) return { key: "rejected", text: "rejected" + dup };
  return { key: "na", text: NA + dup };
}

function originWords(f) {
  if (f.origin === "search") {
    if (f.known_closed_book === true) return "from search, already known to the model";
    return "from search";
  }
  return ORIGIN_WORD[f.origin] || (f.origin ? String(f.origin).replace(/_/g, " ") : NA);
}

// Reference lines: the starting formula from baseline_incumbent, plus any other baselines the summary carries.
function baselines(summary) {
  const out = [];
  const bi = summary && summary.baseline_incumbent;
  if (bi) {
    if (num(bi.select) != null) out.push({ name: "starting formula", value: bi.select, window: "select" });
    if (num(bi.confirm) != null) out.push({ name: "starting formula", value: bi.confirm, window: "confirm" });
  }
  const b = summary && summary.baselines;
  if (b) {
    const rows = Array.isArray(b) ? b : Object.entries(b).map(([name, v]) => (typeof v === "number" ? { name, value: v } : { name, ...v }));
    for (const r of rows) {
      const name = r.name || r.model || "baseline";
      if (num(r.value) != null) out.push({ name, value: r.value, window: null });
      if (num(r.select) != null) out.push({ name, value: r.select, window: "select" });
      if (num(r.confirm) != null) out.push({ name, value: r.confirm, window: "confirm" });
    }
  }
  return out;
}

// The three header lines a candidate file starts with.
function headerLines(src) {
  const out = { change: null, source: null, why: null };
  for (const line of String(src || "").split("\n")) {
    const t = line.trim();
    if (!t) continue;
    if (!t.startsWith("#")) break;                 // only leading comments count
    const m = t.match(/^#\s*(Change|Idea source|Why):\s*(.*)$/i);
    if (m) out[{ change: "change", "idea source": "source", why: "why" }[m[1].toLowerCase()]] = m[2];
  }
  return out;
}

// Keys: "<problem>/<run_id>" for runs/lab, "exp/<matrix>/<arm>/<seed>" for experiment runs.
const runKey = (r) => (r.source === "exp" ? `exp/${r.matrix}/${r.arm}/${r.seed}` : `${r.problem}/${r.run_id}`);
function detailUrl(key) {
  const p = key.split("/").map(encodeURIComponent);
  return key.startsWith("exp/") ? `/api/exp/${p[1]}/${p[2]}/${p[3]}` : `/api/lab/runs/${p[0]}/${p[1]}`;
}
const detailKey = (d) => (d.source === "exp" ? `exp/${d.matrix}/${d.arm}/${d.seed}` : `${d.problem}/${d.run_id}`);

export function create(root, api) {
  const ui = { mode: "run", key: null, chosen: false, show: "all", origin: "all", open: new Set(), matrix: null, matrixChosen: false };
  let runs = null; // null until the first answer
  let detail = null;
  let failed = false;
  let active = false;
  let lastFindingsKey = "";
  let matrices = null;
  let mdetail = null;

  root.classList.add("lab-view");
  root.innerHTML = `
    <div class="lab-main run-only">
      <section class="lab-curve">
        <div class="panel-head">
          <h2>Loss of the current best forecaster <span class="quiet">by generation</span></h2>
          <div class="legend curve-legend"></div>
        </div>
        <div class="chart-wrap"><svg class="chart curve"></svg><div class="chart-note" hidden></div></div>
      </section>
      <section class="lab-ledger">
        <div class="panel-head">
          <h2>Claims ledger <span class="quiet">every change tested, newest first</span></h2>
          <div class="controls">
            <div class="seg show-seg" role="group" aria-label="Outcome">
              <button data-show="all">All</button><button data-show="passed">Passed</button><button data-show="rejected">Rejected</button>
            </div>
            <div class="seg origin-seg" role="group" aria-label="Origin">
              <button data-origin="all">Any origin</button><button data-origin="own">Own</button><button data-origin="notebook">Notebook</button><button data-origin="search">Search</button>
            </div>
          </div>
        </div>
        <div class="ledger-counts"></div>
        <div class="ledger-scroll"><table class="ledger"></table></div>
      </section>
    </div>
    <div class="lab-main exp-main exp-only" hidden>
      <section class="exp-chart-block">
        <div class="panel-head">
          <h2>Final loss on the confirmation window <span class="quiet">by arm, one dot per seed; lower is better</span></h2>
        </div>
        <div class="exp-chart-wrap"><svg class="chart exp-chart"></svg><div class="chart-note" hidden></div></div>
        <div class="legend exp-legend"></div>
      </section>
      <section class="exp-tables"></section>
    </div>
    <aside class="side">
      <div class="lab-mode seg" role="tablist" aria-label="Lab view">
        <button role="tab" data-mode="run">One run</button><button role="tab" data-mode="exp">Experiments</button>
      </div>
      <section class="lead-block run-only">
        <p class="sentence">Reading the lab runs…</p>
        <p class="sub lab-note"></p>
      </section>
      <section class="runs-block run-only">
        <div class="panel-head"><h2>Runs <span class="quiet">newest first</span></h2></div>
        <div class="run-list" role="listbox" aria-label="Lab runs"></div>
      </section>
      <section class="source-block run-only">
        <details class="source">
          <summary><span class="source-title">Current best forecaster</span> <span class="quiet source-meta"></span></summary>
          <div class="header-lines in-open"></div>
          <div class="code-wrap"><pre class="code"></pre></div>
        </details>
        <div class="header-lines change-closed"></div>
      </section>
      <section class="lead-block exp-only" hidden>
        <p class="sentence exp-question">Reading the experiments…</p>
        <p class="exp-status"></p>
        <p class="sub exp-note"></p>
      </section>
      <section class="exp-pick-block exp-only" hidden>
        <div class="panel-head"><h2>Experiment matrices</h2></div>
        <div class="matrix-list run-list" role="listbox" aria-label="Experiment matrices"></div>
      </section>
      <section class="exp-side-tables exp-only" hidden></section>
    </aside>
    <div class="log lab-cost run-only">
      <div class="log-title"><strong>Cost and effort</strong><span class="cost-sub"></span></div>
      <div class="log-row cost-row"></div>
    </div>
    <div class="log exp-runs exp-only" hidden>
      <div class="log-title"><strong>Runs</strong><span>click one to open it</span></div>
      <div class="log-row exp-run-row"></div>
    </div>`;

  const $ = (s) => root.querySelector(s);

  // redraw charts whenever their boxes change size (window resize, layout switch)
  const boxes = {};
  const watch = (sel, fn) => new ResizeObserver(() => {
    const r = $(sel).getBoundingClientRect();
    const box = `${Math.round(r.width)}x${Math.round(r.height)}`;
    if (active && r.width && box !== boxes[sel]) { boxes[sel] = box; fn(); }
  }).observe($(sel));
  watch(".lab-curve .chart-wrap", () => renderCurve());
  watch(".exp-chart-wrap", () => renderExpChart());

  $(".lab-mode").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.dataset.mode === ui.mode) return;
    setMode(b.dataset.mode, true);
  });
  $(".show-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    ui.show = b.dataset.show;
    renderLedger(true);
  });
  $(".origin-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    ui.origin = b.dataset.origin;
    renderLedger(true);
  });
  root.querySelector(".runs-block .run-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-run]");
    if (b) selectRun(b.dataset.run, true);
  });
  $(".matrix-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-matrix]");
    if (b) selectMatrix(b.dataset.matrix, true);
  });
  $(".exp-tables").addEventListener("click", (e) => {
    const b = e.target.closest("[data-run]");
    if (b) openRun(b.dataset.run);
  });
  $(".exp-run-row").addEventListener("click", (e) => {
    const b = e.target.closest("[data-run]");
    if (b) openRun(b.dataset.run);
  });
  $("table.ledger").addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    const id = tr.dataset.id;
    if (ui.open.has(id)) ui.open.delete(id); else ui.open.add(id);
    tr.classList.toggle("open", ui.open.has(id));
  });
  $("table.ledger").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const tr = e.target.closest("tr[data-id]");
    if (!tr || e.target.closest("a")) return;
    e.preventDefault();
    tr.click();
  });

  // ------------------------------------------------------------ mode and routing

  function hashParts() {
    if (ui.mode === "exp") return ["experiments", ...(ui.matrixChosen && ui.matrix ? [ui.matrix] : [])];
    return ui.chosen && ui.key ? ui.key.split("/") : [];
  }

  function setMode(mode, byUser) {
    ui.mode = mode;
    root.querySelectorAll(".run-only").forEach((el) => { el.hidden = mode !== "run"; });
    root.querySelectorAll(".exp-only").forEach((el) => { el.hidden = mode !== "exp"; });
    $(".lab-mode").querySelectorAll("button").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.mode === mode));
      b.setAttribute("aria-pressed", String(b.dataset.mode === mode));
    });
    hideTip();
    if (byUser) {
      api.setHash(hashParts());
      if (mode === "exp") pollExp().then(() => { if (active) renderExp(); });
      else poll();
    }
    render();
  }

  // Open one experiment run in the run part of this view.
  function openRun(key) {
    setMode("run", false);
    selectRun(key, true);
  }

  // ------------------------------------------------------------ run data

  function pickDefault() {
    if (!runs || !runs.length) return null;
    return [...runs].sort((a, b) => String(b.updated_at || b.started_at || "").localeCompare(String(a.updated_at || a.started_at || "")))[0];
  }

  const selectedRow = () => (runs || []).find((r) => runKey(r) === ui.key) || null;

  async function selectRun(key, byUser) {
    if (key === ui.key && detail && !byUser) return;
    if (key !== ui.key) { ui.open.clear(); detail = null; lastFindingsKey = ""; }
    ui.key = key;
    if (byUser) ui.chosen = true;
    api.setHash(hashParts());
    render();
    await fetchDetail();
    render();
  }

  async function fetchDetail() {
    if (!ui.key) return;
    const want = ui.key;
    const d = await api.getJSON(detailUrl(want));
    if (d && ui.key === want && (detailKey(d) === want || d.run_id === want.split("/")[1])) detail = d;
  }

  async function pollRuns() {
    const r = await api.getJSON("/api/lab/runs");
    if (r && Array.isArray(r.runs)) { runs = r.runs; failed = false; } else { failed = true; }
    if (runs && !runs.length) { ui.key = null; detail = null; lastFindingsKey = ""; }
    if (runs && runs.length) {
      // a run named in the URL that the list does not have yet stays selected
      if (!ui.key || (!selectedRow() && !ui.chosen)) {
        const d = pickDefault();
        if (runKey(d) !== ui.key) { ui.key = runKey(d); detail = null; lastFindingsKey = ""; }
        ui.chosen = false;
      }
      const row = selectedRow();
      if (!detail || detailKey(detail) !== ui.key || (row && row.status === "running") ||
          (detail.summary && row && detail.summary.updated_at !== row.updated_at)) {
        await fetchDetail();
      }
    }
  }

  // ------------------------------------------------------------ experiment data

  async function pollExp() {
    const r = await api.getJSON("/api/exp");
    if (r && Array.isArray(r.matrices)) matrices = r.matrices;
    else if (matrices === null) matrices = null;
    if (matrices && matrices.length) {
      if (!ui.matrix || !matrices.some((m) => m.name === ui.matrix)) {
        if (!ui.matrixChosen || !ui.matrix) ui.matrix = matrices[0].name;
      }
      const row = matrices.find((m) => m.name === ui.matrix);
      if (!mdetail || mdetail.name !== ui.matrix || (row && row.runs_running) ||
          (row && mdetail._updated !== row.updated_at)) {
        const d = await api.getJSON(`/api/exp/${encodeURIComponent(ui.matrix)}`);
        if (d && d.name === ui.matrix) { mdetail = d; mdetail._updated = row && row.updated_at; }
      }
    } else if (matrices && !matrices.length) mdetail = null;
  }

  async function selectMatrix(name, byUser) {
    if (byUser) ui.matrixChosen = true;
    if (name !== ui.matrix) mdetail = null;
    ui.matrix = name;
    api.setHash(hashParts());
    renderExp();
    const d = await api.getJSON(`/api/exp/${encodeURIComponent(name)}`);
    if (d && d.name === ui.matrix) {
      const row = (matrices || []).find((m) => m.name === name);
      mdetail = d; mdetail._updated = row && row.updated_at;
    }
    renderExp();
  }

  async function poll() {
    if (ui.mode === "exp") await Promise.all([pollExp(), runs === null ? pollRuns() : null]);
    else await pollRuns();
    if (active) render();
  }

  // ------------------------------------------------------------ render

  function render() {
    if (ui.mode === "exp") { renderExp(); return; }
    renderSentence();
    renderRuns();
    renderCurve();
    renderLedger(false);
    renderSource();
    renderCost();
  }

  // Counts for the run: summary.json fields when present, else counted from the rows.
  function runCounts() {
    const sm = (detail && detail.summary) || {};
    const rows = ((detail && detail.findings) || []).filter((x) => !x.supersedes); // old runs wrote a follow-up row per superseded change
    const anyAdoption = rows.some((f) => f.became_incumbent === true || f.became_incumbent === false);
    const tested = num(sm.findings_total) ?? rows.length;
    const passed = num(sm.accepted_total) ?? rows.filter((f) => f.accepted === true).length;
    const adopted = num(sm.became_incumbent_total) ?? (anyAdoption ? rows.filter((f) => f.became_incumbent === true).length : null);
    const fromSummary = num(sm.findings_total) != null && num(sm.accepted_total) != null;
    // origin of passed changes
    let passedSearch, passedNew, judged;
    const bo = sm.by_origin;
    if (bo && typeof bo === "object" && sm.board && num(sm.board.passed_not_known_closed_book) != null) {
      passedSearch = num((bo.search || {}).passed) ?? 0;
      passedNew = sm.board.passed_not_known_closed_book;
      judged = passedSearch;
    } else {
      const ps = rows.filter((f) => f.accepted === true && f.origin === "search");
      const j = ps.filter((f) => f.known_closed_book === true || f.known_closed_book === false);
      passedSearch = ps.length; judged = j.length; passedNew = j.filter((f) => f.known_closed_book === false).length;
    }
    const usedSearch = rows.some((f) => f.origin === "search") || !!(bo && bo.search);
    return { rows, tested, passed, adopted, passedSearch, passedNew, judged, fromSummary, usedSearch,
      duplicates: num(sm.duplicates_total), byOrigin: bo && typeof bo === "object" ? bo : null };
  }

  function renderSentence() {
    const s = $(".lab-note") && $(".lead-block.run-only .sentence");
    const note = $(".lab-note");
    if (runs === null) {
      s.textContent = failed ? "Cannot reach the lab server." : "Reading the lab runs…";
      note.textContent = failed ? "The dashboard retries every 30 seconds." : "";
      return;
    }
    if (!runs.length) {
      s.innerHTML = `No lab runs yet. Start one: <code>${esc(START_CMD)}</code>`;
      note.textContent = "";
      return;
    }
    if (!detail) { s.textContent = "Reading the run…"; note.textContent = ""; return; }
    const sm = detail.summary || {};
    const curve = Array.isArray(sm.curve) ? sm.curve : [];
    const unit = shortUnit(lossName(detail));
    const u = (v) => `<span class="num">${fmtLoss(v)}${unit ? ` ${esc(unit)}` : ""}</span>`;
    const what = unit ? "error" : "loss";
    const done = num(sm.generations_done) ?? (curve.length ? curve[curve.length - 1].generation : null);
    const first = curve[0], last = curve[curve.length - 1];
    let text;
    if (!first || done == null) {
      text = "This run has not finished a generation yet.";
    } else {
      const g = `In ${fmtInt(done)} generation${done === 1 ? "" : "s"}`;
      const a = num(first.confirm), b = num(last.confirm);
      if (a == null || b == null) text = `${g} the ${what} on the confirmation window is ${NA}.`;
      else if (b < a) text = `${g} the lab cut the ${what} on the confirmation window from ${u(a)} to ${u(b)}.`;
      else if (b > a) text = `${g} the ${what} on the confirmation window rose from ${u(a)} to ${u(b)}.`;
      else text = `${g} the ${what} on the confirmation window stayed at ${u(a)}.`;
      const c = runCounts();
      text += ` It tested <span class="num">${fmtInt(c.tested)}</span> change${c.tested === 1 ? "" : "s"}; <span class="num">${fmtInt(c.passed)}</span> passed`;
      text += c.adopted != null ? ` and <span class="num">${fmtInt(c.adopted)}</span> ${c.adopted === 1 ? "was" : "were"} adopted.` : ".";
    }
    const st = sm.status;
    if (st === "running" && num(sm.generations_planned) != null && done != null) {
      text += ` The run is still going: generation ${fmtInt(done + 1)} of ${fmtInt(sm.generations_planned)}.`;
    } else if (st === "stopped_cost_cap") text += " It stopped when it reached its cost cap.";
    else if (st === "failed") text += " The run failed.";
    s.innerHTML = text;
    const where = detail.source === "exp"
      ? `Experiment run: ${detail.matrix}, arm ${detail.arm}, seed ${detail.seed}. ` : "";
    note.textContent = where + "The confirmation window is later in time than the selection window. Agent-written code never receives its answers, " +
      "but the lab does see each change's score on it and refuses changes that do worse there, so this figure is somewhat optimistic. " +
      "Only the final test window, scored once at the end, is untouched." +
      (unit ? "" : " The run does not state the loss unit.");
  }

  function renderRuns() {
    const el = root.querySelector(".runs-block .run-list");
    if (!runs || !runs.length) {
      el.innerHTML = runs ? `<p class="empty">No runs under <code>runs/lab/</code> or <code>runs/exp/</code> yet.</p>` : "";
      return;
    }
    el.innerHTML = runs.map((r) => {
      const k = runKey(r);
      const on = k === ui.key;
      const gens = r.generations_done != null
        ? `${fmtInt(r.generations_done)}${r.generations_planned != null ? ` of ${fmtInt(r.generations_planned)}` : ""} generations`
        : `generations ${NA}`;
      const upd = r.updated_at ? `updated ${fmtAge(Date.now() - new Date(r.updated_at))}` : "";
      const what = r.source === "exp"
        ? `${r.matrix} · ${r.arm} · seed ${r.seed}`
        : `${r.problem} · ${r.mode || NA}`;
      const sub = r.source === "exp" ? `${r.problem || NA} · ${r.mode || NA} · ${gens}` : `${r.run_id} · ${gens}`;
      return `<button class="run${on ? " on" : ""}" role="option" aria-selected="${on}" data-run="${esc(k)}">
        <span class="run-status st-${esc(r.status || "")}">${esc(capital(statusWord(r.status)))}</span>
        <span class="run-what">${esc(what)}</span>
        <span class="run-id">${esc(sub)}${upd ? ` · ${esc(upd)}` : ""}</span></button>`;
    }).join("");
  }

  // ------------------------------------------------------------ curve

  function adoptedIn(gen) {
    const f = ((detail && detail.findings) || []).filter((x) => !x.supersedes);
    const same = f.filter((x) => x.generation === gen);
    const adopted = same.filter((x) => x.became_incumbent === true);
    if (adopted.length) return { list: adopted, known: true };
    return { list: same.filter((x) => x.accepted === true), known: false };
  }

  function renderCurve() {
    if (ui.mode !== "run") return;
    const svgEl = $("svg.curve");
    const note = $(".lab-curve .chart-note");
    const wrap = svgEl.parentNode;
    const width = Math.max(300, Math.floor(wrap.getBoundingClientRect().width));
    const height = Math.max(150, Math.floor(wrap.getBoundingClientRect().height) || 220);
    const s = d3.select(svgEl).attr("viewBox", `0 0 ${width} ${height}`).attr("height", height);
    const sm = (detail && detail.summary) || {};
    const curve = (Array.isArray(sm.curve) ? sm.curve : []).filter((c) => num(c.generation) != null);
    const unit = shortUnit(lossName(detail));
    const base = baselines(sm);
    $(".curve-legend").innerHTML = `
      <span class="k"><span class="sw-line"></span>Select window</span>
      <span class="k"><span class="sw-line confirm"></span>Confirmation window (later in time)</span>
      <span class="k"><svg width="12" height="12" viewBox="-6 -6 12 12"><circle r="4" fill="#16283A"/></svg>A change was adopted</span>
      ${base.length ? `<span class="k"><span class="sw-line tide"></span>Starting formula</span>` : ""}`;
    if (!curve.length) {
      s.selectAll("*").remove();
      note.hidden = false;
      note.textContent = !runs || !runs.length ? "" : detail ? "No generation finished yet." : "Reading the run…";
      return;
    }
    note.hidden = true;
    const m = { l: 44, r: 128, t: 24, b: 26 };
    const planned = Math.max(num(sm.generations_planned) || 0, curve[curve.length - 1].generation);
    const x = d3.scaleLinear().domain([0, Math.max(1, planned)]).range([m.l, width - m.r]);
    const vals = curve.flatMap((c) => [c.select, c.confirm]).concat(base.map((b) => b.value)).filter((v) => num(v) != null);
    const ext = d3.extent(vals);
    const pad = Math.max(0.02, (ext[1] - ext[0]) * 0.12);
    const y = d3.scaleLinear().domain([ext[0] - pad, ext[1] + pad]).nice(5).range([height - m.b, m.t]);

    if (s.select("g.frame").empty()) {
      s.append("g").attr("class", "frame");
      s.append("g").attr("class", "base-lines");
      s.append("g").attr("class", "lines");
      s.append("g").attr("class", "marks");
      s.append("g").attr("class", "ends");
    }
    const fr = s.select("g.frame");
    fr.selectAll("*").remove();
    const yt = y.ticks(5);
    fr.selectAll("line.gy").data(yt).join("line").attr("class", "gridline gy")
      .attr("x1", m.l).attr("x2", width - m.r).attr("y1", (d) => y(d)).attr("y2", (d) => y(d));
    fr.selectAll("text.yl").data(yt).join("text").attr("class", "tick-label yl").attr("text-anchor", "end")
      .attr("x", m.l - 6).attr("y", (d) => y(d) + 4).text((d) => d3.format(".2f")(d));
    const xt = x.ticks(Math.min(planned, Math.floor((width - m.l - m.r) / 40))).filter(Number.isInteger);
    fr.selectAll("text.xl").data(xt).join("text").attr("class", "tick-label xl").attr("text-anchor", "middle")
      .attr("x", (d) => x(d)).attr("y", height - 8).text((d) => d);
    fr.append("text").attr("class", "tick-label").attr("x", m.l - 6).attr("y", 9).attr("text-anchor", "end")
      .text(unit || "loss");
    fr.append("text").attr("class", "tick-label").attr("x", width - m.r).attr("y", height - 8).attr("text-anchor", "start")
      .attr("dx", 20).text("generation");
    if (planned > curve[curve.length - 1].generation) {
      const xl = x(curve[curve.length - 1].generation);
      fr.append("rect").attr("x", xl).attr("y", m.t).attr("width", x(planned) - xl).attr("height", height - m.t - m.b)
        .attr("fill", "rgba(213,233,241,0.35)");
      fr.append("text").attr("class", "tick-label").attr("x", (xl + x(planned)) / 2).attr("y", m.t + 12)
        .attr("text-anchor", "middle").text(sm.status === "running" ? "still to run" : "not run");
    }

    // end labels with the latest values
    const last = curve[curve.length - 1];
    const ends = [
      { k: "select", v: last.select, label: "select" },
      { k: "confirm", v: last.confirm, label: "confirm" },
    ].filter((e) => num(e.v) != null);
    if (ends.length === 2 && Math.abs(y(ends[0].v) - y(ends[1].v)) < 14) {
      const mid = (y(ends[0].v) + y(ends[1].v)) / 2;
      const up = ends[0].v > ends[1].v ? 0 : 1;
      ends[up].yy = mid - 8; ends[1 - up].yy = mid + 8;
    }
    const endX = x(last.generation) + 6;
    // reference labels sit in the right margin; drop one that would collide with an end label
    const labelY = (b) => y(b.value) + 4;
    const clash = (b) => endX >= width - m.r - 4 && ends.some((e) => Math.abs((e.yy ?? y(e.v)) + 4 - labelY(b)) < 13);
    s.select("g.base-lines").selectAll("g.bl").data(base, (b) => `${b.name}|${b.window}`).join((en) => {
      const g = en.append("g").attr("class", "bl");
      g.append("line").attr("class", "line-ref");
      g.append("text").attr("class", "tick-label ref-label");
      return g;
    }).each(function (b) {
      const g = d3.select(this);
      g.select("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y(b.value)).attr("y2", y(b.value))
        .attr("stroke-dasharray", b.window === "confirm" ? "5 3" : null);
      g.select("text").attr("x", width - m.r + 6).attr("y", labelY(b))
        .text(clash(b) ? "" : `${b.name}${b.window && b.name !== "starting formula" ? `, ${b.window}` : ""}`);
    });

    const step = (key) => d3.line().defined((c) => num(c[key]) != null).x((c) => x(c.generation)).y((c) => y(c[key])).curve(d3.curveStepAfter);
    s.select("g.lines").selectAll("path.sel").data([curve]).join("path").attr("class", "sel lab-line").attr("d", step("select"));
    s.select("g.lines").selectAll("path.con").data([curve]).join("path").attr("class", "con lab-line confirm").attr("d", step("confirm"));
    s.select("g.ends").selectAll("text").data(ends, (e) => e.k).join("text").attr("class", "end-label")
      .attr("x", endX).attr("y", (e) => (e.yy ?? y(e.v)) + 4)
      .text((e) => `${e.label} ${fmtLoss(e.v)}`);

    // a mark where the incumbent changed
    const marks = curve.filter((c, i) => i > 0 && c.incumbent_hash && curve[i - 1].incumbent_hash && c.incumbent_hash !== curve[i - 1].incumbent_hash);
    s.select("g.marks").selectAll("circle").data(marks, (c) => c.generation).join("circle")
      .attr("class", "kept-mark").attr("r", 4.5).attr("tabindex", 0)
      .attr("cx", (c) => x(c.generation)).attr("cy", (c) => y(c.select))
      .on("mousemove focus", (e, c) => showTip(markTip(c), e.type === "focus" ? focusPoint(e.target) : e))
      .on("mouseleave blur", hideTip);
  }

  function focusPoint(el) {
    const r = el.getBoundingClientRect();
    return { clientX: r.right, clientY: r.bottom };
  }

  function markTip(c) {
    const { list, known } = adoptedIn(c.generation);
    let h = `<b>Generation ${c.generation}</b>: select ${fmtLoss(c.select)}, confirm ${fmtLoss(c.confirm)}<br>`;
    if (!list.length) return h + `<span class="q">No finding for this generation says which change was adopted.</span>`;
    if (!known) h += `<span class="q">The run does not record which change was adopted; ${list.length === 1 ? "this one passed" : `these ${list.length} passed`}:</span><br>`;
    h += list.slice(0, 4).map((f) => `• ${esc(shortClaim(f.claim, 150))}<br><span class="q">${f.source_title ? esc(f.source_title) : "no source"} · ${esc(originWords(f))}</span>`).join("<br>");
    if (list.length > 4) h += `<br><span class="q">and ${list.length - 4} more</span>`;
    return h;
  }

  // ------------------------------------------------------------ ledger

  function shortClaim(s, n) {
    s = String(s || "");
    return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s;
  }

  function intervalSVG(f, D, rejected) {
    const W = 120, H = 16, mid = W / 2;
    const sx = (v) => mid + (Math.max(-D, Math.min(D, v)) / D) * (mid - 6);
    const lo = num(f.select_low), hi = num(f.select_high), dv = num(f.select_delta);
    let g = `<line x1="${mid}" x2="${mid}" y1="2" y2="${H - 2}" class="iv-zero"/>`;
    if (lo != null && hi != null) {
      g += `<line x1="${sx(lo)}" x2="${sx(hi)}" y1="${H / 2}" y2="${H / 2}" class="iv-bar"/>`;
      g += `<line x1="${sx(lo)}" x2="${sx(lo)}" y1="${H / 2 - 3}" y2="${H / 2 + 3}" class="iv-bar"/>`;
      g += `<line x1="${sx(hi)}" x2="${sx(hi)}" y1="${H / 2 - 3}" y2="${H / 2 + 3}" class="iv-bar"/>`;
      if (lo < -D) g += `<path d="M2,${H / 2}l5,-3v6z" class="iv-dot"/>`;
      if (hi > D) g += `<path d="M${W - 2},${H / 2}l-5,-3v6z" class="iv-dot"/>`;
    }
    if (dv != null) g += `<circle cx="${sx(dv)}" cy="${H / 2}" r="2.6" class="iv-dot"/>`;
    return `<svg class="iv${rejected ? " rej" : ""}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${g}</svg>`;
  }

  function filtered(all) {
    return all.filter((f) => {
      if (ui.show === "passed" && f.accepted !== true) return false;
      if (ui.show === "rejected" && f.accepted !== false) return false;
      if (ui.origin !== "all" && f.origin !== ui.origin) return false;
      return true;
    });
  }

  function renderLedger(force) {
    if (ui.mode !== "run") return;
    $(".show-seg").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.show === ui.show)));
    $(".origin-seg").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.origin === ui.origin)));
    const table = $("table.ledger");
    const counts = $(".ledger-counts");
    const all = ((detail && detail.findings) || []).filter((x) => !x.supersedes);
    const sm = (detail && detail.summary) || {};
    const key = `${ui.key}|${all.length}|${ui.show}|${ui.origin}|${all.length ? all[all.length - 1].id : ""}|${sm.updated_at || ""}`;
    if (!force && key === lastFindingsKey) return;
    lastFindingsKey = key;

    if (!detail) {
      counts.textContent = "";
      table.innerHTML = runs && !runs.length ? "" : `<tbody><tr><td class="empty">Reading the run…</td></tr></tbody>`;
      return;
    }
    // counts over the whole run, not the filtered view
    const c = runCounts();
    const originBits = c.byOrigin
      ? Object.entries(c.byOrigin).filter(([, v]) => v && num(v.proposals))
        .map(([o, v]) => `${ORIGIN_WORD[o] || o.replace(/_/g, " ")} ${fmtInt(v.proposals)}`).join(" · ")
      : "";
    counts.innerHTML = `
      <span><b>${fmtInt(c.tested)}</b> tested</span>
      <span><b>${fmtInt(c.passed)}</b> passed</span>
      <span><b>${c.adopted == null ? NA : fmtInt(c.adopted)}</b> adopted</span>
      ${c.duplicates ? `<span><b>${fmtInt(c.duplicates)}</b> duplicates</span>` : ""}
      ${c.usedSearch ? `<span><b>${pct(c.passedSearch, c.passed)}</b> of passed changes came from search</span>` : ""}
      ${c.passedSearch ? `<span><b>${pct(c.passedNew, c.judged)}</b> of those were not already known to the model</span>` : ""}
      ${originBits ? `<span class="quiet">Proposals by origin: ${esc(originBits)}</span>` : ""}
      ${c.adopted == null ? `<span class="quiet">This run does not record which passed change was adopted.</span>` : ""}
      ${c.fromSummary ? "" : `<span class="quiet">Counted from the rows below; this run's summary has no totals.</span>`}`;

    const rows = filtered(all).slice().sort((a, b) => (b.generation ?? -1) - (a.generation ?? -1) || String(b.time || "").localeCompare(String(a.time || "")));
    const spans = all.flatMap((f) => [Math.abs(num(f.select_low) ?? 0), Math.abs(num(f.select_high) ?? 0), Math.abs(num(f.select_delta) ?? 0)]).filter((v) => v > 0).sort((a, b) => a - b);
    const D = spans.length ? Math.max(1e-4, d3.quantile(spans, 0.9)) : 1;

    const scroller = $(".ledger-scroll");
    const before = scroller.scrollHeight, top = scroller.scrollTop;
    const head = `<thead><tr>
      <th class="c-gen">Gen.</th><th class="c-claim">Claim and source</th>
      <th class="c-sel">Select window <span class="quiet">← better</span></th>
      <th class="c-con">Confirm</th><th class="c-out">Outcome</th></tr></thead>`;
    if (!rows.length) {
      table.innerHTML = head + `<tbody><tr><td colspan="5" class="empty">${all.length ? "No changes match these filters." : "No change has been tested yet."}</td></tr></tbody>`;
      return;
    }
    table.innerHTML = head + "<tbody>" + rows.map((f) => {
      const o = outcome(f);
      const rej = o.key === "rejected";
      // only plain web links become clickable
      const src = /^https?:\/\//i.test(f.source_url || "")
        ? `<a href="${esc(f.source_url)}" target="_blank" rel="noopener noreferrer">${esc(f.source_title || f.source_url)}</a>`
        : f.source_title ? esc(f.source_title) : "";
      const extra = [originWords(f)];
      if (f.idea_ref) extra.push(`idea ${f.idea_ref}`);
      if (f.source_relevance) extra.push(`${f.source_relevance} source`);
      if (f.search_backend) extra.push(`via ${f.search_backend}`);
      return `<tr data-id="${esc(f.id || "")}" tabindex="0" class="${rej ? "rej" : "kept"}${ui.open.has(f.id) ? " open" : ""}">
        <td class="c-gen">${f.generation ?? NA}</td>
        <td class="c-claim"><div class="claim">${esc(f.claim || NA)}</div>
          <div class="src">${src ? `${src} · ` : ""}<span class="origin">${esc(extra.join(" · "))}</span></div>
          <div class="full">${esc(f.text || "")}</div></td>
        <td class="c-sel">${intervalSVG(f, D, rej)}<span class="dv">${fmtDelta(f.select_delta)}</span>
          <span class="ci">${num(f.select_low) != null && num(f.select_high) != null ? `${fmtDelta(f.select_low)} to ${fmtDelta(f.select_high)}` : `interval ${NA}`}</span></td>
        <td class="c-con"><span class="dv">${fmtDelta(f.confirm_delta)}</span></td>
        <td class="c-out out-${o.key}">${esc(o.text)}</td></tr>`;
    }).join("") + "</tbody>";
    // keep the reader's place when new rows arrive at the top
    if (top > 0) scroller.scrollTop = top + (scroller.scrollHeight - before);
  }

  // ------------------------------------------------------------ source

  function renderSource() {
    const det = $("details.source");
    const meta = $(".source-meta");
    const code = $("pre.code");
    const heads = [...root.querySelectorAll(".header-lines")];
    if (!detail || detail.incumbent_source == null) {
      meta.textContent = detail ? "(no incumbent.py in this run)" : "";
      code.textContent = "";
      heads.forEach((el) => { el.textContent = ""; });
      det.hidden = !detail;
      return;
    }
    det.hidden = false;
    const src = String(detail.incumbent_source);
    const all = src.split("\n");
    const hash = detail.summary && detail.summary.incumbent_hash;
    meta.textContent = `${all.length} lines${hash ? ` · ${hash}` : ""}`;
    const h = headerLines(src);
    const line = (label, v) => `<p class="change-line"><span class="quiet">${label}:</span> ${v == null ? `${NA}` : esc(v)}</p>`;
    const html = h.change == null && h.source == null && h.why == null
      ? `<p class="change-line"><span class="quiet">Header:</span> ${NA} (the file does not start with <code># Change:</code>, <code># Idea source:</code> and <code># Why:</code> lines)</p>`
      : line("Change", h.change) + line("Idea source", h.source) + line("Why", h.why);
    heads.forEach((el) => { if (el.innerHTML !== html) el.innerHTML = html; });
    if (code.dataset.hash !== String(hash) || code.dataset.len !== String(src.length)) {
      const w = String(all.length).length;
      code.innerHTML = all.map((l, i) => `<span class="ln">${String(i + 1).padStart(w, " ")}</span>${esc(l)}`).join("\n");
      code.dataset.hash = String(hash);
      code.dataset.len = String(src.length);
    }
  }

  // ------------------------------------------------------------ cost

  function renderCost() {
    const row = $(".cost-row");
    const sub = $(".cost-sub");
    if (!detail || !detail.summary) {
      row.innerHTML = runs && !runs.length
        ? `<div class="log-empty">No lab runs yet. Start one: <code>${esc(START_CMD)}</code></div>`
        : `<div class="log-empty">${runs ? "Reading the run…" : ""}</div>`;
      sub.textContent = "";
      return;
    }
    const sm = detail.summary, cf = detail.config || {};
    const roles = sm.llm_by_role_all || sm.llm_by_role || null;
    sub.textContent = sm.llm_by_role_all ? "whole run" : "this run";
    const items = [];
    const cap = num(cf.max_cost_usd);
    items.push([num(sm.cost_usd) != null ? `$${sm.cost_usd.toFixed(2)}` : NA, cap != null ? `spent of a $${cap.toFixed(0)} cap` : "spent"]);
    items.push([num(sm.evaluations) != null ? fmtInt(sm.evaluations) : NA, "evaluations"]);
    items.push([num(sm.wall_s) != null ? fmtInt(Math.round(sm.wall_s / 60)) : NA, "minutes of run time"]);
    if (roles && typeof roles === "object") {
      const calls = Object.entries(roles).filter(([, v]) => v && num(v.calls) != null);
      const total = d3.sum(calls, ([, v]) => v.calls);
      items.push([fmtInt(total), "model calls"]);
      for (const [role, v] of calls) {
        items.push([fmtInt(v.calls), `${ROLE_WORD[role] || role.replace(/_/g, " ")}${cf.models && cf.models[role] ? ` (${cf.models[role]})` : ""}${num(v.cost_usd) != null ? ` · $${v.cost_usd.toFixed(2)}` : ""}`]);
      }
    }
    if (sm.search && num(sm.search.searches) != null) items.push([fmtInt(sm.search.searches), `searches${sm.search.backend ? ` (${sm.search.backend})` : ""}`]);
    row.innerHTML = items.map(([v, l]) => `<div class="stat"><div class="stat-v">${esc(v)}</div><div class="stat-l">${esc(l)}</div></div>`).join("");
  }

  // ------------------------------------------------------------ experiments

  const report = () => (mdetail && mdetail.report) || null;
  // a run marked running whose summary has not changed for 45 minutes has probably stopped
  const STALE_MS = 45 * 60e3;
  const staleRun = (r) => r.status === "running" && r.updated_at && Date.now() - new Date(r.updated_at) > STALE_MS;
  const expRuns = () => (mdetail && mdetail.runs) || [];
  const armOrder = () => {
    const cfg = (mdetail && mdetail.manifest && mdetail.manifest.config) || {};
    return (cfg.arms || []).map((a) => a.name);
  };
  const armComment = (arm) => {
    const cfg = (mdetail && mdetail.manifest && mdetail.manifest.config) || {};
    const a = (cfg.arms || []).find((x) => x.name === arm);
    return a && a.comment ? a.comment : "";
  };

  function renderExp() {
    renderMatrixList();
    renderExpHead();
    renderExpChart();
    renderExpTables();
    renderExpRuns();
  }

  function renderMatrixList() {
    const el = $(".matrix-list");
    if (matrices === null) { el.innerHTML = `<p class="empty">Reading the experiments…</p>`; return; }
    if (!matrices.length) {
      el.innerHTML = `<p class="empty">No experiment under <code>runs/exp/</code> yet. Start one: <code>.venv/bin/python -m lab.experiments.matrix run lab/experiments/configs/claim1_ladder_coast.json</code></p>`;
      return;
    }
    el.innerHTML = matrices.map((m) => {
      const on = m.name === ui.matrix;
      const partial = m.runs_finished < m.runs_planned;
      const live = (m.runs_running || 0) - (m.runs_silent || 0);
      const st = live ? `${live} running` : m.runs_silent ? `${m.runs_silent} silent` : partial ? "unfinished" : "finished";
      return `<button class="run${on ? " on" : ""}" role="option" aria-selected="${on}" data-matrix="${esc(m.name)}">
        <span class="run-status ${partial ? "st-partial" : "st-finished"}">${esc(capital(st))}</span>
        <span class="run-what">${esc(QUESTION[m.name] || m.name)}</span>
        <span class="run-id">${esc(m.name)} · ${esc(m.problem || NA)} · ${fmtInt(m.runs_finished)} of ${fmtInt(m.runs_planned)} runs finished${m.has_report ? "" : " · no report yet"}</span></button>`;
    }).join("");
  }

  function renderExpHead() {
    const q = $(".exp-question");
    const st = $(".exp-status");
    const note = $(".exp-note");
    if (matrices === null) { q.textContent = "Reading the experiments…"; st.textContent = ""; note.textContent = ""; return; }
    if (!matrices.length || !ui.matrix) { q.textContent = "No experiments yet."; st.textContent = ""; note.textContent = ""; return; }
    const row = matrices.find((m) => m.name === ui.matrix) || {};
    q.textContent = QUESTION[ui.matrix] || ui.matrix;
    const runsAll = expRuns();
    const fin = runsAll.filter((r) => isFinished(r.status)).length;
    const planned = runsAll.length || row.runs_planned || 0;
    const running = runsAll.filter((r) => r.status === "running" && !staleRun(r)).length;
    const stale = runsAll.filter(staleRun);
    const rep = report();
    let seedsTxt = "";
    if (rep && Array.isArray(rep.arms) && rep.arms.length) {
      const ns = rep.arms.map((a) => (a.confirm && num(a.confirm.n)) || 0);
      const lo = Math.min(...ns), hi = Math.max(...ns);
      seedsTxt = lo === hi ? `Each arm's numbers rest on ${lo} seed${lo === 1 ? "" : "s"}.` : `Arms' numbers rest on ${lo} to ${hi} seeds.`;
    }
    const partial = fin < planned;
    st.innerHTML = `<span class="num">${fmtInt(fin)}</span> of <span class="num">${fmtInt(planned)}</span> runs finished` +
      (running ? `, ${fmtInt(running)} running` : "") +
      (stale.length ? `, ${fmtInt(stale.length)} marked running but silent for ${esc(fmtAge(Date.now() - d3.min(stale, (r) => new Date(r.updated_at))).replace(" ago", ""))}` : "") + (partial ? ` · <span class="partial">partial</span>` : "") +
      (seedsTxt ? `. ${esc(seedsTxt)}` : ".");
    const bits = [];
    if (!mdetail) bits.push("Reading the matrix…");
    else if (!mdetail.report_exists) bits.push(`No report yet. Write one with .venv/bin/python -m lab.experiments.report ${ui.matrix}`);
    else if (!mdetail.report_readable) bits.push("The report is being written; the dashboard will read it on the next poll.");
    else {
      bits.push(`From report.json, written ${fmtWhen(mdetail.report_written_at)}.`);
      const after = runsAll.filter((r) => r.updated_at && mdetail.report_written_at && r.updated_at > mdetail.report_written_at).length;
      if (after) bits.push(`${after} run${after === 1 ? " has" : "s have"} changed since; the report does not include that yet.`);
      if (rep && rep.partial) bits.push("The report marks these results as partial.");
    }
    note.textContent = bits.join(" ");
  }

  // Dot-and-range chart of final confirm loss per arm, with cost per run beside it.
  function renderExpChart() {
    if (ui.mode !== "exp") return;
    const svgEl = $("svg.exp-chart");
    const note = $(".exp-chart-block .chart-note");
    const leg = $(".exp-legend");
    const s = d3.select(svgEl);
    const rep = report();
    const arms = rep && Array.isArray(rep.arms) ? rep.arms : [];
    if (!arms.length) {
      s.selectAll("*").remove();
      s.attr("height", 60);
      note.hidden = false;
      note.textContent = !mdetail ? "" : !mdetail.report_exists ? "Nothing to draw until the report is written." : "The report has no arms yet.";
      leg.innerHTML = "";
      return;
    }
    note.hidden = true;
    const width = Math.max(320, Math.floor(svgEl.parentNode.getBoundingClientRect().width));
    const rowH = 46;
    const m = { l: 150, r: 16, t: 30, b: 34 };
    const height = m.t + m.b + rowH * arms.length;
    s.attr("viewBox", `0 0 ${width} ${height}`).attr("height", height);
    const costW = Math.max(130, Math.min(220, width * 0.24));
    const gap = 34;
    const x0 = m.l, x1 = width - m.r - costW - gap;
    const c0 = width - m.r - costW, c1 = width - m.r;
    const byKey = new Map(expRuns().map((r) => [`${r.arm}|${r.seed}`, r]));
    const seedDots = arms.flatMap((a) => (a.seeds || []).map((sd) => {
      const r = byKey.get(`${a.arm}|${sd}`);
      return r && num(r.final_confirm) != null ? { arm: a.arm, seed: sd, v: r.final_confirm, run: r, partialArm: a.partial } : null;
    }).filter(Boolean));
    const start = rep.seed_confirm && num(rep.seed_confirm.value);
    const target = num(rep.target);
    const vals = arms.flatMap((a) => [a.confirm && a.confirm.min, a.confirm && a.confirm.max]).concat(seedDots.map((d) => d.v), [start, target]).filter((v) => num(v) != null);
    const ext = vals.length ? d3.extent(vals) : [0, 1];
    const pad = Math.max(0.01, (ext[1] - ext[0]) * 0.08);
    const x = d3.scaleLinear().domain([ext[0] - pad, ext[1] + pad]).nice(5).range([x0, x1]);
    const costs = arms.map((a) => a.cost_usd && num(a.cost_usd.max)).filter((v) => v != null);
    const xc = d3.scaleLinear().domain([0, Math.max(0.01, d3.max(costs) || 1)]).nice(4).range([c0, c1]);
    const yRow = (i) => m.t + rowH * i + rowH / 2;

    s.selectAll("*").remove();
    const g = s.append("g");
    // axes
    for (const t of x.ticks(5)) {
      g.append("line").attr("class", "gridline").attr("x1", x(t)).attr("x2", x(t)).attr("y1", m.t - 6).attr("y2", height - m.b + 4);
      g.append("text").attr("class", "tick-label").attr("x", x(t)).attr("y", height - m.b + 18).attr("text-anchor", "middle").text(d3.format(".2f")(t));
    }
    g.append("text").attr("class", "tick-label").attr("x", x0).attr("y", height - 4)
      .text("final loss on the confirmation window, ← better");
    for (const t of xc.ticks(4)) {
      g.append("line").attr("class", "gridline").attr("x1", xc(t)).attr("x2", xc(t)).attr("y1", m.t - 6).attr("y2", height - m.b + 4);
      g.append("text").attr("class", "tick-label").attr("x", xc(t)).attr("y", height - m.b + 18).attr("text-anchor", "middle").text(`$${t}`);
    }
    g.append("text").attr("class", "tick-label").attr("x", c0).attr("y", height - 4).text("cost per run");
    g.append("text").attr("class", "tick-label").attr("x", c1).attr("y", 14).attr("text-anchor", "end").text("cost per run, $");
    // reference lines: starting formula (blue) and the fixed target
    if (start != null) {
      g.append("line").attr("class", "line-ref").attr("x1", x(start)).attr("x2", x(start)).attr("y1", m.t - 10).attr("y2", height - m.b + 4);
      const nearEnd = x(start) > x1 - 80;
      g.append("text").attr("class", "tick-label ref-label").attr("x", x(start)).attr("dx", nearEnd ? -4 : 0).attr("y", m.t - 14)
        .attr("text-anchor", nearEnd ? "end" : "middle").text(`starting formula ${fmt3(start)}`);
    }
    if (target != null) {
      g.append("line").attr("class", "line-ref").attr("stroke-dasharray", "4 3").attr("x1", x(target)).attr("x2", x(target)).attr("y1", m.t - 10).attr("y2", height - m.b + 4);
      const near = start != null && Math.abs(x(start) - x(target)) < 120;
      g.append("text").attr("class", "tick-label ref-label").attr("x", x(target)).attr("y", near ? m.t - 1 : m.t - 14)
        .attr("text-anchor", near ? "end" : "middle").attr("dx", near ? -4 : 0).text(`target ${fmt3(target)}`);
    }
    // rows
    arms.forEach((a, i) => {
      const y = yRow(i);
      const row = g.append("g").attr("class", "exp-row");
      if (i % 2 === 0) row.append("rect").attr("x", 0).attr("y", y - rowH / 2).attr("width", width).attr("height", rowH).attr("fill", "rgba(213,233,241,0.25)");
      const label = row.append("text").attr("class", "arm-label").attr("x", x0 - 12).attr("y", y + 5).attr("text-anchor", "end").text(a.arm);
      if (armComment(a.arm)) label.append("title").text(armComment(a.arm));
      if (a.partial) row.append("text").attr("class", "arm-partial").attr("x", x0 - 12).attr("y", y + 18).attr("text-anchor", "end").text(`partial: ${a.finished} of ${a.planned} finished`);
      const c = a.confirm || {};
      if (num(c.min) != null && num(c.max) != null) {
        row.append("line").attr("class", "arm-range").attr("x1", x(c.min)).attr("x2", x(c.max)).attr("y1", y).attr("y2", y);
      }
      if (num(c.mean) != null) row.append("line").attr("class", "arm-mean").attr("x1", x(c.mean)).attr("x2", x(c.mean)).attr("y1", y - 9).attr("y2", y + 9);
      if (num(c.mean) == null) row.append("text").attr("class", "tick-label").attr("x", x0).attr("y", y + 5).text("no run yet");
      const cc = a.cost_usd || {};
      if (num(cc.min) != null && num(cc.max) != null) row.append("line").attr("class", "arm-range").attr("x1", xc(cc.min)).attr("x2", xc(cc.max)).attr("y1", y).attr("y2", y);
      if (num(cc.mean) != null) {
        row.append("line").attr("class", "arm-mean").attr("x1", xc(cc.mean)).attr("x2", xc(cc.mean)).attr("y1", y - 9).attr("y2", y + 9);
        row.append("text").attr("class", "tick-label").attr("x", Math.min(c1 - 2, xc(cc.max) + 6)).attr("y", y - 8)
          .attr("text-anchor", xc(cc.max) + 50 > c1 ? "end" : "start").text(fmtUsd(cc.mean));
      }
    });
    // one dot per seed; click opens that run
    const dots = g.append("g").selectAll("circle").data(seedDots).join("circle")
      .attr("class", (d) => `seed-dot${isFinished(d.run.status) ? "" : " unfinished"}`)
      .attr("r", 7).attr("tabindex", 0).attr("role", "button")
      .attr("aria-label", (d) => `${d.arm}, seed ${d.seed}: ${fmt3(d.v)}. Open this run.`)
      .attr("cx", (d) => x(d.v))
      .attr("cy", (d) => { const i = arms.findIndex((a) => a.arm === d.arm); const same = seedDots.filter((e) => e.arm === d.arm); const k = same.indexOf(d); return yRow(i) + (same.length > 1 ? (k - (same.length - 1) / 2) * 5 : 0); });
    dots.on("click", (e, d) => openRun(`exp/${ui.matrix}/${d.arm}/${d.seed}`))
      .on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openRun(`exp/${ui.matrix}/${d.arm}/${d.seed}`); } })
      .on("mousemove", (e, d) => showTip(`<b>${esc(d.arm)}, seed ${d.seed}</b><br>Final confirmation loss ${fmt3(d.v)}<br>${esc(capital(statusWord(d.run.status)))}${num(d.run.cost_usd) != null ? `, ${fmtUsd(d.run.cost_usd)}` : ""}<br><span class="q">Click to open this run</span>`, e))
      .on("mouseleave", hideTip);
    leg.innerHTML = `
      <span class="k"><svg width="12" height="12" viewBox="-6 -6 12 12"><circle r="5" class="seed-dot"/></svg>One seed's run (click to open)</span>
      <span class="k"><svg width="14" height="14" viewBox="-7 -7 14 14"><line y1="-6" y2="6" class="arm-mean"/></svg>Mean across seeds</span>
      <span class="k"><svg width="20" height="8" viewBox="0 -4 20 8"><line x1="1" x2="19" class="arm-range"/></svg>Range</span>
      <span class="k"><span class="sw-line tide"></span>Starting formula</span>
      <span class="k"><span class="sw-line dash"></span>Target, ${rep.target_rel != null ? `${Math.round(rep.target_rel * 100)}% below` : "fixed in advance"}</span>`;
  }

  function table(headers, rows, cls = "") {
    if (!rows.length) return "";
    return `<table class="board exp-table ${cls}"><thead><tr>${headers.map((h, i) => `<th${i === 0 ? "" : ""}>${h}</th>`).join("")}</tr></thead><tbody>` +
      rows.map((r) => `<tr>${r.map((c, i) => (i === 0 ? `<th>${c}</th>` : `<td>${c}</td>`)).join("")}</tr>`).join("") + "</tbody></table>";
  }

  function renderExpTables() {
    const main = $(".exp-tables");
    const side = $(".exp-side-tables");
    const rep = report();
    if (!rep) { main.innerHTML = progressGrid(); side.innerHTML = ""; return; }
    const order = armOrder();
    const sortArms = (list) => [...list].sort((a, b) => {
      const ia = order.indexOf(a.arm), ib = order.indexOf(b.arm);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    let html = "";

    // equal proposals versus equal dollars
    const eq = rep.equal_dollars || {};
    const eqBy = new Map((eq.arms || []).map((a) => [a.arm, a]));
    const free = new Set(eq.free_arms || []);
    const arms = sortArms(rep.arms || []);
    html += `<div class="exp-block"><h3>Equal proposals or equal dollars <span class="quiet">final confirmation loss, mean (range)</span></h3>
      <p class="sub">Equal proposals: after the full budget of proposals. Equal dollars: each run's loss at the last generation within ${eq.budget_usd != null ? fmtUsd(eq.budget_usd) : NA}${eq.from_run ? `, the cost of the cheapest paid run (${esc(eq.from_run)})` : ""}. Any claim that one arm is better must say on which basis.</p>` +
      table(["Arm", "Seeds", "Equal proposals", "Equal dollars", "$ per proposal", "Passed / adopted"],
        arms.map((a) => {
          const e = eqBy.get(a.arm);
          return [esc(a.arm), `${fmtInt((a.confirm && a.confirm.n) || 0)}${a.partial ? ` <span class="partial">partial</span>` : ""}`,
            mr(a.confirm), free.has(a.arm) ? "costs nothing; left out" : e ? mr(e.confirm) : NA,
            a.usd_per_proposal ? fmt3(a.usd_per_proposal.mean) : NA,
            `${a.accepted ?? NA} / ${a.became_incumbent == null ? NA : a.became_incumbent} of ${a.proposals ?? NA}`];
        })) + "</div>";

    // paired comparison
    const p = rep.paired;
    if (p && p.a && p.b) {
      const line = (basis, v) => v ? `<li>${basis}: ${esc(p.a)} lower in ${v.a_wins} seed${v.a_wins === 1 ? "" : "s"}, ${esc(p.b)} lower in ${v.b_wins}${v.ties ? `, ${v.ties} tied` : ""}${num(v.mean_diff) != null ? `; mean difference ${fmtDelta(v.mean_diff)}` : ""}. <b>${esc(v.test || "")}</b>${num(v.p_value) != null ? ` (p = ${v.p_value.toFixed(3)})` : ""}</li>` : "";
      html += `<div class="exp-block"><h3>${esc(p.a)} against ${esc(p.b)} <span class="quiet">paired by seed</span></h3><ul class="exp-list">
        ${line("Equal proposals", p.equal_proposals)}${line("Equal dollars", p.equal_dollars)}</ul>${p.equal_dollars_note ? `<p class="sub">${esc(p.equal_dollars_note)}</p>` : ""}</div>`;
    }

    // time to target
    const ttt = rep.time_to_target || [];
    if (ttt.length) {
      const byArm = d3.group(ttt, (r) => r.arm);
      html += `<div class="exp-block"><h3>Time to target <span class="quiet">confirmation loss at or below ${fmt3(rep.target)}</span></h3>
        <p class="sub">${esc(rep.target_note || "")}</p>` +
        table(["Arm", "Reached", "Seed", "Proposals", "Dollars", "Minutes"],
          sortArms([...byArm.keys()].map((arm) => ({ arm }))).flatMap(({ arm }) => {
            const rs = byArm.get(arm);
            const k = rs.filter((r) => r.reached).length;
            return rs.map((r, i) => [i === 0 ? esc(arm) : "", i === 0 ? `${k} of ${rs.length}` : "", `seed ${r.seed}`,
              r.reached ? fmtInt(r.proposals) : `<span class="not-reached">${r.finished === false || r.status === "running" ? "not reached (yet)" : "not reached"}</span>`,
              r.reached ? fmtUsd(r.usd) : "", r.reached && num(r.minutes) != null ? r.minutes.toFixed(1) : ""]);
          }), "ttt") + "</div>";
    }
    main.innerHTML = html + (rep.partial ? progressGrid() : "");

    // side: where ideas came from, board, search, memory
    let sh = "";
    if (Array.isArray(rep.origins)) {
      sh += `<div class="exp-block"><h3>Where the ideas came from</h3>` +
        (rep.origins.length ? table(["Arm", "Origin", "Proposals", "Passed", "Adopted"],
          sortArms(rep.origins).map((o) => [esc(o.arm), esc(ORIGIN_WORD[o.origin] || String(o.origin).replace(/_/g, " ")), fmtInt(o.proposals), fmtInt(o.passed), o.adopted == null ? NA : fmtInt(o.adopted)]))
          : `<p class="sub">No proposals yet.</p>`) + "</div>";
    } else {
      sh += `<div class="exp-block"><h3>Where the ideas came from</h3><p class="sub">This report was written before the origin table existed.</p></div>`;
    }
    if (Array.isArray(rep.board) && rep.board.length) {
      sh += `<div class="exp-block"><h3>Ideas board <span class="quiet">researcher arms with search</span></h3>` +
        table(["Arm", "Offered", "Used", "Used, passed", "Used, adopted", "Passed, new to the model"],
          sortArms(rep.board).map((b) => [esc(b.arm), fmtInt(b.offered), fmtInt(b.used), fmtInt(b.used_passed), fmtInt(b.used_adopted),
            `${fmtInt(b.passed_not_known_closed_book)}${num(b.share_passed_not_known) != null ? ` (${Math.round(b.share_passed_not_known * 100)}%)` : ""}`])) + "</div>";
    }
    const search = (rep.search || []).filter((r) => r.searches || r.claims);
    if (search.length) {
      sh += `<div class="exp-block"><h3>Search</h3>` +
        table(["Arm", "Backend", "Searches", "Failed", "Claims", "Passed", "New to the model", "Per 100 searches"],
          sortArms(search).map((r) => [esc(r.arm), esc(r.backend), fmtInt(r.searches), fmtInt(r.failed), fmtInt(r.claims), fmtInt(r.accepted),
            `${fmtInt(r.accepted_new)}${num(r.share_accepted_new) != null ? ` (${Math.round(r.share_accepted_new * 100)}%)` : ""}`,
            num(r.useful_per_100_searches) != null ? r.useful_per_100_searches.toFixed(1) : NA]));
      const sc = rep.search_cost || [];
      if (sc.length) {
        sh += table(["Arm", "Search-role cost", "Passed from search", "Per dollar"],
          sortArms(sc).map((r) => [esc(r.arm), fmtUsd(r.search_role_usd), fmtInt(r.accepted_from_search), num(r.accepted_per_usd) != null ? r.accepted_per_usd.toFixed(2) : NA]));
      }
      sh += "</div>";
    }
    const mem = (rep.memory || []).filter((r) => r.recall_calls);
    if (mem.length) {
      sh += `<div class="exp-block"><h3>Memory</h3>` +
        table(["Arm", "Notebook", "Recalls", "Mean latency", "Mean characters"],
          sortArms(mem).map((r) => [esc(r.arm), esc(r.memory || NA), fmtInt(r.recall_calls),
            num(r.mean_latency_ms) != null ? `${r.mean_latency_ms.toFixed(0)} ms` : NA, num(r.mean_chars) != null ? fmtInt(Math.round(r.mean_chars)) : NA])) + "</div>";
    }
    if ((rep.skipped || []).length) {
      sh += `<div class="exp-block"><h3>Not run</h3><ul class="exp-list">` +
        rep.skipped.map((r) => `<li>${esc(r.arm)} seed ${esc(r.seed)}: ${esc(statusWord(r.status))}${r.message ? `, ${esc(r.message)}` : ""}</li>`).join("") + "</ul></div>";
    }
    side.innerHTML = sh;
  }

  // Status of every planned run by arm and seed, with each run's own final loss. No statistics.
  function progressGrid() {
    const rs = expRuns();
    if (!rs.length) return "";
    const seeds = [...new Set(rs.map((r) => r.seed))].sort((a, b) => a - b);
    const arms = [...new Set(rs.map((r) => r.arm))];
    const cell = (r) => {
      if (!r) return "";
      const st = staleRun(r) ? "silent" : statusWord(r.status);
      const cls = isFinished(r.status) ? "" : r.status === "running" && !staleRun(r) ? "st-running" : "st-other";
      const val = num(r.final_confirm) != null ? ` · ${fmt3(r.final_confirm)}` : "";
      const label = `${esc(st)}${val}`;
      return r.has_run ? `<button class="grid-run ${cls}" data-run="exp/${esc(ui.matrix)}/${esc(r.arm)}/${esc(r.seed)}">${label}</button>`
        : `<span class="grid-run ${cls}">${label}</span>`;
    };
    return `<div class="exp-block"><h3>Runs by arm and seed <span class="quiet">status, and the current or final loss on the confirmation window</span></h3>
      <p class="sub">${report() ? "Shown because the report is partial." : "Each run's own latest figure, not a result: the report has not been written."}</p>` +
      table(["Arm", ...seeds.map((sd) => `Seed ${sd}`)],
        arms.map((arm) => [`<span title="${esc(armComment(arm))}">${esc(arm)}</span>`, ...seeds.map((sd) => cell(rs.find((r) => r.arm === arm && r.seed === sd)))]), "grid") + "</div>";
  }

  function renderExpRuns() {
    const row = $(".exp-run-row");
    const rs = expRuns();
    if (!mdetail) { row.innerHTML = `<div class="log-empty">${matrices && !matrices.length ? "No experiments yet." : "Reading the matrix…"}</div>`; return; }
    if (!rs.length) { row.innerHTML = `<div class="log-empty">No runs planned in this matrix.</div>`; return; }
    row.innerHTML = rs.map((r) => {
      const st = staleRun(r) ? `Marked running; no update for ${esc(fmtAge(Date.now() - new Date(r.updated_at)).replace(" ago", ""))}` : esc(capital(statusWord(r.status)));
      const body = `<div class="when">${esc(r.arm)} · seed ${esc(r.seed)}</div>
        <div class="what st-${esc(r.status)}">${st}</div>
        <div class="then">${num(r.final_confirm) != null ? `final ${fmt3(r.final_confirm)}` : r.message ? esc(r.message) : ""}${num(r.cost_usd) != null ? ` · ${fmtUsd(r.cost_usd)}` : ""}</div>`;
      return r.has_run
        ? `<button class="log-item exp-run" data-run="exp/${esc(ui.matrix)}/${esc(r.arm)}/${esc(r.seed)}">${body}</button>`
        : `<div class="log-item exp-run off">${body}</div>`;
    }).join("");
  }

  // ------------------------------------------------------------ lifecycle

  return {
    setState() {},
    poll,
    activate(parts) {
      active = true;
      if (parts[0] === "experiments") {
        if (parts[1]) { if (parts[1] !== ui.matrix) mdetail = null; ui.matrix = parts[1]; ui.matrixChosen = true; }
        setMode("exp", false);
      } else {
        const key = parts[0] === "exp" && parts.length >= 4 ? parts.slice(0, 4).join("/")
          : parts[0] && parts[1] ? `${parts[0]}/${parts[1]}` : null;
        if (key) {
          if (key !== ui.key) { detail = null; lastFindingsKey = ""; ui.open.clear(); }
          ui.key = key; ui.chosen = true;
        }
        setMode("run", false);
      }
    },
    deactivate() { active = false; hideTip(); },
    resize() { if (active) { if (ui.mode === "exp") renderExpChart(); else renderCurve(); } },
    hashParts,
    headerStatus() {
      if (ui.mode === "exp") {
        if (matrices === null) return { chain: "Reading the experiments…", age: "", stale: false };
        const running = matrices.reduce((a, m) => a + (m.runs_running || 0) - (m.runs_silent || 0), 0);
        const chain = `${fmtInt(matrices.length)} experiment matri${matrices.length === 1 ? "x" : "ces"}, ${fmtInt(running)} run${running === 1 ? "" : "s"} running`;
        const row = matrices.find((m) => m.name === ui.matrix);
        return { chain, age: row && row.updated_at ? `updated ${fmtAge(Date.now() - new Date(row.updated_at))}` : "", stale: false };
      }
      if (runs === null) return { chain: failed ? "Cannot reach the lab server" : "Reading the lab runs…", age: "", stale: failed };
      const n = runs.length, running = runs.filter((r) => r.status === "running").length;
      const chain = `${fmtInt(n)} lab run${n === 1 ? "" : "s"}, ${fmtInt(running)} running`;
      const row = selectedRow();
      if (!row || !row.updated_at) return { chain, age: "", stale: false };
      const ageMs = Date.now() - new Date(row.updated_at);
      const stale = row.status === "running" && ageMs > 45 * 60e3;
      return { chain, age: stale ? `run updated ${fmtAge(ageMs)}: it may have stopped` : `run updated ${fmtAge(ageMs)}`, stale };
    },
  };
}
