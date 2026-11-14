// Turbulence view: sealed risk zones over the continental US, and the pilot
// reports that later land on them.

import {
  drawSeals, drawTicks, sealSVG, fmtTime, fmtWhen, fmtInt, esc, showTip, hideTip, boxSize, numberWord,
} from "./seal.js";

const d3 = window.d3;
const topojson = window.topojson;

const LAB = "SeedBlend";
const ROC_MODELS = ["SeedBlend", "Ellrod1", "GAirmet"];
const BOARD_ORDER = ["SeedBlend", "Ellrod1", "GAirmet", "Shear", "Richardson", "WindSpeed", "Climatology"];
const LABEL = {
  SeedBlend: "Lab formula",
  Ellrod1: "Ellrod index",
  GAirmet: "G-AIRMET",
  Shear: "Shear",
  Richardson: "Richardson",
  WindSpeed: "Wind speed",
  Climatology: "Climatology",
};
const OFFICIAL = new Set(["Ellrod1", "GAirmet"]);
const CODE_WORDS = ["smooth air", "smooth to light", "light turbulence", "light to moderate turbulence",
  "moderate turbulence", "moderate to severe turbulence", "severe turbulence", "severe to extreme turbulence",
  "extreme turbulence"];
const TOP = 0.8;
const WASH = 0.6;
const H = 3600e3;
const GRADE_DELAY_MIN = 75;
const DEFAULT_HPA = 250;

function flToHpa(fl) {
  const h = fl * 100 * 0.3048;
  if (h <= 11000) return 1013.25 * Math.pow(1 - 2.25577e-5 * h, 5.25588);
  return 226.32 * Math.exp(-(h - 11000) / 6341.62);
}

function nearestLevel(fl, levels) {
  const lp = Math.log(flToHpa(fl));
  let best = 0;
  levels.forEach((p, i) => { if (Math.abs(Math.log(p) - lp) < Math.abs(Math.log(levels[best]) - lp)) best = i; });
  return best;
}

const sgn = (v) => (v < 0 ? `−${Math.abs(v).toFixed(4)}` : v.toFixed(4));
// Real lead: valid time minus sealing time (older records carry only an hour offset in meta).
const leadMin = (valid, sealed) => (new Date(valid) - new Date(sealed)) / 60e3;
function leadText(min) {
  if (min == null || !isFinite(min)) return "";
  if (min < 90) return `${Math.max(1, Math.round(min))} min`;
  const h = Math.round(min / 6) / 10;
  return `${h % 1 ? h.toFixed(1) : h.toFixed(0)} h`;
}
const flName = (fl) => `FL${String(Math.round(fl / 10) * 10).padStart(3, "0")}`;
const hourKey = (t) => new Date(t).toISOString().replace(".000", "");

function quantileThreshold(values, share) {
  const s = values.filter((v) => v != null && isFinite(v)).sort((a, b) => a - b);
  if (!s.length) return null;
  return s[Math.min(s.length - 1, Math.ceil(share * s.length))];
}

// Mann-Whitney AUC and ROC points from (score, label) pairs
function roc(pairs) {
  const pos = pairs.filter((p) => p.y === 1).length;
  const neg = pairs.length - pos;
  if (!pos || !neg) return null;
  const s = [...pairs].sort((a, b) => b.s - a.s);
  const pts = [[0, 0]];
  let tp = 0, fp = 0, auc = 0;
  for (let i = 0; i < s.length;) {
    let j = i, dtp = 0, dfp = 0;
    while (j < s.length && s[j].s === s[i].s) { if (s[j].y === 1) dtp++; else dfp++; j++; }
    auc += (dfp / neg) * ((tp + dtp / 2) / pos);
    tp += dtp; fp += dfp;
    pts.push([fp / neg, tp / pos]);
    i = j;
  }
  return { pts, auc, pos, neg };
}

export function create(root, api) {
  const ui = { hpa: DEFAULT_HPA, hour: null, boardMode: "live" };
  let state = null;
  let failed = false;
  let active = false;
  let geo = null;
  let geoLoading = null;
  let past = new Map(); // valid time -> latest-sealed record for past hours (the one graded)
  let sealedTimes = null; // records of the latest cycle, for the log's sealing times
  let proj = null;
  let mapSize = null;

  root.innerHTML = `
    <div class="map-pane">
      <svg class="map" role="img" aria-label="Map of sealed turbulence risk zones and pilot reports over the continental US"></svg>
      <div class="ladder" role="group" aria-label="Flight level"><svg></svg></div>
      <div class="map-legend turb-legend"></div>
      <div class="map-msg" hidden></div>
      <div class="scrubber" role="group" aria-label="Valid hour"><svg tabindex="0"></svg></div>
    </div>
    <aside class="side">
      <section class="lead-block">
        <p class="sentence">Reading the latest cycle…</p>
        <p class="sub"></p>
      </section>
      <section class="detail">
        <div class="panel-head"><h2>Bumpy reports caught, smooth ones flagged <span class="quiet">last 12 hours</span></h2></div>
        <div class="roc-grid">
          <div class="chart-wrap"><svg class="chart roc"></svg><div class="chart-note" hidden></div></div>
          <div class="roc-words"></div>
        </div>
      </section>
      <section class="board">
        <div class="panel-head">
          <h2>Scoreboard</h2>
          <div class="controls">
            <div class="seg mode-seg" role="group" aria-label="Results">
              <button data-mode="live">Live, sealed</button><button data-mode="backtest">Backtest</button>
            </div>
          </div>
        </div>
        <p class="sub board-note"></p>
        <table class="board"></table>
        <p class="sub board-foot"></p>
      </section>
    </aside>
    <div class="log">
      <div class="log-title"><strong>Log</strong><span class="log-sub">Lab formula, one record per hour; newest at right</span></div>
      <div class="log-row"></div>
    </div>`;

  const $ = (s) => root.querySelector(s);
  const mapPane = $(".map-pane");
  const svg = d3.select($("svg.map"));
  const ladderSvg = d3.select($(".ladder svg"));
  const scrubSvg = d3.select($(".scrubber svg"));
  const legendEl = $(".turb-legend");

  $(".mode-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    ui.boardMode = b.dataset.mode;
    renderBoard();
  });

  // ------------------------------------------------------------ data helpers

  const ok = () => state && !state.empty && state.grid;
  const levels = () => (ok() ? state.grid.levels_hpa : [150, 200, 250, 300, 400, 500]);
  const fls = () => (ok() ? state.grid.flight_levels : [446, 387, 340, 301, 236, 183]);
  const levelIndex = () => Math.max(0, levels().indexOf(ui.hpa));
  const reports = () => (ok() ? state.reports_12h || [] : []);

  // 13 hour slots: six graded hours back, the current hour, six sealed hours ahead
  function slots() {
    const now = Date.now();
    const h0 = Math.floor(now / H) * H;
    const fc = ok() ? (state.forecasts[LAB] || []) : [];
    const fcMap = new Map(fc.map((f) => [hourKey(f.valid_time), f]));
    const out = [];
    for (let k = -6; k <= 6; k++) {
      const t = h0 + k * H;
      const key = hourKey(t);
      const pr = past.get(key);
      const fr = fcMap.get(key);
      let rec = null;
      const fromPast = () => ({ risk: pr.prediction.risk, lead: leadMin(pr.target_time, pr.sealed_at), sealed: pr.sealed_at, seq: pr.seq, graded: !!pr.grade, gradedAt: pr.grade && pr.grade.graded_at, truthN: pr.grade && pr.grade.truth.n });
      const sealedF = fr && (fr.sealed_at || state.issued_at);
      if (t <= now && pr) rec = fromPast();
      else if (fr) rec = { risk: fr.risk, lead: fr.lead_minutes != null ? fr.lead_minutes : leadMin(fr.valid_time, sealedF), sealed: sealedF, seq: fr.seq, graded: false };
      else if (pr) rec = fromPast();
      out.push({ t, key, rec, future: t > now });
    }
    return out;
  }

  // positives graded against the record valid at this hour
  function hourOutcome(slot) {
    if (!slot.rec || !slot.rec.graded) return null;
    let hits = 0, total = 0, negs = 0;
    for (const r of reports()) {
      const g = r.graded && r.graded[LAB];
      if (!g || hourKey(g.valid_time) !== slot.key) continue;
      if (r.label === 1) { total++; if (g.in_top20) hits++; } else if (r.label === 0) negs++;
    }
    return { hits, total, negs };
  }

  function slotState(slot) {
    if (!slot.rec) return "none";
    if (!slot.rec.graded) return "open";
    const o = hourOutcome(slot);
    if (!o || !o.total) return "neutral";
    return o.hits / o.total >= 0.5 ? "good" : "miss";
  }

  function pickDefaultHour(sl) {
    const graded = sl.filter((s) => s.rec && s.rec.graded);
    if (graded.length) return graded[graded.length - 1].key;
    const fut = sl.find((s) => s.future && s.rec);
    if (fut) return fut.key;
    const any = sl.filter((s) => s.rec);
    return any.length ? any[any.length - 1].key : sl[6].key;
  }

  // The hour follows the data (latest graded, else next sealed) until someone picks one.
  function currentSlot(sl) {
    let s = ui.hourChosen ? sl.find((x) => x.key === ui.hour) : null;
    if (!s) { ui.hourChosen = false; ui.hour = pickDefaultHour(sl); s = sl.find((x) => x.key === ui.hour); }
    return s;
  }

  function hashParts() {
    return [String(ui.hpa), ui.hourChosen ? ui.hour : null];
  }

  // ------------------------------------------------------------ map base

  async function loadGeo() {
    if (geo || geoLoading) return geoLoading;
    geoLoading = (async () => {
      const [landTopo, us] = await Promise.all([
        fetch("geo/land-50m.json").then((r) => r.json()),
        fetch("geo/states-10m.json").then((r) => r.json()),
      ]);
      const fc = topojson.feature(landTopo, landTopo.objects.land);
      const polys = [];
      for (const feat of fc.features) {
        const g = feat.geometry;
        for (const poly of g.type === "Polygon" ? [g.coordinates] : g.coordinates) {
          let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
          for (const [x, y] of poly[0]) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
          if (x1 > -145 && x0 < -45 && y1 > 10 && y0 < 65) polys.push(poly);
        }
      }
      geo = {
        land: { type: "Feature", geometry: { type: "MultiPolygon", coordinates: polys } },
        coast: { type: "MultiLineString", coordinates: polys.flatMap((p) => p) },
        states: topojson.mesh(us, us.objects.states, (a, b) => a !== b),
        nation: topojson.mesh(us, us.objects.nation),
      };
    })();
    await geoLoading;
    if (active) drawMap();
  }

  function gridBoundary(step = 0.5) {
    const lats = ok() ? state.grid.lats : [25, 50];
    const lons = ok() ? state.grid.lons : [-125, -67.5];
    const la0 = lats[0], la1 = lats[lats.length - 1], lo0 = lons[0], lo1 = lons[lons.length - 1];
    const pts = [];
    for (let x = lo0; x <= lo1; x += step) pts.push([x, la0]);
    for (let y = la0; y <= la1; y += step) pts.push([lo1, y]);
    for (let x = lo1; x >= lo0; x -= step) pts.push([x, la1]);
    for (let y = la1; y >= la0; y -= step) pts.push([lo0, y]);
    return pts;
  }

  function layout() {
    const { w, h } = boxSize(mapPane, 320, 320);
    const narrow = w < 640;
    const left = narrow ? 102 : 124;
    const scrubH = 92;
    const legendH = legendEl.getBoundingClientRect().height || 60;
    return { w, h, left, top: 14, right: w - 14, bottom: h - scrubH - legendH - 20, scrubH, legendH };
  }

  function drawMap() {
    if (!active) return;
    renderLegend();
    const L = layout();
    mapSize = { w: L.w, h: L.h };
    legendEl.style.left = `${L.left}px`;
    legendEl.style.right = "14px";
    legendEl.style.bottom = `${L.scrubH + 10}px`;
    svg.attr("viewBox", `0 0 ${L.w} ${L.h}`);
    proj = d3.geoConicEqualArea().parallels([29.5, 45.5]).rotate([96, 0])
      .fitExtent([[L.left, L.top], [L.right, Math.max(L.top + 120, L.bottom)]], { type: "MultiPoint", coordinates: gridBoundary(1) })
      .clipExtent([[0, 0], [L.w, L.h]]);
    const path = d3.geoPath(proj);

    svg.selectAll("*").remove();
    const defs = svg.append("defs");
    const pat = defs.append("pattern").attr("id", "hatch").attr("patternUnits", "userSpaceOnUse")
      .attr("width", 5).attr("height", 5).attr("patternTransform", "rotate(45)");
    pat.append("rect").attr("width", 5).attr("height", 5).attr("fill", "rgba(180,35,127,0.10)");
    pat.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5).attr("stroke", "#B4237F").attr("stroke-width", 1.3);
    const bpts = gridBoundary(0.5).map((p) => proj(p));
    defs.append("clipPath").attr("id", "grid-clip").append("path").attr("d", `M${bpts.join("L")}Z`);

    svg.append("rect").attr("class", "ocean").attr("width", L.w).attr("height", L.h);
    svg.append("path").attr("class", "graticule").attr("d", path(d3.geoGraticule().step([5, 5]).extent([[-140, 15], [-50, 60]])()));
    if (geo) {
      svg.append("path").attr("class", "us-land").attr("d", path(geo.land));
      svg.append("path").attr("class", "us-coast").attr("d", path(geo.coast));
      svg.append("path").attr("class", "states").attr("d", path(geo.states));
      svg.append("path").attr("class", "nation").attr("d", path(geo.nation));
    }
    const names = svg.append("g");
    for (const [txt, lon, lat, size] of [["Pacific Ocean", -121.5, 30.5, 20], ["Gulf of Mexico", -90.5, 25.6, 18], ["Atlantic Ocean", -70.5, 34.5, 20]]) {
      const p = proj([lon, lat]);
      if (p && p[0] > L.left + 40 && p[0] < L.w - 40 && p[1] < L.bottom) {
        names.append("text").attr("class", "water-name").attr("x", p[0]).attr("y", p[1]).attr("font-size", size)
          .attr("text-anchor", "middle").text(txt);
      }
    }
    svg.append("path").attr("class", "domain-edge").attr("d", `M${bpts.join("L")}Z`);
    svg.append("g").attr("class", "field").attr("clip-path", "url(#grid-clip)");
    svg.append("g").attr("class", "jetg");
    svg.append("g").attr("class", "reps");
    renderDynamic();
  }

  // ------------------------------------------------------------ map: field, jet, reports

  function renderDynamic() {
    if (!active || !proj) return;
    const sl = slots();
    const slot = currentSlot(sl);
    renderField(slot);
    renderJet(slot);
    renderReports(slot);
    renderLadder(slot);
    renderScrubber(sl, slot);
    const msg = $(".map-msg");
    if (!ok()) {
      msg.hidden = false;
      msg.textContent = failed ? "Cannot reach the lab server. Retrying every 30 seconds." : !state ? "Reading the latest cycle…" : "No sealed risk maps yet: the live job has not written a state file.";
    } else if (!slot.rec) {
      msg.hidden = false;
      msg.textContent = `Nothing was sealed for ${fmtWhen(slot.t)}.`;
    } else msg.hidden = true;
  }

  function renderField(slot) {
    const g = svg.select("g.field");
    const li = levelIndex();
    if (!slot || !slot.rec || !ok()) { g.selectAll("*").remove(); return; }
    const grid = slot.rec.risk[li];
    const lats = state.grid.lats, lons = state.grid.lons;
    const flat = grid.flat();
    const t80 = quantileThreshold(flat, TOP);
    const t60 = quantileThreshold(flat, WASH);
    const U = 4;
    const nx = (lons.length - 1) * U + 1, ny = (lats.length - 1) * U + 1;
    const lo = d3.min(flat.filter((v) => v != null)) - 1;
    const val = (j, i) => { const v = grid[j][i]; return v == null ? lo : v; };
    const vals = new Float64Array(nx * ny);
    for (let y = 0; y < ny; y++) {
      const fy = y / U, j0 = Math.min(Math.floor(fy), lats.length - 2), ty = fy - j0;
      for (let x = 0; x < nx; x++) {
        const fx = x / U, i0 = Math.min(Math.floor(fx), lons.length - 2), tx = fx - i0;
        vals[y * nx + x] = (1 - ty) * ((1 - tx) * val(j0, i0) + tx * val(j0, i0 + 1)) + ty * ((1 - tx) * val(j0 + 1, i0) + tx * val(j0 + 1, i0 + 1));
      }
    }
    const step = (lons[1] - lons[0]) / U;
    const stepY = (lats[1] - lats[0]) / U;
    const transform = d3.geoTransform({
      point(x, y) {
        const p = proj([lons[0] + (x - 0.5) * step, lats[0] + (y - 0.5) * stepY]);
        if (p) this.stream.point(p[0], p[1]);
      },
    });
    const gp = d3.geoPath(transform);
    const contours = d3.contours().size([nx, ny]).thresholds([t60, t80].filter((t) => t != null))(vals);
    const data = contours.map((c, i) => ({ c, cls: i === 0 ? "zone-wash" : "zone-top" }));
    g.selectAll("path").data(data, (d) => d.cls).join("path").attr("class", (d) => d.cls).attr("d", (d) => gp(d.c));
    slot.thresholds = { t60, t80 };
  }

  function renderJet(slot) {
    const g = svg.select("g.jetg");
    const jet = ok() && slot ? (state.jet_250hpa || []).find((j) => hourKey(j.valid_time) === slot.key) : null;
    if (!jet) { g.selectAll("*").remove(); g.attr("data-key", null); return; }
    if (g.attr("data-key") === jet.valid_time && g.attr("data-w") === String(mapSize.w)) return;
    g.attr("data-key", jet.valid_time).attr("data-w", String(mapSize.w));
    const lats = state.grid.lats, lons = state.grid.lons;
    const at = (lat, lon) => {
      const fy = (lat - lats[0]) / (lats[1] - lats[0]), fx = (lon - lons[0]) / (lons[1] - lons[0]);
      if (fy < 0 || fx < 0 || fy > lats.length - 1 || fx > lons.length - 1) return null;
      const j0 = Math.min(Math.floor(fy), lats.length - 2), i0 = Math.min(Math.floor(fx), lons.length - 2);
      const ty = fy - j0, tx = fx - i0;
      const uv = (j, i) => {
        const s = jet.speed_ms[j][i], d = jet.direction_deg[j][i];
        if (s == null || d == null) return [0, 0];
        const r = d * Math.PI / 180;
        return [-s * Math.sin(r), -s * Math.cos(r)];
      };
      const a = uv(j0, i0), b = uv(j0, i0 + 1), c = uv(j0 + 1, i0), e = uv(j0 + 1, i0 + 1);
      const u = (1 - ty) * ((1 - tx) * a[0] + tx * b[0]) + ty * ((1 - tx) * c[0] + tx * e[0]);
      const v = (1 - ty) * ((1 - tx) * a[1] + tx * b[1]) + ty * ((1 - tx) * c[1] + tx * e[1]);
      return [u, v];
    };
    const MIN = 30;
    const cell = 2.4;
    const occ = new Set();
    const cellKey = (lat, lon) => `${Math.floor(lat / cell)},${Math.floor(lon / cell)}`;
    const seeds = [];
    for (let j = 0; j < lats.length; j++) for (let i = 0; i < lons.length; i++) {
      const s = jet.speed_ms[j][i];
      if (s != null && s >= MIN + 5) seeds.push({ lat: lats[j], lon: lons[i], s });
    }
    seeds.sort((a, b) => b.s - a.s);
    const lines = [];
    const h = 0.45;
    for (const sd of seeds) {
      if (occ.has(cellKey(sd.lat, sd.lon))) continue;
      const trace = (dir) => {
        const pts = [];
        let lat = sd.lat, lon = sd.lon;
        const own = new Set();
        for (let n = 0; n < 60; n++) {
          const w = at(lat, lon);
          if (!w) break;
          const sp = Math.hypot(w[0], w[1]);
          if (sp < MIN) break;
          const k = cellKey(lat, lon);
          if (occ.has(k) && !own.has(k)) break;
          own.add(k);
          pts.push([lon, lat, sp]);
          lat += dir * h * w[1] / sp;
          lon += dir * h * w[0] / sp / Math.cos(lat * Math.PI / 180);
        }
        return { pts, own };
      };
      const fwd = trace(1), back = trace(-1);
      const pts = [...back.pts.slice(1).reverse(), ...fwd.pts];
      if (pts.length < 6) continue;
      for (const k of [...fwd.own, ...back.own]) occ.add(k);
      lines.push(pts);
    }
    const line = d3.line().curve(d3.curveBasis);
    const wScale = d3.scaleLinear().domain([MIN, 80]).range([0.6, 1.8]).clamp(true);
    g.selectAll("*").remove();
    for (const pts of lines) {
      const xy = pts.map((p) => proj([p[0], p[1]])).filter(Boolean);
      const sp = d3.mean(pts, (p) => p[2]);
      g.append("path").attr("class", "jet").attr("stroke-width", wScale(sp)).attr("stroke-opacity", 0.6).attr("d", line(xy));
      const a = xy[xy.length - 2], b = xy[xy.length - 1];
      if (a && b) {
        const ang = Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;
        g.append("path").attr("class", "jet-head").attr("fill-opacity", 0.7)
          .attr("transform", `translate(${b[0]},${b[1]}) rotate(${ang})`).attr("d", "M1.5,0L-4,-2.2L-4,2.2Z");
      }
    }
  }

  function reportClass(r, slot) {
    if (r.label === 0) return "smooth";
    const g = r.graded && r.graded[LAB];
    if (g && hourKey(g.valid_time) === slot.key) return g.in_top20 ? "hit" : "miss";
    return "wait";
  }

  function renderReports(slot) {
    const g = svg.select("g.reps");
    if (!ok() || !slot) { g.selectAll("*").remove(); return; }
    const li = levelIndex();
    const lv = levels();
    const list = reports().filter((r) => r.label != null && Math.abs(new Date(r.time) - slot.t) <= 30 * 60e3 &&
      nearestLevel(r.fl, lv) === li);
    list.sort((a, b) => (a.label - b.label));
    const sel = g.selectAll("g.rep").data(list, (r) => `${r.time}|${r.lat}|${r.lon}|${r.fl}`).join((en) => {
      const e = en.append("g").attr("class", "rep");
      e.append("path").attr("class", "shape");
      e.append("path").attr("class", "bars");
      return e;
    });
    sel.attr("transform", (r) => { const p = proj([r.lon, r.lat]); return p ? `translate(${p[0]},${p[1]})` : "translate(-99,-99)"; })
      .each(function (r) {
        const c = reportClass(r, slot);
        const e = d3.select(this);
        if (c === "smooth") {
          e.select("path.shape").attr("class", "shape rep-smooth").attr("d", d3.symbol(d3.symbolCircle, 22)());
          e.select("path.bars").attr("d", null);
        } else {
          e.select("path.shape").attr("class", `shape rep-${c}`)
            .attr("d", "M-6,2.5L0,-5L6,2.5L3.4,2.5L0,-1.4L-3.4,2.5Z");
          e.select("path.bars").attr("class", `bars rep-bar-${c}`)
            .attr("d", r.code >= 6 ? "M-6,5H6M-6,8H6" : "M-6,5.2H6");
        }
      })
      .on("mousemove", (e, r) => showTip(reportTip(r, slot), e))
      .on("mouseleave", hideTip);
    g.selectAll("g.rep").filter((r) => reportClass(r, slot) !== "smooth").raise();
  }

  function reportTip(r, slot) {
    let h = `<b>${esc(CODE_WORDS[r.code] || "report")}</b>, ${esc(flName(r.fl))}, ${esc(fmtWhen(r.time))}<br>`;
    h += `<span class="q">${esc(r.raw)}</span><br>`;
    if (r.label === 0) h += "Smooth air counts against a zone only if it falls inside one.<br>";
    const g = r.graded || {};
    let any = false;
    for (const m of ROC_MODELS) {
      const x = g[m];
      if (!x || hourKey(x.valid_time) !== slot.key) continue;
      any = true;
      const where = x.in_top20 ? "inside the sealed zone" : "outside the sealed zone";
      const cls = m === LAB ? "s" : "o";
      h += `<span class="${cls}">${esc(LABEL[m])}</span>: ${where}, higher than ${x.percentile.toFixed(0)}% of the map at this level (sealed ${esc(leadText(x.lead_minutes != null ? x.lead_minutes : x.lead_hours * 60))} ahead)<br>`;
    }
    if (!any) h += `<span class="q">Not graded yet: grading runs ${GRADE_DELAY_MIN} minutes after the hour.</span>`;
    return h;
  }

  // ------------------------------------------------------------ ladder

  function renderLadder(slot) {
    const el = $(".ladder");
    const { width: w, height: h } = el.getBoundingClientRect();
    if (!w || !h) return;
    ladderSvg.attr("viewBox", `0 0 ${w} ${h}`);
    const y = d3.scaleLinear().domain([160, 470]).range([h - 12, 30]);
    if (ladderSvg.select("g.tape").empty()) {
      ladderSvg.append("text").attr("class", "ladder-title").attr("x", 8).attr("y", 16).text("Flight level");
      ladderSvg.append("g").attr("class", "tape");
      ladderSvg.append("g").attr("class", "lvls");
    }
    ladderSvg.select(".ladder-title").attr("x", 8);
    const tape = ladderSvg.select("g.tape");
    tape.selectAll("*").remove();
    for (let fl = 170; fl <= 460; fl += 10) {
      const major = fl % 50 === 0;
      tape.append("line").attr("class", "tape-tick").attr("x1", w - (major ? 14 : 8)).attr("x2", w).attr("y1", y(fl)).attr("y2", y(fl));
    }
    const lv = levels(), fl = fls();
    const counts = lv.map(() => 0);
    if (slot && ok()) {
      for (const r of reports()) {
        if (r.label == null || Math.abs(new Date(r.time) - slot.t) > 30 * 60e3) continue;
        counts[nearestLevel(r.fl, lv)]++;
      }
    }
    const data = lv.map((p, i) => ({ p, fl: fl[i], i, n: counts[i] }));
    const g = ladderSvg.select("g.lvls").selectAll("g.lvl").data(data, (d) => d.p).join((en) => {
      const e = en.append("g").attr("class", "lvl").attr("tabindex", 0).attr("role", "button");
      e.append("rect").attr("class", "box");
      e.append("text").attr("class", "fl");
      e.append("text").attr("class", "hpa");
      return e;
    });
    g.classed("on", (d) => d.p === ui.hpa)
      .attr("aria-pressed", (d) => String(d.p === ui.hpa))
      .attr("aria-label", (d) => `${flName(d.fl)}, ${d.p} hectopascals, ${d.n} reports this hour`)
      .attr("transform", (d) => `translate(0,${y(d.fl)})`)
      .each(function (d) {
        const e = d3.select(this);
        e.select("rect.box").attr("x", 3).attr("y", -15).attr("width", w - 22).attr("height", 30).attr("rx", 2);
        e.select("text.fl").attr("x", 9).attr("y", -1).text(flName(d.fl));
        e.select("text.hpa").attr("x", 9).attr("y", 11).text(`${d.p} hPa${d.n ? ` · ${d.n}` : ""}`);
      })
      .on("click", (e, d) => setLevel(d.p))
      .on("keydown", (e, d) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setLevel(d.p); }
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const i = lv.indexOf(ui.hpa) + (e.key === "ArrowUp" ? -1 : 1);
          if (i >= 0 && i < lv.length) setLevel(lv[i], true);
        }
      });
  }

  function setLevel(p, focus) {
    ui.hpa = p;
    api.setHash(hashParts());
    renderDynamic();
    if (focus) {
      const n = [...root.querySelectorAll(".ladder g.lvl")].find((x) => x.classList.contains("on"));
      if (n) n.focus();
    }
  }

  // ------------------------------------------------------------ scrubber

  function renderScrubber(sl, slot) {
    const el = $(".scrubber");
    const { width: w, height: h } = el.getBoundingClientRect();
    if (!w || !h) return;
    scrubSvg.attr("viewBox", `0 0 ${w} ${h}`);
    const x0 = 24, x1 = w - 24;
    const x = d3.scaleTime().domain([new Date(sl[0].t - H / 2), new Date(sl[sl.length - 1].t + H / 2)]).range([x0, x1]);
    const yD = 38, yAxis = 48;
    if (scrubSvg.select("g.axis").empty()) {
      scrubSvg.append("text").attr("class", "scrub-caption strong cap1").attr("x", x0).attr("y", 17);
      scrubSvg.append("g").attr("class", "axis");
      scrubSvg.append("g").attr("class", "hours");
      scrubSvg.append("g").attr("class", "ticks");
      scrubSvg.append("g").attr("class", "seals");
      scrubSvg.append("g").attr("class", "nowg");
    }
    // caption
    let cap = `${fmtWhen(slot.t)}`;
    if (slot.rec) {
      cap += ` · sealed ${leadText(slot.rec.lead)} ahead, at ${fmtTime(slot.rec.sealed)}`;
      if (slot.rec.graded) {
        const o = hourOutcome(slot);
        cap += o && o.total ? ` · ${o.hits} of ${o.total} moderate-or-worse reports inside the zones` : " · graded, no moderate-or-worse reports";
      } else if (!slot.future) cap += ` · graded from ${fmtTime(slot.t + GRADE_DELAY_MIN * 60e3)}`;
      else cap += " · waiting";
    } else cap += " · nothing sealed for this hour";
    scrubSvg.select(".cap1").text(cap);

    const ax = scrubSvg.select("g.axis");
    ax.selectAll("*").remove();
    ax.append("line").attr("class", "gridline").attr("x1", x0).attr("x2", x1).attr("y1", yAxis).attr("y2", yAxis);
    ax.append("text").attr("class", "scrub-caption").attr("x", x1).attr("y", 17).attr("text-anchor", "end")
      .text(w > 760 ? "six graded hours back, six sealed hours ahead" : "");

    const cw = (x1 - x0) / sl.length;
    const every = cw < 44 ? 2 : 1;
    const hours = scrubSvg.select("g.hours").selectAll("g.hour").data(sl, (s) => s.key).join((en) => {
      const e = en.append("g").attr("class", "hour");
      e.append("rect").attr("class", "hitbox");
      e.append("text").attr("class", "hour-label");
      return e;
    });
    hours.classed("on", (s) => s.key === slot.key)
      .each(function (s, i) {
        const e = d3.select(this);
        e.select("rect.hitbox").attr("x", x(s.t) - cw / 2).attr("y", 24).attr("width", cw).attr("height", h - 44).attr("rx", 2);
        e.select("text.hour-label").attr("x", x(s.t)).attr("y", h - 8)
          .text(i % every === 0 || s.key === slot.key ? fmtTime(s.t) : "");
      })
      .on("click", (e, s) => setHour(s.key));

    const marks = sl.map((s) => ({ key: s.key, x: x(s.t), y: yD, state: slotState(s), r: 5.5 }));
    drawSeals(scrubSvg.select("g.seals"), marks, { r: 5.5 })
      .style("cursor", "pointer")
      .on("click", (e, d) => setHour(d.key))
      .on("mousemove", (e, d) => showTip(slotTip(sl.find((s) => s.key === d.key)), e))
      .on("mouseleave", hideTip);
    const ticks = sl.filter((s) => s.rec && s.rec.sealed).map((s) => ({
      key: s.key,
      x0: Math.max(x0, x(new Date(s.rec.sealed))),
      x1: x(s.t) - 6,
      y: yAxis + 4 + Math.min(6, Math.max(0, (s.rec.lead || 0) / 60)) * 2.6,
    })).filter((d) => d.x1 > d.x0);
    drawTicks(scrubSvg.select("g.ticks"), ticks);

    const nowg = scrubSvg.select("g.nowg");
    nowg.selectAll("*").remove();
    const nx = x(new Date());
    nowg.append("line").attr("class", "now-rule").attr("x1", nx).attr("x2", nx).attr("y1", 24).attr("y2", yAxis + 20);
    nowg.append("text").attr("class", "now-label").attr("x", nx - 3).attr("y", 30).attr("text-anchor", "end").text("now");
  }

  function slotTip(s) {
    if (!s) return "";
    let h = `<b>Valid ${esc(fmtWhen(s.t))}</b><br>`;
    if (!s.rec) return h + `<span class="q">Nothing was sealed for this hour.</span>`;
    h += `<span class="s">Sealed ${esc(leadText(s.rec.lead))} ahead, at ${esc(fmtWhen(s.rec.sealed))}</span><br>`;
    if (s.rec.graded) {
      const o = hourOutcome(s);
      h += o && o.total ? `${o.hits} of ${o.total} moderate-or-worse reports fell inside the zones` : "Graded; no moderate-or-worse reports in the window";
      if (s.rec.gradedAt) h += `<br><span class="q">Graded ${esc(fmtWhen(s.rec.gradedAt))} with ${fmtInt(s.rec.truthN)} reports</span>`;
    } else h += `<span class="q">${s.future ? "Waiting for the hour" : `Graded from ${esc(fmtTime(s.t + GRADE_DELAY_MIN * 60e3))}`}</span>`;
    return h;
  }

  function setHour(key) {
    ui.hour = key;
    ui.hourChosen = true;
    api.setHash(hashParts());
    renderDynamic();
  }

  scrubSvg.on("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const sl = slots();
    const i = sl.findIndex((s) => s.key === ui.hour);
    const j = Math.max(0, Math.min(sl.length - 1, i + (e.key === "ArrowRight" ? 1 : -1)));
    setHour(sl[j].key);
  });

  function renderLegend() {
    legendEl.innerHTML = `
      <div class="row" style="flex-wrap:wrap;gap:4px 16px">
        <span class="row"><svg width="22" height="14"><rect width="22" height="14" fill="url(#hatch)" stroke="#B4237F"/></svg>Sealed zone: top fifth of risk at this level</span>
        <span class="row"><svg width="22" height="14"><rect width="22" height="14" fill="rgba(180,35,127,0.13)"/></svg>Next fifth</span>
        <span class="row"><svg width="16" height="16" viewBox="-8 -7 16 16"><path d="M-6,2.5L0,-5L6,2.5L3.4,2.5L0,-1.4L-3.4,2.5Z" fill="#16283A" stroke="#16283A"/><path d="M-6,5.2H6" stroke="#16283A" stroke-width="1.4"/></svg>Moderate-or-worse report inside a zone</span>
        <span class="row"><svg width="16" height="16" viewBox="-8 -7 16 16"><path d="M-6,2.5L0,-5L6,2.5L3.4,2.5L0,-1.4L-3.4,2.5Z" fill="#F4F9FB" stroke="#C9461B" stroke-width="1.4"/><path d="M-6,5.2H6" stroke="#C9461B" stroke-width="1.4"/></svg>Outside</span>
        <span class="row"><svg width="16" height="16" viewBox="-8 -7 16 16"><path d="M-6,2.5L0,-5L6,2.5L3.4,2.5L0,-1.4L-3.4,2.5Z" fill="#F4F9FB" stroke="#5B6B7A" stroke-width="1.2"/><path d="M-6,5.2H6" stroke="#5B6B7A" stroke-width="1.2"/></svg>Not graded yet</span>
        <span class="row"><svg width="10" height="10" viewBox="-5 -5 10 10"><circle r="2.6" fill="#F4F9FB" stroke="#16283A"/></svg>Smooth or light</span>
        <span class="row"><svg width="22" height="10"><path d="M1,5H17" stroke="#2A6A9E" stroke-width="1.5"/><path d="M21,5L14,2L14,8Z" fill="#2A6A9E"/></svg>Jet stream, 250 hPa</span>
      </div>`;
    legendEl.style.maxWidth = "none";
  }

  // ------------------------------------------------------------ side: sentence

  function renderSentence() {
    const sEl = $(".sentence");
    const sub = $(".lead-block .sub");
    if (!ok()) {
      sEl.innerHTML = failed && !state
        ? "Cannot reach the lab server."
        : !state ? "Reading the latest cycle…"
        : `No forecasts sealed yet. Start the live job: <code>.venv/bin/python -m lab.tasks.turb.live run --interval 1800</code>`;
      sub.textContent = failed ? "The dashboard retries every 30 seconds." : "";
      return;
    }
    const now = Date.now();
    const reps = reports();
    let text = null;
    for (const win of [3, 6, 12]) {
      const P = reps.filter((r) => r.label === 1 && r.graded && r.graded[LAB] && now - new Date(r.time) <= win * H);
      if (P.length >= 10 || (win === 12 && P.length)) {
        const hits = P.filter((r) => r.graded[LAB].in_top20).length;
        const mins = P.map((r) => { const g = r.graded[LAB]; return g.lead_minutes != null ? g.lead_minutes : g.lead_hours * 60; }).filter((v) => v != null && isFinite(v));
        const a = leadText(d3.min(mins)), b = leadText(d3.max(mins));
        const long = (t) => t.replace(/ min$/, " minutes").replace(/ h$/, " hours");
        const leadTxt = a === b ? long(a) : a.slice(-3) === b.slice(-3) ? `${a.split(" ")[0]} to ${long(b)}` : `${long(a)} to ${long(b)}`;
        text = `In the last ${numberWord(win)} hours, <span class="num">${hits} of ${P.length}</span> moderate-or-worse reports fell inside zones the lab sealed <span class="sealed">${leadTxt}</span> earlier.`;
        const others = [];
        for (const m of ["Ellrod1", "GAirmet"]) {
          const Q = P.filter((r) => r.graded[m]);
          if (Q.length === P.length) others.push(`the ${m === "GAirmet" ? "official G-AIRMET" : "Ellrod index"} caught <span class="official num">${Q.filter((r) => r.graded[m].in_top20).length}</span>`);
        }
        if (others.length) text += ` Over the same reports, ${others.join(" and ")}.`;
        break;
      }
    }
    const fc = state.forecasts[LAB] || [];
    const lastValid = fc.length ? fc[fc.length - 1].valid_time : null;
    const firstValid = fc.length ? fc[0].valid_time : null;
    if (!text) {
      const pos = reps.filter((r) => r.label === 1).length;
      text = lastValid
        ? `The lab has sealed turbulence zones for every hour through <span class="sealed">${fmtWhen(lastValid)}</span>. Pilots filed <span class="num">${pos}</span> moderate-or-worse reports in the last 12 hours.`
        : `Pilots filed <span class="num">${pos}</span> moderate-or-worse reports in the last 12 hours; no risk map is sealed right now.`;
    }
    sEl.innerHTML = text;
    const graded = (state.ledger && state.ledger.graded) || 0;
    if (!graded) {
      const first = [...past.values()].map((r) => r.target_time).concat(firstValid ? [firstValid] : []).sort()[0];
      sub.textContent = first
        ? `A zone is the top fifth of the sealed risk map at a report's flight level. The first sealed hour, ${fmtWhen(first)}, is graded after ${fmtWhen(new Date(new Date(first).getTime() + GRADE_DELAY_MIN * 60e3))}.`
        : "A zone is the top fifth of the sealed risk map at a report's flight level.";
    } else {
      sub.textContent = "A zone is the top fifth of the sealed risk map at a report's flight level. Light-to-moderate reports are not scored.";
    }
  }

  // ------------------------------------------------------------ side: ROC

  function renderRoc() {
    const svgEl = $("svg.roc");
    const note = $(".detail .chart-note");
    const words = $(".roc-words");
    const size = Math.max(150, Math.min(window.innerHeight < 820 ? 170 : 230, Math.floor(svgEl.parentNode.getBoundingClientRect().width)));
    const m = { l: 42, r: 8, t: 8, b: 32 };
    const s = d3.select(svgEl).attr("viewBox", `0 0 ${size} ${size}`).attr("height", size);
    const x = d3.scaleLinear().domain([0, 1]).range([m.l, size - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([size - m.b, m.t]);
    if (s.select("g.frame").empty()) {
      const f = s.append("g").attr("class", "frame");
      s.append("g").attr("class", "curves");
      f.selectAll("line.gx").data([0, 0.5, 1]).join("line").attr("class", "gridline gx");
      f.selectAll("line.gy").data([0, 0.5, 1]).join("line").attr("class", "gridline gy");
      f.append("line").attr("class", "roc-diag");
      f.selectAll("text.tx").data([0, 0.5, 1]).join("text").attr("class", "tick-label tx").attr("text-anchor", "middle");
      f.selectAll("text.ty").data([0, 0.5, 1]).join("text").attr("class", "tick-label ty").attr("text-anchor", "end");
      f.append("text").attr("class", "tick-label xt").attr("text-anchor", "middle").text("smooth reports flagged");
      f.append("text").attr("class", "tick-label yt").attr("text-anchor", "middle").text("bumpy reports caught");
    }
    const f = s.select("g.frame");
    f.selectAll("line.gx").attr("x1", (d) => x(d)).attr("x2", (d) => x(d)).attr("y1", y(0)).attr("y2", y(1));
    f.selectAll("line.gy").attr("y1", (d) => y(d)).attr("y2", (d) => y(d)).attr("x1", x(0)).attr("x2", x(1));
    f.select("line.roc-diag").attr("x1", x(0)).attr("y1", y(0)).attr("x2", x(1)).attr("y2", y(1));
    f.selectAll("text.tx").attr("x", (d) => x(d)).attr("y", y(0) + 13).text((d) => d);
    f.selectAll("text.ty").attr("x", x(0) - 4).attr("y", (d) => y(d) + 4).text((d) => d);
    f.select("text.xt").attr("x", (x(0) + x(1)) / 2).attr("y", size - 3);
    f.select("text.yt").attr("transform", `translate(${11},${(y(0) + y(1)) / 2}) rotate(-90)`);

    const curves = [];
    if (ok()) {
      for (const mdl of ROC_MODELS) {
        const pairs = reports().filter((r) => r.label != null && r.graded && r.graded[mdl])
          .map((r) => ({ s: r.graded[mdl].risk, y: r.label }));
        const rc = roc(pairs);
        if (rc) curves.push({ mdl, ...rc });
      }
    }
    const cls = { SeedBlend: "lab", Ellrod1: "ref", GAirmet: "ref2" };
    const line = d3.line().x((p) => x(p[0])).y((p) => y(p[1]));
    s.select("g.curves").selectAll("path").data(curves, (c) => c.mdl).join("path")
      .attr("class", (c) => `roc-curve ${cls[c.mdl]}`).attr("d", (c) => line(c.pts));
    s.select("g.curves").selectAll("path").filter((c) => c.mdl === LAB).raise();

    if (curves.length) {
      note.hidden = true;
      const lab = curves.find((c) => c.mdl === LAB);
      const n = lab ? lab.pos + lab.neg : curves[0].pos + curves[0].neg;
      const pos = lab ? lab.pos : curves[0].pos;
      words.innerHTML = curves.map((c) => {
        const k = c.mdl === LAB ? "lab" : "ref";
        return `<p><span class="${k}">${esc(LABEL[c.mdl])} ${c.auc.toFixed(2)}.</span> It ranks a bumpy report above a smooth one ${Math.round(c.auc * 100)} times in 100.</p>`;
      }).join("") + `<p class="sub">From ${fmtInt(n)} graded reports in the last 12 hours, ${fmtInt(pos)} of them moderate or worse; light-to-moderate reports are left out. 50 in 100 is a coin toss.</p>`;
    } else {
      note.hidden = false;
      note.textContent = ok() ? "No graded reports yet." : "";
      const bt = ok() && state.backtest;
      let html = `<p>The curves fill in once sealed hours are graded against pilot reports.</p>`;
      if (bt && bt.table) {
        const rows = bt.table.filter((r) => r.model === LAB || r.model === "Ellrod1");
        html += `<p class="sub">Meanwhile, the ${bt.days}-day backtest on hindsight fields (a fair comparison of the indices, not a test of forecasting): ` +
          rows.map((r) => `<span class="${r.model === LAB ? "lab" : "ref"}">${esc(LABEL[r.model])} ${r.auc.toFixed(3)}</span> (95% interval ${r.low.toFixed(3)} to ${r.high.toFixed(3)})`).join(", ") + ".</p>";
      }
      words.innerHTML = html;
    }
  }

  // ------------------------------------------------------------ side: scoreboard

  function renderBoard() {
    const table = $("table.board");
    const note = $(".board-note");
    const foot = $(".board-foot");
    $(".mode-seg").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === ui.boardMode)));
    if (!ok()) { table.innerHTML = ""; note.textContent = "No results yet."; foot.textContent = ""; return; }
    let rows, models, cell;
    if (ui.boardMode === "live") {
      const sb = state.scoreboard || {};
      models = BOARD_ORDER.filter((m) => sb[m]);
      if (!models.length) models = ["SeedBlend", "Ellrod1", "GAirmet"];
      rows = ["1", "2", "3", "4", "5", "6"];
      cell = (m, l) => { const c = sb[m] && sb[m][`at_least_${l}h`]; return c && c.auc != null ? { v: c.auc, n: c.n } : null; };
      note.textContent = Object.keys(sb).length
        ? "Area under the curve for forecasts sealed at least this long before the hour: 1 is perfect, 0.5 a coin toss. Small figures count graded report layers."
        : "Nothing graded yet: the table fills in once the first sealed hour is graded.";
      foot.textContent = "";
    } else {
      const bt = state.backtest;
      const tb = (bt && bt.table) || [];
      models = BOARD_ORDER.filter((m) => tb.some((r) => r.model === m));
      rows = ["bt"];
      cell = (m) => { const r = tb.find((x) => x.model === m); return r ? { v: r.auc, n: r.n } : null; };
      note.textContent = bt
        ? `Backtest over ${bt.days} days on hindsight fields: a fair comparison of the indices, not a test of forecasting. Tested on ${fmtInt(bt.test.n)} report layers from ${new Date(bt.test.from).toLocaleDateString()} to ${new Date(bt.test.to).toLocaleDateString()}.`
        : "No backtest has been run yet.";
      const pr = bt && bt.paired_SeedBlend_vs_Ellrod1;
      foot.textContent = pr && pr.verdict ? `${pr.verdict} ${(bt.caveats || [])[0] || ""}` : pr
        ? `Paired against the Ellrod index, the lab formula's area under the curve is ${pr.diff >= 0 ? "higher" : "lower"} by ${Math.abs(pr.diff).toFixed(4)} (95% interval ${sgn(pr.low)} to ${sgn(pr.high)}); it came out ahead in ${Math.round(pr.share_a_better * 100)}% of ${fmtInt(pr.n_boot)} resamples.`
        : "";
    }
    let html = `<thead><tr><th>${ui.boardMode === "live" ? "Sealed ahead" : "Fields"}</th>${models.map((m) =>
      `<th class="${OFFICIAL.has(m) ? "official" : ""}${m === LAB ? "lab" : ""}">${esc(LABEL[m] || m)}</th>`).join("")}</tr></thead><tbody>`;
    for (const l of rows) {
      const cells = models.map((m) => cell(m, l));
      const vals = cells.filter(Boolean).map((c) => c.v);
      const best = vals.length > 1 ? Math.max(...vals) : null;
      const name = l === "bt" ? "Hindsight" : `${l} h or more`;
      html += `<tr><th>${name}</th>` + cells.map((c, i) => {
        const cls = [models[i] === LAB ? "lab" : ""];
        if (!c) return `<td class="dash ${cls.join(" ")}"><span class="v">–</span><span class="n"></span></td>`;
        if (best != null && c.v === best) cls.push("best");
        return `<td class="${cls.join(" ")}"><span class="v">${c.v.toFixed(ui.boardMode === "live" ? 2 : 3)}</span><span class="n">${fmtInt(c.n)}</span></td>`;
      }).join("") + "</tr>";
    }
    table.innerHTML = html + "</tbody>";
  }

  // ------------------------------------------------------------ log

  function peakText(risk, li) {
    if (!risk || !ok()) return "";
    const g = risk[li];
    let best = null;
    g.forEach((row, j) => row.forEach((v, i) => { if (v != null && (!best || v > best.v)) best = { v, j, i }; }));
    if (!best) return "";
    const lat = state.grid.lats[best.j], lon = state.grid.lons[best.i];
    return `peak ${Math.round(best.v * 100)}% near ${lat}°N ${Math.abs(lon)}°W`;
  }

  function renderLog() {
    const row = $(".log-row");
    if (!state) {
      row.innerHTML = `<div class="log-empty">${failed ? "Cannot reach the lab server." : "Reading the ledger…"}</div>`;
      return;
    }
    if (!ok()) {
      row.innerHTML = `<div class="log-empty">No forecasts sealed yet. Start the live job: <code>.venv/bin/python -m lab.tasks.turb.live run --interval 1800</code></div>`;
      return;
    }
    const li = levels().indexOf(250) >= 0 ? levels().indexOf(250) : levelIndex();
    const flTxt = flName(fls()[li]);
    const sealedAt = new Map((sealedTimes || []).map((r) => [r.seq, r.sealed_at]));
    const items = [];
    for (const [key, r] of [...past.entries()].sort()) {
      if (!r.grade) continue;
      const slot = { key, t: new Date(key).getTime(), rec: { graded: true } };
      const o = hourOutcome(slot);
      const st = !o || !o.total ? "neutral" : (o.hits / o.total >= 0.5 ? "good" : "miss");
      items.push({
        seq: r.seq, st, when: `Sealed ${fmtWhen(r.sealed_at)} · ${leadText(leadMin(r.target_time, r.sealed_at))} ahead`,
        what: `Zones for ${fmtTime(key)}, ${peakText(r.prediction.risk, li)}`,
        then: o && o.total ? `${o.hits} of ${o.total} bumpy reports inside` : `Graded with ${fmtInt(r.grade.truth.n)} reports, none bumpy`,
        thenCls: st,
      });
    }
    for (const f of state.forecasts[LAB] || []) {
      if (items.some((x) => x.seq === f.seq)) continue;
      const due = new Date(f.valid_time).getTime() + GRADE_DELAY_MIN * 60e3;
      items.push({
        seq: f.seq, st: "open",
        when: `Sealed ${fmtWhen(f.sealed_at || sealedAt.get(f.seq) || state.issued_at)} · ${leadText(f.lead_minutes != null ? f.lead_minutes : leadMin(f.valid_time, f.sealed_at || sealedAt.get(f.seq) || state.issued_at))} ahead`,
        what: `Zones for ${fmtTime(f.valid_time)}, ${peakText(f.risk, li)}`,
        then: `Graded after ${fmtTime(due)}`, thenCls: "",
      });
    }
    items.sort((a, b) => a.seq - b.seq);
    if (!items.length) { row.innerHTML = `<div class="log-empty">No sealed records yet.</div>`; return; }
    const atEnd = row.scrollLeft + row.clientWidth >= row.scrollWidth - 4;
    row.innerHTML = items.map((it) => `<div class="log-item" title="Record ${it.seq}; peak at ${esc(flTxt)}">${sealSVG(it.st, 5)}
      <div class="when">${esc(it.when)}</div><div class="what">${esc(it.what)}</div>
      <div class="then ${it.thenCls}">${esc(it.then)}</div></div>`).join("");
    if (atEnd || !row.dataset.scrolled) { row.scrollLeft = row.scrollWidth; row.dataset.scrolled = "1"; }
  }

  // ------------------------------------------------------------ polling

  async function pollRecords() {
    if (!ok()) return;
    const [p, s] = await Promise.all([
      api.getJSON(`/api/turb/records?model=${LAB}&kind=turb&due=true&since_hours=7&per_target=latest&limit=12`),
      api.getJSON(`/api/turb/records?model=${LAB}&kind=turb&graded=no&prediction=false&limit=6`),
    ]);
    if (p) past = new Map(p.records.map((r) => [hourKey(r.target_time), r]));
    if (s) sealedTimes = s.records;
  }

  function renderSide() {
    renderSentence();
    renderRoc();
    renderBoard();
    renderLog();
  }

  return {
    setState(s, { failed: f } = {}) {
      failed = !!f;
      if (s !== undefined && s !== null) state = s;
      if (state && state.grid && !levels().includes(ui.hpa)) ui.hpa = levels()[Math.min(2, levels().length - 1)];
      if (!active) return;
      renderDynamic();
      renderSide();
    },
    async poll() {
      await pollRecords();
      if (!active) return;
      renderDynamic();
      renderSide();
    },
    activate(parts) {
      active = true;
      if (parts[0] && !isNaN(+parts[0])) ui.hpa = +parts[0];
      if (parts[1]) { ui.hour = parts[1]; ui.hourChosen = true; }
      if (!geo) loadGeo();
      const { w, h } = boxSize(mapPane, 320, 320);
      if (!mapSize || mapSize.w !== w || mapSize.h !== h || !proj) drawMap();
      else renderDynamic();
      renderSide();
    },
    deactivate() {
      active = false;
      hideTip();
    },
    resize() {
      if (!active) return;
      drawMap();
      renderSide();
    },
    hashParts,
  };
}
