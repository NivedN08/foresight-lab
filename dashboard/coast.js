// Coast view: tide gauges and wave buoys from San Diego to the Strait of Juan de Fuca.

import {
  drawSeals, drawTicks, sealSVG, fmtTime, fmtWhen, fmtInt, esc, showTip, hideTip, boxSize, numberWord, capital,
} from "./seal.js";

const d3 = window.d3;
const topojson = window.topojson;

const LAB = "SeedPhysics";
const WAVE_LAB = "SeedSwell";
const WAVE_REF = "WaveModel";
const LEADS = [1, 3, 6, 12, 24];
const DEFAULT_STATION = "9414290"; // San Francisco
const MAP_LEAD = 6; // the map's diamonds show the forecast for six hours ahead

const LABEL = {
  TideTable: "Tide table",
  Persistence: "Persistence",
  DampedPersistence: "Damped persistence",
  SeedPhysics: "Lab formula",
  WaveModel: "Wave model",
  SeedSwell: "Lab formula",
};
const OFFICIAL = new Set(["TideTable", "WaveModel"]);
const LABS = new Set([LAB, WAVE_LAB]);

const EXTENT = { lon: [-127.6, -115.8], lat: [32.3, 49.3] };
const BAY = { lon: [-123.12, -121.9], lat: [37.3, 38.2] };
const KM_PER_DEG = 111.2;
const BAR_PX_PER_M = 50;

const H = 3600e3;

const inBay = (lon, lat) => lon >= BAY.lon[0] && lon <= BAY.lon[1] && lat >= BAY.lat[0] && lat <= BAY.lat[1];

function seriesPoints(s) {
  if (!s || !s.values) return [];
  const t0 = new Date(s.start).getTime();
  const step = s.step_min * 60e3;
  return s.values.map((v, k) => ({ t: new Date(t0 + k * step), v }));
}

function buoyName(raw) {
  if (!raw) return "";
  let s = raw.split(" - ")[0];
  s = s.replace(/\(\d+\)/g, "").replace(/,\s*[A-Z]{2}\s*$/, "").replace(/,\s*[A-Z]{2}\b/, "").trim();
  s = s.toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());
  return s.replace(/\bPt\b/, "Point");
}

function distKm(a, b) {
  const dy = (a.lat - b.lat) * KM_PER_DEG;
  const dx = (a.lon - b.lon) * KM_PER_DEG * Math.cos(((a.lat + b.lat) / 2) * Math.PI / 180);
  return Math.hypot(dx, dy);
}

const cmText = (m) => {
  const cm = Math.round(m * 100);
  if (cm === 0) return "right on the table";
  return `${Math.abs(cm)} cm ${cm > 0 ? "above" : "below"} the table`;
};

// Forecast arrays -> objects. kind "water" or "waves".
function forecastList(f, kind) {
  if (!f || !f.lead_h) return [];
  return f.lead_h.map((lead, k) => {
    const o = {
      lead,
      target: new Date(f.target[k]),
      issued: new Date(f.issued[k]),
      error_cm: f.error_cm[k],
    };
    if (kind === "water") {
      o.value = f.water_level_m[k];
      o.residual = f.residual_m[k];
      o.truth = f.truth_water_level_m[k];
      o.truthResidual = o.truth == null ? null : o.truth - (o.value - o.residual);
    } else {
      o.value = f.wave_height_m[k];
      o.truth = f.truth_wave_height_m[k];
    }
    o.key = `${f.issued[k]}|${f.target[k]}|${lead}`;
    return o;
  });
}

// Good = closer than the official reference (tide table for water, wave model for waves).
function judge(o, refError) {
  if (o.error_cm == null) return "open";
  if (refError == null || !isFinite(refError)) return "neutral";
  return o.error_cm <= refError + 1e-9 ? "good" : "miss";
}

export function create(root, api) {
  const ui = { station: DEFAULT_STATION, buoy: null, lead: 6, boardMode: "live", boardKind: "water" };
  let state = null;
  let failed = false;
  let active = false;
  let land = null;
  let landLoading = null;
  let logRecords = null;
  let mapSize = null;

  root.innerHTML = `
    <div class="map-pane">
      <svg class="map" role="img" aria-label="Map of West Coast tide gauges and wave buoys"></svg>
      <div class="map-legend"></div>
      <div class="map-msg" hidden></div>
    </div>
    <aside class="side">
      <section class="lead-block">
        <p class="sentence">Reading the latest cycle…</p>
        <p class="alert" hidden></p>
        <p class="sub"></p>
      </section>
      <section class="detail">
        <div class="panel-head">
          <h2 class="strip-title">Water level</h2>
          <div class="controls"><span class="seg-label">Sealed</span><div class="seg lead-seg" role="group" aria-label="Lead time"></div></div>
        </div>
        <div class="chart-wrap"><svg class="chart strip-water"></svg><div class="chart-note" hidden></div></div>
        <div class="legend strip-legend"></div>
      </section>
      <section class="board">
        <div class="panel-head">
          <h2>Scoreboard</h2>
          <div class="controls">
            <div class="seg kind-seg" role="group" aria-label="Quantity">
              <button data-kind="water">Water level</button><button data-kind="waves">Waves</button>
            </div>
            <div class="seg mode-seg" role="group" aria-label="Results">
              <button data-mode="live">Live, sealed</button><button data-mode="backtest">Backtest</button>
            </div>
          </div>
        </div>
        <p class="sub board-note"></p>
        <table class="board"></table>
        <p class="sub board-foot"></p>
      </section>
      <section class="waves">
        <div class="panel-head"><h2 class="waves-title">Wave height</h2></div>
        <div class="chart-wrap"><svg class="chart strip-waves"></svg><div class="chart-note" hidden></div></div>
        <div class="legend waves-legend"></div>
        <p class="sub waves-sub"></p>
      </section>
    </aside>
    <div class="log">
      <div class="log-title"><strong>Log</strong><span class="log-sub"></span></div>
      <div class="log-row"></div>
    </div>`;

  const $ = (s) => root.querySelector(s);
  const mapPane = $(".map-pane");
  const svg = d3.select($("svg.map"));
  const sentenceEl = $(".sentence");
  const alertEl = $(".alert");
  const subEl = $(".lead-block .sub");

  // lead selector
  const leadSeg = $(".lead-seg");
  leadSeg.innerHTML = LEADS.map((l) => `<button data-lead="${l}">${l} h</button>`).join("");
  leadSeg.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    ui.lead = +b.dataset.lead;
    renderSide();
  });
  $(".mode-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    ui.boardMode = b.dataset.mode;
    renderBoard();
  });
  $(".kind-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    ui.boardKind = b.dataset.kind;
    renderBoard();
  });

  // ------------------------------------------------------------ data helpers

  const stations = () => (state && state.water ? state.water.stations : []);
  const buoys = () => (state && state.waves ? state.waves.buoys : []);
  const station = () => stations().find((s) => s.id === ui.station) || stations()[0];
  const buoy = () => buoys().find((b) => b.id === ui.buoy) || null;

  function nearestBuoy(st) {
    let best = null;
    for (const b of buoys()) {
      const d = distKm(st, b);
      if (!best || d < best.d) best = { d, b };
    }
    return best ? best.b : null;
  }

  function ensureSelection() {
    const st = station();
    if (st) ui.station = st.id;
    if (!buoy() && st) {
      const b = nearestBuoy(st);
      ui.buoy = b ? b.id : null;
    }
  }

  function select(stationId, buoyId) {
    if (stationId) {
      ui.station = stationId;
      const b = nearestBuoy(station());
      ui.buoy = b ? b.id : null;
    }
    if (buoyId) ui.buoy = buoyId;
    api.setHash(hashParts());
    renderMapMarks();
    renderSide();
    pollLog();
  }

  function hashParts() {
    return [ui.station, ui.buoy];
  }

  // ------------------------------------------------------------ map: base

  async function loadLand() {
    if (land || landLoading) return landLoading;
    landLoading = (async () => {
      const topo = await (await fetch("geo/land-10m.json")).json();
      const fc = topojson.feature(topo, topo.objects.land);
      const polys = [];
      for (const feat of fc.features) {
        const g = feat.geometry;
        const list = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
        for (const poly of list) {
          let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
          for (const [x, y] of poly[0]) {
            if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
          }
          if (x1 > -150 && x0 < -100 && y1 > 24 && y0 < 58) polys.push(poly);
        }
      }
      land = {
        area: { type: "Feature", geometry: { type: "MultiPolygon", coordinates: polys } },
        coast: { type: "MultiLineString", coordinates: polys.flatMap((p) => p) },
      };
    })();
    await landLoading;
    if (active) drawMap();
  }

  let proj = null;
  let inset = null; // {proj, x, y, w, h}

  // border band of the printed chart: outer neatline at FRAME_OUT, map inside FRAME_IN
  const FRAME_OUT = 6;
  const FRAME_IN = 26;

  function makeProjection(w, h) {
    const pad = FRAME_IN + 10;
    const p = d3.geoMercator().fitExtent([[pad, pad], [w - pad, h - pad]], {
      type: "MultiPoint",
      coordinates: [[EXTENT.lon[0], EXTENT.lat[0]], [EXTENT.lon[1], EXTENT.lat[1]]],
    });
    const right = p([EXTENT.lon[1], 40])[0];
    const shift = (w - pad) - right;
    if (shift > 0) {
      const [tx, ty] = p.translate();
      p.translate([tx + shift, ty]);
    }
    return p.clipExtent([[FRAME_IN, FRAME_IN], [w - FRAME_IN, h - FRAME_IN]]);
  }

  function makeInset(w, h) {
    const left = FRAME_IN + 26;
    const coastX = proj([-125.4, 38])[0];
    const avail = coastX - 30 - left;
    if (avail < 170 || w < 520) return null;
    const iw = Math.min(avail, 360, w * 0.42);
    const ih = iw * 0.86;
    const sfY = proj([-122.5, 37.8])[1];
    const iy = Math.max(64, Math.min(sfY - ih * 0.45, h - ih - 200));
    const ix = left;
    const p = d3.geoMercator()
      .fitExtent([[ix + 4, iy + 4], [ix + iw - 4, iy + ih - 4]], {
        type: "MultiPoint", coordinates: [[BAY.lon[0], BAY.lat[0]], [BAY.lon[1], BAY.lat[1]]],
      })
      .clipExtent([[ix, iy], [ix + iw, iy + ih]]);
    return { proj: p, x: ix, y: iy, w: iw, h: ih };
  }

  function drawMap() {
    if (!active) return;
    // on narrow screens the legend becomes a strip under the map instead of covering it
    const narrow = mapPane.clientWidth < 600;
    mapPane.classList.toggle("narrow", narrow);
    renderLegend();
    const legH = narrow ? $(".map-legend").offsetHeight : 0;
    const { w, h } = boxSize(mapPane, 300, 300 + legH);
    const hMap = h - legH;
    $("svg.map").style.height = `${hMap}px`;
    return drawMapSized(w, hMap);
  }

  function drawMapSized(w, h) {
    mapSize = { w, h };
    svg.attr("viewBox", `0 0 ${w} ${h}`);
    proj = makeProjection(w, h);
    inset = makeInset(w, h);
    const path = d3.geoPath(proj);

    svg.selectAll("*").remove();
    svg.append("defs").append("clipPath").attr("id", "coast-clip").append("rect")
      .attr("x", FRAME_IN).attr("y", FRAME_IN).attr("width", w - 2 * FRAME_IN).attr("height", h - 2 * FRAME_IN);
    svg.append("rect").attr("class", "ocean").attr("x", FRAME_IN).attr("y", FRAME_IN)
      .attr("width", w - 2 * FRAME_IN).attr("height", h - 2 * FRAME_IN);

    // graticule and neatline with degree ticks, as on a printed chart
    const grat = d3.geoGraticule().step([2, 2]).extent([[-140, 28], [-108, 54]]);
    const base = svg.append("g").attr("class", "base").attr("clip-path", "url(#coast-clip)");
    base.append("path").attr("class", "graticule").attr("d", path(grat()));
    if (land) {
      base.append("path").attr("class", "shoal").attr("stroke-width", 14).attr("d", path(land.coast));
      base.append("path").attr("class", "shoal").attr("stroke-width", 6).attr("stroke", "#C4E0EB").attr("d", path(land.coast));
      base.append("path").attr("class", "land").attr("d", path(land.area));
      base.append("path").attr("class", "coastline").attr("d", path(land.coast));
    }
    drawFrame(svg, w, h);

    // water names: the first spot clear of the inset and the legend
    const names = svg.append("g");
    const coastAt = (lat) => proj([-124.6, lat])[0];
    for (const lat of [46.8, 43.5, 35.2, 40.5]) {
      const cx = (FRAME_IN + coastAt(lat)) / 2;
      const y = proj([-130, lat])[1];
      const fsz = Math.max(18, Math.min(28, (coastAt(lat) - FRAME_IN) / 9));
      const half = fsz * 3.3;
      const box = { x0: cx - half, x1: cx + half, y0: y - fsz, y1: y + 6 };
      const hitsInset = inset && box.x0 < inset.x + inset.w && inset.x < box.x1 && box.y0 < inset.y + inset.h && inset.y - 24 < box.y1;
      const hitsLegend = box.y1 > h - 200 && box.x0 < 360;
      if (hitsInset || hitsLegend || box.x0 < FRAME_IN + 4 || y < FRAME_IN + 30) continue;
      names.append("text").attr("class", "water-name").attr("x", cx).attr("y", y)
        .attr("font-size", fsz).attr("text-anchor", "middle").text("Pacific Ocean");
      break;
    }

    // bay inset
    if (inset) {
      const ip = d3.geoPath(inset.proj);
      const a = proj([BAY.lon[0], BAY.lat[1]]);
      const b = proj([BAY.lon[1], BAY.lat[0]]);
      svg.append("rect").attr("class", "inset-box").attr("x", a[0]).attr("y", a[1])
        .attr("width", b[0] - a[0]).attr("height", b[1] - a[1]);
      svg.append("path").attr("class", "inset-box")
        .attr("d", `M${a[0]},${(a[1] + b[1]) / 2}L${inset.x + inset.w},${inset.y + inset.h / 2}`);
      const g = svg.append("g").attr("class", "inset");
      g.append("rect").attr("class", "inset-frame").attr("x", inset.x).attr("y", inset.y)
        .attr("width", inset.w).attr("height", inset.h);
      if (land) {
        g.append("path").attr("class", "shoal").attr("stroke-width", 10).attr("d", ip(land.coast));
        g.append("path").attr("class", "land").attr("d", ip(land.area));
        g.append("path").attr("class", "coastline").attr("d", ip(land.coast));
      }
      const bn = inset.proj([-122.33, 37.66]);
      g.append("text").attr("class", "water-name").attr("x", bn[0]).attr("y", bn[1]).attr("font-size", 15)
        .attr("text-anchor", "middle").text("San Francisco Bay");
      g.append("rect").attr("x", inset.x).attr("y", inset.y).attr("width", inset.w).attr("height", inset.h)
        .attr("fill", "none").attr("stroke", "#16283A");
      g.append("text").attr("class", "inset-title").attr("x", inset.x).attr("y", inset.y - 7)
        .text("San Francisco Bay, enlarged");
    }

    svg.append("g").attr("class", "marks-main");
    svg.append("g").attr("class", "marks-inset");
    svg.append("g").attr("class", "labels");
    renderMapMarks();
  }

  function drawFrame(sel, w, h) {
    const a = FRAME_OUT, b = FRAME_IN;
    const g = sel.append("g");
    g.append("rect").attr("class", "neatline").attr("x", a).attr("y", a).attr("width", w - 2 * a).attr("height", h - 2 * a);
    g.append("rect").attr("class", "neatline").attr("x", b).attr("y", b).attr("width", w - 2 * b).attr("height", h - 2 * b);
    const inv = proj.invert;
    const lonL = inv([b, h / 2])[0], lonR = inv([w - b, h / 2])[0];
    const latT = inv([w / 2, b])[1], latB = inv([w / 2, h - b])[1];
    // alternating degree bars in the border, as on a chart's scale border
    const bar = (x1, y1, x2, y2, k) => g.append("line").attr("x1", x1).attr("y1", y1).attr("x2", x2).attr("y2", y2)
      .attr("stroke", "#16283A").attr("stroke-width", 3).attr("stroke-opacity", k % 2 ? 0.9 : 0.12);
    for (let lon = Math.floor(lonL); lon <= lonR; lon++) {
      const x1 = Math.max(b, proj([lon, 40])[0]), x2 = Math.min(w - b, proj([lon + 1, 40])[0]);
      if (x2 > x1) { bar(x1, b - 2.5, x2, b - 2.5, lon); bar(x1, h - b + 2.5, x2, h - b + 2.5, lon); }
      const x = proj([lon, 40])[0];
      if (lon % 2 === 0 && x > b + 24 && x < w - b - 24) {
        g.append("text").attr("class", "frame-label").attr("x", x).attr("y", b - 8).attr("text-anchor", "middle").text(`${-lon}°W`);
        g.append("text").attr("class", "frame-label").attr("x", x).attr("y", h - b + 17).attr("text-anchor", "middle").text(`${-lon}°W`);
      }
    }
    for (let lat = Math.floor(latB); lat <= latT; lat++) {
      const y1 = Math.min(h - b, proj([-120, lat])[1]), y2 = Math.max(b, proj([-120, lat + 1])[1]);
      if (y1 > y2) { bar(b - 2.5, y1, b - 2.5, y2, lat); bar(w - b + 2.5, y1, w - b + 2.5, y2, lat); }
      const y = proj([-120, lat])[1];
      if (lat % 2 === 0 && y > b + 24 && y < h - b - 24) {
        g.append("text").attr("class", "frame-label").attr("transform", `translate(${b - 8},${y}) rotate(-90)`).attr("text-anchor", "middle").text(`${lat}°N`);
        g.append("text").attr("class", "frame-label").attr("transform", `translate(${w - b + 17},${y}) rotate(-90)`).attr("text-anchor", "middle").text(`${lat}°N`);
      }
    }
  }

  // ------------------------------------------------------------ map: marks

  // Lab forecast for MAP_LEAD hours ahead, from the latest issue still in the future.
  function mapForecast(st) {
    const list = forecastList(st.forecasts && st.forecasts[LAB], "water")
      .filter((o) => o.lead === MAP_LEAD && o.target.getTime() > Date.now());
    list.sort((a, b) => b.issued - a.issued);
    return list[0] || null;
  }

  function renderMapMarks() {
    if (!active || !proj) return;
    ensureSelection();
    const sts = stations();
    const bys = buoys();
    const items = sts.map((s) => {
      const f = mapForecast(s);
      return { s, f, bay: !!inset && inBay(s.lon, s.lat) };
    });
    placeGlyphs(svg.select("g.marks-main"), items.filter((d) => !d.bay), proj, bys.filter((b) => !(inset && inBay(b.lon, b.lat))));
    if (inset) placeGlyphs(svg.select("g.marks-inset"), items.filter((d) => d.bay), inset.proj, bys.filter((b) => inBay(b.lon, b.lat)));
    else svg.select("g.marks-inset").selectAll("*").remove();
    renderLabels(items);
    const msg = $(".map-msg");
    if (!state || state.empty) {
      msg.hidden = false;
      msg.textContent = failed ? "Cannot reach the lab server. Retrying every 30 seconds." : "No tide gauges yet: the live job has not written a state file.";
    } else msg.hidden = true;
  }

  function placeGlyphs(g, items, p, bys) {
    const k = BAR_PX_PER_M;
    const placed = [];
    const glyphs = items.map((d) => {
      const [sx, sy] = p([d.s.lon, d.s.lat]);
      const r = d.s.current_residual_m;
      const f = d.f ? d.f.residual : null;
      const ys = [sy, r == null ? sy : sy - r * k, f == null ? sy : sy - f * k];
      return { ...d, sx, sy, r, fr: f, top: Math.min(...ys) - 6, bot: Math.max(...ys) + 6 };
    }).sort((a, b) => a.sy - b.sy);
    for (const gl of glyphs) {
      let bx = gl.sx - 10;
      let guard = 0;
      while (guard++ < 12 && placed.some((q) => Math.abs(q.bx - bx) < 10 && q.top < gl.bot && gl.top < q.bot)) bx -= 11;
      gl.bx = bx;
      placed.push(gl);
    }
    g.node().__boxes = glyphs.map((d) => ({ x0: d.bx - 6, x1: Math.max(d.bx + 6, d.sx + 4), y0: d.top, y1: d.bot }))
      .concat(bys.map((b) => { const [x, y] = p([b.lon, b.lat]); return { x0: x - 8, x1: x + 8, y0: y - 8, y1: y + 8 }; }));

    // buoys first, underneath the gauges
    const bsel = g.selectAll("g.buoy").data(bys, (b) => b.id).join((en) => {
      const e = en.append("g").attr("class", "buoy").attr("tabindex", 0).attr("role", "button");
      e.append("path");
      e.append("circle").attr("class", "ring").attr("r", 0);
      return e;
    });
    bsel.attr("transform", (b) => `translate(${p([b.lon, b.lat])})`)
      .attr("aria-label", (b) => `Buoy ${buoyName(b.name)}`)
      .each(function (b) {
        const hgt = b.latest_wave_height_m;
        const s = 5 + 1.7 * (hgt == null ? 0 : Math.min(hgt, 10));
        const e = d3.select(this);
        e.select("path").attr("d", `M0,${-s * 0.62}L${s * 0.55},${s * 0.38}L${-s * 0.55},${s * 0.38}Z`)
          .attr("fill-opacity", hgt == null ? 0.25 : 0.8);
        e.select("circle.ring").attr("r", b.id === ui.buoy ? s * 0.75 + 3 : 0);
      })
      .on("click", (e, b) => select(null, b.id))
      .on("keydown", (e, b) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(null, b.id); } })
      .on("mousemove", (e, b) => showTip(buoyTip(b), e))
      .on("mouseleave", hideTip);

    const sel = g.selectAll("g.gauge").data(glyphs, (d) => d.s.id).join((en) => {
      const e = en.append("g").attr("class", "gauge").attr("tabindex", 0).attr("role", "button");
      e.append("rect").attr("class", "hit");
      e.append("path").attr("class", "leader");
      e.append("line").attr("class", "base");
      e.append("rect").attr("class", "bar");
      e.append("circle").attr("class", "ring").attr("r", 0);
      e.append("circle").attr("class", "dot").attr("r", 3);
      e.append("g").attr("class", "fc");
      return e;
    });
    sel.attr("aria-label", (d) => `${d.s.name}: ${d.r == null ? "no recent reading" : cmText(d.r)}`)
      .each(function (d) {
        const e = d3.select(this);
        const x0 = Math.min(d.bx - 7, d.sx - 5);
        e.select("rect.hit").attr("x", x0).attr("y", d.top).attr("width", Math.max(d.sx + 6, d.bx + 7) - x0).attr("height", d.bot - d.top);
        e.select("path.leader").attr("d", d.bx < d.sx - 11 ? `M${d.sx},${d.sy}H${d.bx}` : null);
        e.select("line.base").attr("x1", d.bx - 5).attr("x2", d.bx + 5).attr("y1", d.sy).attr("y2", d.sy);
        const r = d.r == null ? 0 : d.r * k;
        e.select("rect.bar").attr("x", d.bx - 2).attr("width", 4)
          .attr("y", r >= 0 ? d.sy - r : d.sy).attr("height", Math.abs(r));
        e.select("circle.dot").attr("cx", d.sx).attr("cy", d.sy).attr("fill-opacity", d.r == null ? 0.3 : 1);
        e.select("circle.ring").attr("cx", d.sx).attr("cy", d.sy).attr("r", d.s.id === ui.station ? 7 : 0);
        drawSeals(e.select("g.fc"), d.fr == null ? [] : [{ key: d.s.id, x: d.bx, y: d.sy - d.fr * k, state: "open" }], { r: 4.5 });
      })
      .on("click", (e, d) => select(d.s.id))
      .on("keydown", (e, d) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(d.s.id); } })
      .on("mousemove", (e, d) => showTip(gaugeTip(d), e))
      .on("mouseleave", hideTip);
    sel.filter((d) => d.s.id === ui.station).raise();
  }

  function renderLabels(items) {
    const g = svg.select("g.labels");
    g.selectAll("*").remove();
    if (!proj) return;
    const sets = {
      main: [...(svg.select("g.marks-main").node().__boxes || [])],
      bay: [...((inset && svg.select("g.marks-inset").node().__boxes) || [])],
    };
    if (inset) sets.main.push({ x0: inset.x, x1: inset.x + inset.w, y0: inset.y - 22, y1: inset.y + inset.h });
    const ordered = [...items].sort((a, b) => (b.s.id === ui.station) - (a.s.id === ui.station) || b.s.lat - a.s.lat);
    for (const d of ordered) {
      const boxes = d.bay ? sets.bay : sets.main;
      const fits = (b) => !boxes.some((o) => b.x0 < o.x1 && o.x0 < b.x1 && b.y0 < o.y1 && o.y0 < b.y1);
      const p = d.bay ? inset.proj : proj;
      const [x, y] = p([d.s.lon, d.s.lat]);
      const sel = d.s.id === ui.station;
      const text = sel && d.s.current_residual_m != null
        ? `${d.s.name} ${d.s.current_residual_m >= 0 ? "+" : "−"}${Math.abs(Math.round(d.s.current_residual_m * 100))} cm`
        : d.s.name;
      const fs = sel ? 15 : 13;
      const wTxt = text.length * fs * 0.48 + 4;
      // try right of the gauge, then below-right, then above-right
      const cands = [[x + 8, y + 4, 0], [x + 6, y + 16, 0], [x + 6, y - 8, 0], [x - 22, y + 4, 1]];
      let pos = null;
      for (const [tx, ty, left] of cands) {
        const b = left
          ? { x0: tx - wTxt, x1: tx + 2, y0: ty - fs * 0.9 - 2, y1: ty + fs * 0.35 + 2 }
          : { x0: tx - 2, x1: tx + wTxt, y0: ty - fs * 0.9 - 2, y1: ty + fs * 0.35 + 2 };
        if (fits(b)) { pos = [tx, ty, b, left]; break; }
      }
      if (!pos && sel) pos = [x + 8, y + 4, { x0: x + 7, x1: x + 8 + wTxt, y0: y - 12, y1: y + 5 }];
      if (!pos) continue;
      boxes.push(pos[2]);
      for (const cls of ["place halo", "place"]) {
        g.append("text").attr("class", cls + (sel ? " selected" : "") + (pos[3] ? " on-water" : ""))
          .attr("text-anchor", pos[3] ? "end" : "start").attr("x", pos[0]).attr("y", pos[1]).text(text);
      }
    }
  }

  function gaugeTip(d) {
    const s = d.s;
    let h = `<b>${esc(s.name)}</b> <span class="q">gauge ${esc(s.id)}</span><br>`;
    h += d.r == null ? `<span class="q">No reading in the last hour</span><br>`
      : `Now: ${esc(cmText(d.r))}, measured ${esc(fmtWhen(s.latest_obs_time))}<br>`;
    if (d.f) h += `<span class="s">Sealed for ${esc(fmtWhen(d.f.target))}: ${esc(cmText(d.f.residual))}</span><br>`;
    h += `<span class="o">Tide table: 0 cm by definition</span>`;
    return h;
  }

  function buoyTip(b) {
    let h = `<b>${esc(buoyName(b.name))}</b> <span class="q">buoy ${esc(b.id)}</span><br>`;
    h += b.latest_wave_height_m == null ? `<span class="q">No recent report</span>`
      : `Waves ${b.latest_wave_height_m.toFixed(1)} m${b.latest_dominant_period_s ? `, period ${b.latest_dominant_period_s.toFixed(0)} s` : ""}, ${esc(fmtWhen(b.latest_obs_time))}`;
    return h;
  }

  function renderLegend() {
    const k = BAR_PX_PER_M;
    const leg = $(".map-legend");
    leg.innerHTML = `
      <div class="title">Tide gauges</div>
      <div class="row"><svg width="20" height="30" viewBox="-10 -26 20 30">
        <line x1="-6" x2="6" y1="0" y2="0" stroke="#2A6A9E" stroke-width="1.5"/>
        <rect x="-2" width="4" y="${-0.5 * k}" height="${0.5 * k}" fill="#16283A"/></svg>
        <span>Ink bar: water above the tide table now, 50 cm shown. Blue tick: the tide table.</span></div>
      <div class="row">${sealSVG("open", 4.5)}<span>Sealed by the lab for ${MAP_LEAD} hours ahead</span></div>
      <div class="row"><svg width="20" height="14" viewBox="-10 -7 20 14"><path d="M0,-6L5.5,4L-5.5,4Z" fill="#16283A" fill-opacity="0.8"/></svg>
        <span>Wave buoy, larger for higher waves</span></div>`;
  }

  // ------------------------------------------------------------ side: sentence

  function renderSentence() {
    alertEl.hidden = true;
    if (!state || state.empty) {
      sentenceEl.innerHTML = failed && !state
        ? "Cannot reach the lab server."
        : `No forecasts sealed yet. Start the live job: <code>.venv/bin/python -m lab.tasks.coast.live run</code>`;
      subEl.textContent = failed ? "The dashboard retries every 30 seconds." : "";
      return;
    }
    const st = station();
    if (!st) {
      sentenceEl.textContent = "The state file lists no tide gauges.";
      subEl.textContent = "";
      return;
    }
    const now = Date.now();
    const name = esc(st.name);
    let s;
    const r = st.current_residual_m;
    if (r == null) s = `${name} has not reported a water level in the last hour.`;
    else {
      const cm = Math.round(r * 100);
      s = cm === 0 ? `${name} is running right on the tide table.`
        : `${name} is running <span class="num">${Math.abs(cm)} cm</span> ${cm > 0 ? "above" : "below"} the tide table.`;
    }
    const fl = forecastList(st.forecasts && st.forecasts[LAB], "water").filter((o) => o.lead === 6);
    const due = fl.filter((o) => o.target.getTime() <= now && o.target.getTime() > now - 1.5 * H)
      .sort((a, b) => b.target - a.target)[0];
    const sealedCm = (m) => {
      const cm = Math.round(m * 100);
      return `<span class="sealed num">${cm < 0 ? `${-cm} cm below` : `${cm} cm`}</span>`;
    };
    if (due) {
      s += ` Six hours ago the lab sealed ${sealedCm(due.residual)}; the official table said <span class="official num">0</span>.`;
      if (due.truthResidual != null) {
        const t = Math.round(due.truthResidual * 100);
        s += ` The gauge measured <span class="num">${t < 0 ? `${-t} cm below` : `${t} cm`}</span>.`;
      }
    } else {
      const next = fl.filter((o) => o.target.getTime() > now).sort((a, b) => b.issued - a.issued)[0];
      if (next) {
        s += ` At ${fmtTime(next.issued)} the lab sealed ${sealedCm(next.residual)} for ${fmtWhen(next.target)}; the official table says <span class="official num">0</span>.`;
      }
    }
    sentenceEl.innerHTML = s;

    // alerts: one line
    const alerts = (state.alerts || []).filter((a) => !a.ends || new Date(a.ends).getTime() > now);
    if (alerts.length) {
      const order = ["Coastal Flood Warning", "Storm Surge Warning", "Coastal Flood Advisory", "Coastal Flood Watch",
        "High Surf Warning", "Tsunami Warning", "Storm Warning", "Gale Warning", "High Surf Advisory"];
      const rank = (a) => { const i = order.indexOf(a.event); return i < 0 ? 99 : i; };
      const top = [...alerts].sort((a, b) => rank(a) - rank(b))[0];
      const area = (top.area || "").split(";")[0].trim();
      const more = alerts.length - 1;
      alertEl.hidden = false;
      alertEl.textContent = `${top.event}${area ? ` for ${area}` : ""}${more ? `, and ${more} more coastal alert${more > 1 ? "s" : ""} from the National Weather Service` : ""}.`;
      const counts = d3.rollups(alerts, (v) => v.length, (a) => a.event).sort((a, b) => b[1] - a[1]);
      alertEl.title = counts.map(([e, n]) => `${e}: ${n}`).join("\n");
    }

    // what has been graded, or when grading starts
    const sb = state.water.scoreboard || {};
    const lab6 = sb[LAB] && sb[LAB]["6"];
    const tide6 = sb.TideTable && sb.TideTable["6"];
    if (lab6 && lab6.graded) {
      subEl.textContent = `Graded so far at 6 hours ahead, over all ${stations().length} gauges: the lab formula is off by ${lab6.mae_cm.toFixed(1)} cm on average (${fmtInt(lab6.graded)} forecasts)` +
        (tide6 ? `, the tide table by ${tide6.mae_cm.toFixed(1)} cm.` : ".");
    } else {
      subEl.textContent = firstGradeText();
    }
  }

  function firstGradeText() {
    let first = null;
    for (const s of stations()) {
      for (const o of forecastList(s.forecasts && s.forecasts[LAB], "water")) {
        if (o.error_cm == null && (!first || o.target < first)) first = o.target;
      }
    }
    if (!first) return "Nothing sealed is waiting for a grade.";
    return first.getTime() > Date.now()
      ? `Sealed forecasts are graded against the gauges once their time passes. First grades arrive after ${fmtWhen(first)}.`
      : `The first sealed forecasts came due at ${fmtWhen(first)}; they are graded as soon as the gauges report.`;
  }

  // ------------------------------------------------------------ side: strip charts

  function stripChart(svgEl, noteEl, cfg) {
    const width = Math.max(260, Math.floor(svgEl.parentNode.getBoundingClientRect().width));
    const height = cfg.height;
    const m = { l: 34, r: 8, t: 22, b: 22 };
    const s = d3.select(svgEl).attr("viewBox", `0 0 ${width} ${height}`).attr("height", height);
    const now = new Date();
    const x = d3.scaleTime().domain([new Date(+now - 24 * H), new Date(+now + 24 * H)]).range([m.l, width - m.r]);
    const inWin = (p) => p.t >= x.domain()[0] && p.t <= x.domain()[1];
    const ref = cfg.ref.filter(inWin);
    const obs = cfg.obs.filter(inWin);
    const fcs = cfg.forecasts.filter((o) => o.target >= x.domain()[0] && o.target <= x.domain()[1]);
    const vals = [...ref.map((p) => p.v), ...obs.map((p) => p.v), ...fcs.map((o) => o.value), ...fcs.map((o) => o.truth)]
      .filter((v) => v != null && isFinite(v));
    const ext = vals.length ? d3.extent(vals) : [0, 1];
    if (cfg.zeroFloor) ext[0] = Math.min(0, ext[0]);
    const pad = Math.max(0.1, (ext[1] - ext[0]) * 0.08);
    const lo = cfg.zeroFloor ? Math.max(0, ext[0] - pad) : ext[0] - pad;
    const y = d3.scaleLinear().domain([lo, ext[1] + pad]).nice(4).range([height - m.b, m.t]);

    if (s.select("g.frame").empty()) {
      s.append("g").attr("class", "frame");
      s.append("defs").append("clipPath").attr("id", cfg.clipId).append("rect");
      s.append("g").attr("class", "lines").attr("clip-path", `url(#${cfg.clipId})`);
      s.append("g").attr("class", "ticks");
      s.append("g").attr("class", "seals");
      s.append("g").attr("class", "nowg");
    }
    s.select(`#${cfg.clipId} rect`).attr("x", m.l).attr("y", 0).attr("width", width - m.l - m.r).attr("height", height);

    const frame = s.select("g.frame");
    frame.selectAll("*").remove();
    const yt = y.ticks(4);
    frame.selectAll("line.gridline").data(yt).join("line").attr("class", "gridline")
      .attr("x1", m.l).attr("x2", width - m.r).attr("y1", (d) => y(d)).attr("y2", (d) => y(d));
    frame.selectAll("text.yl").data(yt).join("text").attr("class", "tick-label yl")
      .attr("x", m.l - 5).attr("y", (d) => y(d) + 4).attr("text-anchor", "end").text((d) => d3.format(".1f")(d));
    frame.append("text").attr("class", "tick-label").attr("x", 2).attr("y", 9).text(cfg.unitLong || cfg.unit);
    const xt = x.ticks(d3.timeHour.every(width < 420 ? 12 : 6));
    frame.selectAll("text.xl").data(xt).join("text").attr("class", "tick-label xl")
      .attr("x", (d) => x(d)).attr("y", height - 5).attr("text-anchor", "middle")
      .text((d) => (d.getHours() === 0 ? d.toLocaleDateString(undefined, { weekday: "short" }) : fmtTime(d)));
    frame.selectAll("line.xt").data(xt).join("line").attr("class", "gridline xt")
      .attr("x1", (d) => x(d)).attr("x2", (d) => x(d)).attr("y1", height - m.b).attr("y2", height - m.b + 4);

    const lines = s.select("g.lines");
    const lineGen = d3.line().defined((p) => p.v != null).x((p) => x(p.t)).y((p) => y(p.v)).curve(d3.curveMonotoneX);
    lines.selectAll("path.ref").data([ref]).join("path").attr("class", `ref ${cfg.refClass}`).attr("d", lineGen);
    lines.selectAll("path.obs").data([obs.filter((p) => p.t <= now)]).join("path").attr("class", "obs line-obs").attr("d", lineGen);

    const nowg = s.select("g.nowg");
    nowg.selectAll("*").remove();
    nowg.append("line").attr("class", "now-rule").attr("x1", x(now)).attr("x2", x(now)).attr("y1", m.t - 6).attr("y2", height - m.b);
    nowg.append("text").attr("class", "now-label").attr("x", x(now) + 4).attr("y", m.t - 4).text("now");

    const pts = fcs.filter((o) => o.value != null).map((o) => ({
      ...o, x: x(o.target), y: y(o.value), x0: Math.max(m.l, x(o.issued)), state: cfg.judge(o),
    }));
    drawTicks(s.select("g.ticks"), pts.map((p) => ({ key: p.key, x0: p.x0, x1: p.x - 5, y: p.y })));
    drawSeals(s.select("g.seals"), pts, { r: 4.5 })
      .on("mousemove", (e, d) => showTip(cfg.tip(d), e))
      .on("mouseleave", hideTip);

    if (cfg.note) { noteEl.hidden = false; noteEl.textContent = cfg.note; } else noteEl.hidden = true;
  }

  function renderStrips() {
    leadSeg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.lead === ui.lead)));
    const st = state && !state.empty ? station() : null;
    const wSvg = $("svg.strip-water");
    const wNote = $(".detail .chart-note");
    $(".strip-title").innerHTML = st ? `${esc(st.name)} <span class="quiet">water level</span>` : "Water level";
    if (!st) {
      d3.select(wSvg).selectAll("*").remove();
      d3.select(wSvg).attr("height", 190);
      wNote.hidden = false;
      wNote.textContent = state && state.empty ? "No gauge data yet." : "Waiting for data.";
    } else {
      const fl = forecastList(st.forecasts && st.forecasts[LAB], "water").filter((o) => o.lead === ui.lead);
      const nGraded = fl.filter((o) => o.error_cm != null).length;
      stripChart(wSvg, wNote, {
        height: 172,
        clipId: "clip-water",
        unit: "m",
        unitLong: "metres above mean lower low water",
        ref: seriesPoints(st.tide),
        refClass: "line-tide",
        obs: seriesPoints(st.observed),
        forecasts: fl,
        judge: (o) => judge(o, o.truthResidual == null ? null : Math.abs(o.truthResidual) * 100),
        tip: (o) => {
          let h = `<b>Issued ${esc(fmtWhen(o.issued))}</b> for ${esc(fmtWhen(o.target))}, ${o.lead} h ahead<br>`;
          h += `<span class="s">Lab formula: ${o.value.toFixed(2)} m, ${esc(cmText(o.residual))}</span><br>`;
          if (o.error_cm != null) {
            h += `Measured: ${o.truth.toFixed(2)} m, ${esc(cmText(o.truthResidual))}<br>`;
            h += `Lab error ${o.error_cm.toFixed(1)} cm; <span class="o">tide table error ${(Math.abs(o.truthResidual) * 100).toFixed(1)} cm</span>`;
          } else h += `<span class="q">${o.target.getTime() > Date.now() ? "Not due yet" : "Due; waiting for the gauge"}</span>`;
          return h;
        },
        note: fl.length ? null : `Nothing sealed ${ui.lead} h ahead in this window yet.`,
      });
      $(".strip-legend").innerHTML = `
        <span class="k"><span class="sw-line tide"></span>Tide table</span>
        <span class="k"><span class="sw-line"></span>Measured</span>
        <span class="k">${sealSVG("open", 4.5)}Sealed ${ui.lead} h ahead, waiting</span>
        <span class="k">${sealSVG("good", 4.5)}Closer than the table</span>
        <span class="k">${sealSVG("miss", 4.5)}Further off</span>
        <span class="k">${nGraded ? `${nGraded} graded here` : ""}</span>`;
    }

    // waves
    const wavesSec = $("section.waves");
    const b = state && !state.empty && state.waves ? buoy() : null;
    wavesSec.hidden = !b;
    if (b) {
      $(".waves-title").innerHTML = `${esc(buoyName(b.name))} <span class="quiet">wave height, buoy ${esc(b.id)}</span>`;
      const fl = forecastList(b.forecasts && b.forecasts[WAVE_LAB], "waves").filter((o) => o.lead === ui.lead);
      const ref = new Map(forecastList(b.forecasts && b.forecasts[WAVE_REF], "waves").map((o) => [o.key, o]));
      stripChart($("svg.strip-waves"), $(".waves .chart-note"), {
        height: 112,
        clipId: "clip-waves",
        unit: "m",
        unitLong: "significant wave height, metres",
        zeroFloor: true,
        ref: seriesPoints(b.wave_model),
        refClass: "line-ref",
        obs: seriesPoints(b.observed),
        forecasts: fl,
        judge: (o) => judge(o, ref.get(o.key) ? ref.get(o.key).error_cm : null),
        tip: (o) => {
          const rf = ref.get(o.key);
          let h = `<b>Issued ${esc(fmtWhen(o.issued))}</b> for ${esc(fmtWhen(o.target))}, ${o.lead} h ahead<br>`;
          h += `<span class="s">Lab formula: ${o.value.toFixed(2)} m</span><br>`;
          if (rf) h += `<span class="o">Wave model: ${rf.value.toFixed(2)} m</span><br>`;
          if (o.error_cm != null) {
            h += `Measured: ${o.truth.toFixed(2)} m; lab error ${o.error_cm.toFixed(0)} cm` +
              (rf && rf.error_cm != null ? `, wave model error ${rf.error_cm.toFixed(0)} cm` : "");
          } else h += `<span class="q">${o.target.getTime() > Date.now() ? "Not due yet" : "Due; waiting for the buoy"}</span>`;
          return h;
        },
        note: fl.length ? null : `Nothing sealed ${ui.lead} h ahead for this buoy yet.`,
      });
      $(".waves-legend").innerHTML = `
        <span class="k"><span class="sw-line tide"></span>Wave model</span>
        <span class="k"><span class="sw-line"></span>Measured</span>
        <span class="k">${sealSVG("open", 4.5)}Sealed, waiting</span>
        <span class="k">${sealSVG("good", 4.5)}Closer than the wave model</span>
        <span class="k">${sealSVG("miss", 4.5)}Further off</span>`;
      const up = b.upstream_buoy && (state.waves.offshore || []).find((o) => o.id === b.upstream_buoy);
      $(".waves-sub").textContent = up && up.latest_wave_height_m != null
        ? `The lab formula also reads offshore buoy ${b.upstream_buoy}, ${fmtInt(b.upstream_distance_km)} km out, whose swell reaches here later. It last reported ${up.latest_wave_height_m.toFixed(1)} m at ${fmtWhen(up.latest_obs_time)}.`
        : "";
    }
  }

  // ------------------------------------------------------------ side: scoreboard

  function renderBoard() {
    const table = $("table.board");
    const note = $(".board-note");
    const foot = $(".board-foot");
    const kindSeg = $(".kind-seg");
    const modeSeg = $(".mode-seg");
    const ok = state && !state.empty;
    const hasWaves = ok && !!state.waves;
    const hasWaveBt = ok && !!state.backtest_waves;
    if (!hasWaves && ui.boardKind === "waves") ui.boardKind = "water";
    kindSeg.querySelector('[data-kind="waves"]').disabled = !hasWaves;
    kindSeg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.kind === ui.boardKind)));
    modeSeg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === ui.boardMode)));
    modeSeg.querySelector('[data-mode="backtest"]').disabled = ui.boardKind === "waves" && !hasWaveBt;

    if (!ok) {
      table.innerHTML = "";
      note.textContent = "No results yet.";
      foot.textContent = "";
      return;
    }
    const water = ui.boardKind === "water";
    const block = water ? state.water : state.waves;
    const models = block.models || [];
    const leads = block.leads_h || LEADS;
    let cell;
    if (ui.boardMode === "live") {
      const sb = block.scoreboard || {};
      cell = (m, l) => { const c = sb[m] && sb[m][String(l)]; return c ? { v: c.mae_cm, n: c.graded } : null; };
      const any = Object.keys(sb).length > 0;
      note.textContent = any
        ? "Mean error in cm of sealed forecasts graded so far, all stations. Small figures count them."
        : `Nothing graded yet. ${firstGradeText()}`;
    } else {
      const bt = water ? block.backtest : state.backtest_waves;
      const tb = (bt && bt.table) || {};
      cell = (m, l) => { const c = tb[m] && tb[m][String(l)]; return c ? { v: c.mae_cm, n: c.n } : null; };
      note.textContent = bt ? `Backtest over the past ${bt.days} days: mean error in cm. Small figures count forecasts.` : "No backtest has been run yet.";
    }
    let html = `<thead><tr><th>Ahead</th>${models.map((m) =>
      `<th class="${OFFICIAL.has(m) ? "official" : ""}${LABS.has(m) ? "lab" : ""}">${esc(LABEL[m] || m)}</th>`).join("")}</tr></thead><tbody>`;
    for (const l of leads) {
      const cells = models.map((m) => cell(m, l));
      const vals = cells.filter(Boolean).map((c) => c.v);
      const best = vals.length > 1 ? Math.min(...vals) : null;
      html += `<tr><th>${l} h</th>` + cells.map((c, i) => {
        const cls = [LABS.has(models[i]) ? "lab" : ""];
        if (!c) return `<td class="dash ${cls.join(" ")}"><span class="v">–</span><span class="n"></span></td>`;
        if (best != null && c.v === best) cls.push("best");
        return `<td class="${cls.join(" ")}"><span class="v">${c.v.toFixed(ui.boardMode === "live" ? 1 : 2)}</span><span class="n">${fmtInt(c.n)}</span></td>`;
      }).join("") + "</tr>";
    }
    table.innerHTML = html + "</tbody>";

    // backtest: the paired comparison for the selected lead, and the caveat
    foot.textContent = "";
    if (ui.boardMode === "backtest") {
      const bt = water ? block.backtest : state.backtest_waves;
      const other = water ? "Persistence" : "WaveModel";
      const pr = bt && bt.paired_seed_vs && bt.paired_seed_vs[other] && bt.paired_seed_vs[other][String(ui.lead)];
      let t = "";
      if (pr) {
        const name = (LABEL[other] || other).toLowerCase();
        const f = (v) => Math.abs(v).toFixed(2);
        if (pr.high < 0) t = `At ${ui.lead} h ahead the lab formula's error was ${f(pr.diff_cm)} cm lower than ${name}'s (95% interval ${f(pr.high)} to ${f(pr.low)} cm lower). `;
        else if (pr.low > 0) t = `At ${ui.lead} h ahead the lab formula's error was ${f(pr.diff_cm)} cm higher than ${name}'s (95% interval ${f(pr.low)} to ${f(pr.high)} cm higher). `;
        else t = `At ${ui.lead} h ahead the lab formula and ${name} were level: the 95% interval runs from ${f(pr.low)} cm lower to ${f(pr.high)} cm higher. `;
      }
      foot.textContent = t + (bt && bt.note ? bt.note : "");
    }
  }

  function renderSide() {
    renderSentence();
    renderStrips();
    renderBoard();
  }

  // ------------------------------------------------------------ log row

  async function pollLog() {
    if (!active || !state || state.empty) { renderLog(); return; }
    const base = `/api/coast/records?model=${LAB}&kind=coast_water&station=${encodeURIComponent(ui.station)}`;
    const [done, open] = await Promise.all([
      api.getJSON(`${base}&graded=yes&limit=6`),
      api.getJSON(`${base}&graded=no&limit=8`),
    ]);
    if (done || open) {
      const bySeq = new Map();
      for (const r of [...(done ? done.records : []), ...(open ? open.records : [])]) bySeq.set(r.seq, r);
      logRecords = { station: ui.station, list: [...bySeq.values()].sort((a, b) => a.seq - b.seq) };
    }
    renderLog();
  }

  function renderLog() {
    const row = $(".log-row");
    const st = station();
    $(".log-sub").textContent = st ? `${st.name}, lab formula; newest at right` : "";
    if (!state || state.empty) {
      row.innerHTML = `<div class="log-empty">No forecasts sealed yet. Start the live job: <code>.venv/bin/python -m lab.tasks.coast.live run</code></div>`;
      return;
    }
    if (!logRecords || logRecords.station !== ui.station) {
      row.innerHTML = `<div class="log-empty">Reading the ledger…</div>`;
      return;
    }
    if (!logRecords.list.length) {
      row.innerHTML = `<div class="log-empty">No sealed records for this gauge yet.</div>`;
      return;
    }
    const atEnd = row.scrollLeft + row.clientWidth >= row.scrollWidth - 4;
    row.innerHTML = logRecords.list.map((r) => {
      const p = r.prediction || {};
      const g = r.grade && r.grade.truth;
      let stateName = "open", then;
      if (g) {
        const ref = Math.abs(g.residual_m) * 100;
        stateName = g.abs_error_cm <= ref ? "good" : "miss";
        then = `<div class="then ${stateName}">Measured ${esc(cmText(g.residual_m))}, off by ${g.abs_error_cm.toFixed(1)} cm</div>`;
      } else {
        then = new Date(r.target_time).getTime() > Date.now()
          ? `<div class="then">Due ${esc(fmtWhen(r.target_time))}</div>`
          : `<div class="then">Due; waiting for the gauge</div>`;
      }
      const lead = (r.meta && r.meta.lead_h) || "";
      return `<div class="log-item" title="Record ${r.seq}, hash ${esc(r.hash.slice(0, 12))}…">${sealSVG(stateName, 5)}
        <div class="when">Sealed ${esc(fmtWhen(r.sealed_at))} · ${lead} h ahead</div>
        <div class="what">${esc(capital(cmText(p.residual_m)))} at ${esc(fmtTime(r.target_time))}</div>${then}</div>`;
    }).join("");
    if (atEnd || !row.dataset.scrolled) { row.scrollLeft = row.scrollWidth; row.dataset.scrolled = "1"; }
  }

  // ------------------------------------------------------------ lifecycle

  return {
    setState(s, { failed: f } = {}) {
      failed = !!f;
      if (s !== undefined && s !== null) state = s;
      if (!active) return;
      ensureSelection();
      renderMapMarks();
      renderSide();
    },
    async poll() {
      await pollLog();
    },
    activate(parts) {
      active = true;
      if (parts[0]) ui.station = parts[0];
      if (parts[1]) ui.buoy = parts[1];
      ensureSelection();
      if (!land) loadLand();
      const { w, h } = boxSize(mapPane, 300, 300);
      if (!mapSize || mapSize.w !== w || mapSize.h !== h || !proj) drawMap();
      else renderMapMarks();
      renderSide();
      renderLog();
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
