// Lab view: the research lab itself. One run at a time: how the current best
// forecaster's loss moved over generations, every change it tested, its source,
// and what the run cost. Reads /api/lab/runs and /api/lab/runs/<problem>/<run_id>.
//
// Colours: ink for measured losses, blue for baselines and reference lines,
// --miss for rejected changes. Magenta stays reserved for sealed forecasts.

import { fmtWhen, fmtInt, fmtAge, esc, showTip, hideTip, capital } from "./seal.js";

const d3 = window.d3;
const START_CMD = ".venv/bin/python -m lab.swarm.run --problem coast_water --generations 5 --proposals 4";
const STATUS_WORD = {
  running: "running", finished: "finished", stopped_cost_cap: "stopped at its cost cap",
  failed: "failed", unreadable: "unreadable", starting: "starting",
};
const ROLE_WORD = { analyst: "analyst", closed_book: "closed book", scout: "scout", judge: "judge",
  engineer: "engineer", playbook: "playbook", single_agent: "single agent" };

const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const NA = "n/a";
const fmtLoss = (v) => (num(v) == null ? NA : v.toFixed(2));
const fmtDelta = (v) => (num(v) == null ? NA : `${v < 0 ? "−" : "+"}${Math.abs(v).toFixed(3)}`);
const pct = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : NA);
const statusWord = (s) => STATUS_WORD[s] || s || "unknown";

// Loss unit, only if the run says so.
function lossUnit(detail) {
  const c = (detail && detail.config) || {};
  const s = (detail && detail.summary) || {};
  for (const v of [c.loss_unit, c.unit, s.loss_unit, s.unit, c.loss, s.loss]) {
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return null;
}
// "absolute error, cm" -> "cm"; "cm" -> "cm"
const shortUnit = (u) => (u ? u.split(",").pop().trim() : "");

// Outcome of one finding, from its own fields only.
function outcome(f) {
  if (f.accepted === true) {
    if (f.became_incumbent === true) return { key: "adopted", text: "kept and adopted" };
    if (f.became_incumbent === false) return { key: "passed", text: "passed, not adopted" };
    return { key: "passed-na", text: `passed; adoption ${NA}` };
  }
  if (f.accepted === false) return { key: "rejected", text: "rejected" };
  return { key: "na", text: NA };
}

function originWords(f) {
  if (f.origin === "search") {
    if (f.known_closed_book === true) return "from search, already known to the model";
    if (f.known_closed_book === false) return "from search";
    return "from search (known to the model: n/a)";
  }
  if (f.origin === "closed_book") return "closed book";
  if (f.origin === "single_agent") return "single agent";
  if (f.origin === "random_mutation") return "random mutation";
  return f.origin ? String(f.origin).replace(/_/g, " ") : NA;
}

// Baselines only if the summary carries them: {name: number | {select, confirm}} or [{name, select, confirm}].
function baselines(summary) {
  const b = summary && summary.baselines;
  if (!b) return [];
  const rows = Array.isArray(b) ? b : Object.entries(b).map(([name, v]) => (typeof v === "number" ? { name, value: v } : { name, ...v }));
  const out = [];
  for (const r of rows) {
    const name = r.name || r.model || "baseline";
    if (num(r.value) != null) out.push({ name, value: r.value, window: null });
    if (num(r.select) != null) out.push({ name, value: r.select, window: "select" });
    if (num(r.confirm) != null) out.push({ name, value: r.confirm, window: "confirm" });
  }
  return out;
}

export function create(root, api) {
  const ui = { problem: null, runId: null, chosen: false, show: "all", origin: "all", open: new Set() };
  let runs = null; // null until the first answer
  let detail = null;
  let failed = false;
  let active = false;
  let lastFindingsKey = "";

  root.classList.add("lab-view");
  root.innerHTML = `
    <div class="lab-main">
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
              <button data-show="all">All</button><button data-show="kept">Kept</button><button data-show="rejected">Rejected</button>
            </div>
            <div class="seg origin-seg" role="group" aria-label="Origin">
              <button data-origin="all">Any origin</button><button data-origin="search">From search</button><button data-origin="closed_book">Closed book</button>
            </div>
          </div>
        </div>
        <div class="ledger-counts"></div>
        <div class="ledger-scroll"><table class="ledger"></table></div>
      </section>
    </div>
    <aside class="side">
      <section class="lead-block">
        <p class="sentence">Reading the lab runs…</p>
        <p class="sub lab-note"></p>
      </section>
      <section class="runs-block">
        <div class="panel-head"><h2>Runs <span class="quiet">newest first</span></h2></div>
        <div class="run-list" role="listbox" aria-label="Lab runs"></div>
      </section>
      <section class="source-block">
        <details class="source">
          <summary><span class="source-title">Current best forecaster</span> <span class="quiet source-meta"></span></summary>
          <p class="change-line"></p>
          <div class="code-wrap"><pre class="code"></pre></div>
        </details>
        <p class="change-line change-closed"></p>
      </section>
    </aside>
    <div class="log lab-cost">
      <div class="log-title"><strong>Cost and effort</strong><span class="cost-sub"></span></div>
      <div class="log-row cost-row"></div>
    </div>`;

  const $ = (s) => root.querySelector(s);

  // redraw the curve whenever its box changes size (window resize, layout switch)
  let lastBox = "";
  new ResizeObserver(() => {
    const r = $(".lab-curve .chart-wrap").getBoundingClientRect();
    const box = `${Math.round(r.width)}x${Math.round(r.height)}`;
    if (active && r.width && box !== lastBox) { lastBox = box; renderCurve(); }
  }).observe($(".lab-curve .chart-wrap"));

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
  $(".run-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-run]");
    if (!b) return;
    const [p, r] = b.dataset.run.split("/");
    selectRun(p, r, true);
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

  // ------------------------------------------------------------ data

  function pickDefault() {
    if (!runs || !runs.length) return null;
    return [...runs].sort((a, b) => String(b.updated_at || b.started_at || "").localeCompare(String(a.updated_at || a.started_at || "")))[0];
  }

  const selectedRow = () => (runs || []).find((r) => r.problem === ui.problem && r.run_id === ui.runId) || null;

  function hashParts() {
    return ui.chosen && ui.problem ? [ui.problem, ui.runId] : [];
  }

  async function selectRun(problem, runId, byUser) {
    if (problem === ui.problem && runId === ui.runId && detail) return;
    ui.problem = problem;
    ui.runId = runId;
    if (byUser) ui.chosen = true;
    ui.open.clear();
    detail = null;
    lastFindingsKey = "";
    api.setHash(hashParts());
    render();
    await fetchDetail();
    render();
  }

  async function fetchDetail() {
    if (!ui.problem) return;
    const d = await api.getJSON(`/api/lab/runs/${encodeURIComponent(ui.problem)}/${encodeURIComponent(ui.runId)}`);
    if (d && d.run_id === ui.runId) detail = d;
  }

  async function poll() {
    const r = await api.getJSON("/api/lab/runs");
    if (r && Array.isArray(r.runs)) { runs = r.runs; failed = false; } else { failed = true; if (runs === null) runs = null; }
    if (runs && !runs.length) {
      ui.problem = null; ui.runId = null; detail = null; lastFindingsKey = "";
    }
    if (runs && runs.length) {
      if (!selectedRow()) {
        const d = pickDefault();
        ui.problem = d.problem; ui.runId = d.run_id; ui.chosen = false; detail = null; lastFindingsKey = "";
      }
      const row = selectedRow();
      if (!detail || detail.run_id !== ui.runId || (row && row.status === "running") ||
          (detail.summary && row && detail.summary.updated_at !== row.updated_at)) {
        await fetchDetail();
      }
    }
    if (active) render();
  }

  // ------------------------------------------------------------ render

  function render() {
    renderSentence();
    renderRuns();
    renderCurve();
    renderLedger(false);
    renderSource();
    renderCost();
  }

  function renderSentence() {
    const s = $(".sentence");
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
    const f = detail.findings || [];
    const unit = shortUnit(lossUnit(detail));
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
      if (a == null || b == null) text = `${g} the ${what} on later, unseen data is ${NA}.`;
      else if (b < a) text = `${g} the lab cut the ${what} on later, unseen data from ${u(a)} to ${u(b)}.`;
      else if (b > a) text = `${g} the ${what} on later, unseen data rose from ${u(a)} to ${u(b)}.`;
      else text = `${g} the ${what} on later, unseen data stayed at ${u(a)}.`;
      const kept = f.filter((x) => x.accepted === true).length;
      text += ` It tested <span class="num">${fmtInt(f.length)}</span> change${f.length === 1 ? "" : "s"} and kept <span class="num">${fmtInt(kept)}</span>.`;
    }
    const st = sm.status;
    if (st === "running" && num(sm.generations_planned) != null && done != null) {
      text += ` The run is still going: generation ${fmtInt(done + 1)} of ${fmtInt(sm.generations_planned)}.`;
    } else if (st === "stopped_cost_cap") text += " It stopped when it reached its cost cap.";
    else if (st === "failed") text += " The run failed.";
    s.innerHTML = text;
    note.textContent = "Later, unseen data is the confirm window: cases after the select window that the agents never see. " +
      "Changes are chosen on the select window; the confirm window is only used to refuse a change that makes it worse." +
      (unit ? "" : " The run does not state the loss unit.");
  }

  function renderRuns() {
    const el = $(".run-list");
    if (!runs || !runs.length) {
      el.innerHTML = runs ? `<p class="empty">No runs under <code>runs/lab/</code> yet.</p>` : "";
      return;
    }
    el.innerHTML = runs.map((r) => {
      const on = r.problem === ui.problem && r.run_id === ui.runId;
      const gens = r.generations_done != null
        ? `${fmtInt(r.generations_done)}${r.generations_planned != null ? ` of ${fmtInt(r.generations_planned)}` : ""} generations`
        : `generations ${NA}`;
      const upd = r.updated_at ? `updated ${fmtAge(Date.now() - new Date(r.updated_at))}` : "";
      return `<button class="run${on ? " on" : ""}" role="option" aria-selected="${on}" data-run="${esc(r.problem)}/${esc(r.run_id)}">
        <span class="run-status st-${esc(r.status || "")}">${esc(capital(statusWord(r.status)))}</span>
        <span class="run-what">${esc(r.problem)} · ${esc(r.mode || NA)} · ${esc(gens)}</span>
        <span class="run-id">${esc(r.run_id)}${upd ? ` · ${esc(upd)}` : ""}</span></button>`;
    }).join("");
  }

  // ------------------------------------------------------------ curve

  function adoptedIn(gen) {
    const f = (detail && detail.findings) || [];
    const same = f.filter((x) => x.generation === gen);
    const adopted = same.filter((x) => x.became_incumbent === true);
    if (adopted.length) return { list: adopted, known: true };
    return { list: same.filter((x) => x.accepted === true), known: false };
  }

  function renderCurve() {
    const svgEl = $("svg.curve");
    const note = $(".lab-curve .chart-note");
    const wrap = svgEl.parentNode;
    const width = Math.max(300, Math.floor(wrap.getBoundingClientRect().width));
    const height = Math.max(150, Math.floor(wrap.getBoundingClientRect().height) || 220);
    const s = d3.select(svgEl).attr("viewBox", `0 0 ${width} ${height}`).attr("height", height);
    const sm = (detail && detail.summary) || {};
    const curve = (Array.isArray(sm.curve) ? sm.curve : []).filter((c) => num(c.generation) != null);
    const unit = shortUnit(lossUnit(detail));
    const base = baselines(sm);
    $(".curve-legend").innerHTML = `
      <span class="k"><span class="sw-line"></span>Select window</span>
      <span class="k"><span class="sw-line confirm"></span>Confirm window (later, unseen)</span>
      <span class="k"><svg width="12" height="12" viewBox="-6 -6 12 12"><circle r="4" fill="#16283A"/></svg>A change was adopted</span>
      ${base.length ? `<span class="k"><span class="sw-line tide"></span>Baseline</span>` : ""}`;
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

    s.select("g.base-lines").selectAll("g.bl").data(base, (b) => `${b.name}|${b.window}`).join((en) => {
      const g = en.append("g").attr("class", "bl");
      g.append("line").attr("class", "line-ref");
      g.append("text").attr("class", "tick-label");
      return g;
    }).each(function (b) {
      const g = d3.select(this);
      g.select("line").attr("x1", m.l).attr("x2", width - m.r).attr("y1", y(b.value)).attr("y2", y(b.value))
        .attr("stroke-dasharray", b.window === "confirm" ? "5 3" : null);
      g.select("text").attr("x", width - m.r + 6).attr("y", y(b.value) + 4).attr("fill", "#2A6A9E")
        .text(`${b.name}${b.window ? `, ${b.window}` : ""}`);
    });

    const step = (key) => d3.line().defined((c) => num(c[key]) != null).x((c) => x(c.generation)).y((c) => y(c[key])).curve(d3.curveStepAfter);
    const ext2 = [...curve];
    s.select("g.lines").selectAll("path.sel").data([ext2]).join("path").attr("class", "sel lab-line").attr("d", step("select"));
    s.select("g.lines").selectAll("path.con").data([ext2]).join("path").attr("class", "con lab-line confirm").attr("d", step("confirm"));

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
    s.select("g.ends").selectAll("text").data(ends, (e) => e.k).join("text").attr("class", "end-label")
      .attr("x", x(last.generation) + 6).attr("y", (e) => (e.yy ?? y(e.v)) + 4)
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
      if (ui.show === "kept" && f.accepted !== true) return false;
      if (ui.show === "rejected" && f.accepted !== false) return false;
      if (ui.origin === "search" && f.origin !== "search") return false;
      if (ui.origin === "closed_book" && f.origin !== "closed_book") return false;
      return true;
    });
  }

  function renderLedger(force) {
    $(".show-seg").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.show === ui.show)));
    $(".origin-seg").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.origin === ui.origin)));
    const table = $("table.ledger");
    const counts = $(".ledger-counts");
    const all = (detail && detail.findings) || [];
    const key = `${ui.runId}|${all.length}|${ui.show}|${ui.origin}|${all.length ? all[all.length - 1].id : ""}`;
    if (!force && key === lastFindingsKey) return;
    lastFindingsKey = key;

    if (!detail) {
      counts.textContent = "";
      table.innerHTML = runs && !runs.length ? "" : `<tbody><tr><td class="empty">Reading the run…</td></tr></tbody>`;
      return;
    }
    // counts over the whole run, not the filtered view
    const kept = all.filter((f) => f.accepted === true);
    const keptSearch = kept.filter((f) => f.origin === "search");
    const judged = keptSearch.filter((f) => f.known_closed_book === true || f.known_closed_book === false);
    const fresh = judged.filter((f) => f.known_closed_book === false);
    const adoptedKnown = all.some((f) => f.became_incumbent === true || f.became_incumbent === false);
    counts.innerHTML = `
      <span><b>${fmtInt(all.length)}</b> tested</span>
      <span><b>${fmtInt(kept.length)}</b> kept</span>
      <span><b>${pct(keptSearch.length, kept.length)}</b> of kept changes came from search</span>
      <span><b>${pct(fresh.length, judged.length)}</b> of those were not already known to the model${judged.length < keptSearch.length ? ` (${judged.length} of ${keptSearch.length} judged)` : ""}</span>
      ${adoptedKnown ? "" : `<span class="quiet">This run does not record which kept change was adopted.</span>`}`;

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
        : f.source_title ? esc(f.source_title) : "no source";
      const extra = [originWords(f)];
      if (f.source_relevance) extra.push(`${f.source_relevance} source`);
      if (f.search_backend) extra.push(`via ${f.search_backend}`);
      if (f.duplicate_of) extra.push("duplicate of an earlier proposal");
      return `<tr data-id="${esc(f.id || "")}" tabindex="0" class="${rej ? "rej" : "kept"}${ui.open.has(f.id) ? " open" : ""}">
        <td class="c-gen">${f.generation ?? NA}</td>
        <td class="c-claim"><div class="claim">${esc(f.claim || NA)}</div>
          <div class="src">${src} · <span class="origin">${esc(extra.join(" · "))}</span></div>
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
    const lines = $$changeLines();
    if (!detail || detail.incumbent_source == null) {
      meta.textContent = detail ? "(no incumbent.py in this run)" : "";
      code.textContent = "";
      lines.forEach((el) => { el.textContent = ""; });
      det.hidden = !detail;
      return;
    }
    det.hidden = false;
    const src = String(detail.incumbent_source);
    const all = src.split("\n");
    const change = all.find((l) => /^\s*#\s*Change:/.test(l));
    const hash = detail.summary && detail.summary.incumbent_hash;
    meta.textContent = `${all.length} lines${hash ? ` · ${hash}` : ""}`;
    const changeText = change ? change.replace(/^\s*#\s*Change:\s*/, "") : null;
    lines.forEach((el) => {
      el.innerHTML = changeText ? `<span class="quiet">Latest change:</span> ${esc(changeText)}` : `<span class="quiet">Latest change:</span> ${NA} (the file has no <code># Change:</code> line)`;
    });
    if (code.dataset.hash !== String(hash) || code.dataset.len !== String(src.length)) {
      const w = String(all.length).length;
      code.innerHTML = all.map((l, i) => `<span class="ln">${String(i + 1).padStart(w, " ")}</span>${esc(l)}`).join("\n");
      code.dataset.hash = String(hash);
      code.dataset.len = String(src.length);
    }
  }

  function $$changeLines() {
    return [...root.querySelectorAll(".change-line")];
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

  // ------------------------------------------------------------ lifecycle

  return {
    setState() {},
    poll,
    activate(parts) {
      active = true;
      if (parts[0] && parts[1]) {
        if (parts[0] !== ui.problem || parts[1] !== ui.runId) { detail = null; lastFindingsKey = ""; }
        ui.problem = parts[0]; ui.runId = parts[1]; ui.chosen = true;
      }
      render();
    },
    deactivate() { active = false; hideTip(); },
    resize() { if (active) renderCurve(); },
    hashParts,
    headerStatus() {
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
