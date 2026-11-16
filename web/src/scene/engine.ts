// The scene: a canvas that shows, for one moment on the ledger's timeline, what had been sealed and
// what had come true. Nothing moves unless the ledger says so: a gauge lights when its forecast is
// sealed and shows the gauge's reading once the grade exists; the sky shows the sealed grid of the
// latest turbulence cycle and, once graded, the pilot reports that graded it.

import type { Coast, Gap, HistRow, Site, Turb, TurbCycle } from "../data/types";
import { cycleAt, fmtHours, fmtMin, gapAt, modelName, nearestIndex, reportClass, riskAt, siteAt } from "../data/derive";
import { makeLayout, project, rowTop, shortName, unproject, type Box, type Layout } from "./layout";
import { BAYER, C, RAMP, hexRgb } from "./palette";
import { buildStatic } from "./staticLayer";
import type { Geo } from "../data/types";

export type Target =
  | { type: "gauge"; id: string; seq?: number }
  | { type: "buoy"; id: string; seq?: number }
  | { type: "cell"; level: number; i: number; j: number }
  | { type: "report"; index: number };

export type View = "all" | "coast" | "sky";

export type SceneCallbacks = {
  onHover: (target: Target | null, x: number, y: number) => void;
  onSelect: (target: Target | null) => void;
};

export type SceneState = {
  t: number;
  level: number;
  waterModel: number;
  wavesModel: number;
  selected: Target | null;
  gaps: Gap[];
};

/** The same thing on the map; a site is the same site whichever of its records is pinned. */
const same = (a: Target | null, b: Target | null) => {
  if (!a || !b) return a === b;
  if ((a.type === "gauge" || a.type === "buoy") && (b.type === "gauge" || b.type === "buoy")) return a.type === b.type && a.id === b.id;
  return JSON.stringify(a) === JSON.stringify(b);
};

export class SceneEngine {
  private ctx: CanvasRenderingContext2D;
  private L: Layout;
  private staticCv: HTMLCanvasElement;
  private skyCv: HTMLCanvasElement;
  private skyKey = "";
  private raf = 0;
  private real = 0;
  private lastNow = 0;
  private cam = { x: 0, y: 0, z: 1 };
  private camTo: { x: number; y: number; z: number } | null = null;
  private view: View = "all";
  /** Width of the stage's right edge covered by the side panel; the camera fits the rest. */
  private insetR = 0;
  private dpr = 1;
  private cw = 1;
  private ch = 1;
  private hover: Target | null = null;
  private resizeObs: ResizeObserver;
  private reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  /** Display range of each board's bars, over the whole history so bars keep their scale while scrubbing. */
  private range: { water: [number, number]; waves: [number, number] };
  private s: SceneState;
  /** The forecast leads, in hours, that the snapshot kept for the boards. */
  private leads: number[];

  constructor(
    private canvas: HTMLCanvasElement,
    private coast: Coast,
    private turb: Turb | null,
    geo: Geo,
    state: SceneState,
    private cb: SceneCallbacks,
  ) {
    this.ctx = canvas.getContext("2d")!;
    this.s = state;
    this.L = makeLayout(geo, coast.water.stations, coast.waves.buoys);
    this.staticCv = buildStatic(geo, this.L);
    this.skyCv = document.createElement("canvas");
    this.skyCv.width = this.L.sky.box.w;
    this.skyCv.height = this.L.sky.box.h;
    const rangeOf = (sites: Site[]): [number, number] => {
      let lo = 0;
      let hi = 0;
      for (const s of sites)
        for (const r of s.hist)
          for (const v of [r[4], r[5]]) {
            if (v === null) continue;
            lo = Math.min(lo, v);
            hi = Math.max(hi, v);
          }
      return [lo, hi || 1];
    };
    this.range = { water: rangeOf(coast.water.stations), waves: rangeOf(coast.waves.buoys) };
    this.leads = Array.from(new Set(coast.water.stations.flatMap((s) => s.hist.map((r) => r[10])))).sort((a, b) => a - b);
    this.bind();
    this.resizeObs = new ResizeObserver(() => {
      this.resize();
      this.focus(this.view, false);
    });
    this.resizeObs.observe(canvas);
    this.resize();
    this.focus("all", false);
    this.raf = requestAnimationFrame(this.frame);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.resizeObs.disconnect();
    this.unbind();
  }

  set(state: Partial<SceneState>) {
    this.s = { ...this.s, ...state };
  }

  // ---- Camera ---------------------------------------------------------------------------------

  private boxOf(view: View): Box {
    if (view === "coast") return this.L.coastGroup;
    if (view === "sky") return { x: this.L.sky.box.x, y: 0, w: this.L.sky.box.w, h: this.L.sky.box.y + this.L.sky.box.h };
    return this.L.world;
  }

  /** Tell the camera how much of the stage's right edge the side panel covers. */
  setInset(right: number, animate = true) {
    const r = Math.max(0, Math.min(this.cw * 0.6, right));
    if (r === this.insetR) return;
    this.insetR = r;
    this.focus(this.view, animate);
  }

  focus(view: View, animate = true) {
    this.view = view;
    const b = this.boxOf(view);
    const padX = 28;
    const padY = 22;
    const availW = Math.max(1, this.cw - this.insetR);
    const z = Math.min(availW / (b.w + padX * 2), this.ch / (b.h + padY * 2));
    // Centre the box in the uncovered part of the stage.
    const to = { x: b.x + b.w / 2 + this.insetR / 2 / z, y: b.y + b.h / 2, z };
    if (animate && !this.reduced) this.camTo = to;
    else {
      this.cam = to;
      this.camTo = null;
    }
  }

  private resize() {
    const rect = this.canvas.getBoundingClientRect();
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.cw = Math.max(1, rect.width);
    this.ch = Math.max(1, rect.height);
    this.canvas.width = Math.round(this.cw * this.dpr);
    this.canvas.height = Math.round(this.ch * this.dpr);
  }

  private toScreen(x: number, y: number) {
    return { x: (x - this.cam.x) * this.cam.z + this.cw / 2, y: (y - this.cam.y) * this.cam.z + this.ch / 2 };
  }

  private toWorld(sx: number, sy: number) {
    return { x: (sx - this.cw / 2) / this.cam.z + this.cam.x, y: (sy - this.ch / 2) / this.cam.z + this.cam.y };
  }

  /** Screen position of a target, for anchoring the hover card during the tour. */
  screenOf(t: Target): { x: number; y: number } | null {
    const p = this.anchorOf(t);
    return p ? this.toScreen(p.x, p.y) : null;
  }

  private anchorOf(t: Target): { x: number; y: number } | null {
    if (t.type === "gauge" || t.type === "buoy") {
      const site = this.siteById(t);
      if (!site) return null;
      return project(this.L.coast, site.lon, site.lat);
    }
    if (t.type === "cell" && this.turb) {
      const { lats, lons } = this.turb.grid;
      return project(this.L.sky, lons[t.j], lats[t.i]);
    }
    if (t.type === "report" && this.turb) {
      const c = cycleAt(this.turb, this.s.t);
      const r = c?.reports[t.index];
      return r ? project(this.L.sky, r[1], r[0]) : null;
    }
    return null;
  }

  private siteById(t: { type: "gauge" | "buoy"; id: string }) {
    return (t.type === "gauge" ? this.coast.water.stations : this.coast.waves.buoys).find((s) => s.id === t.id) ?? null;
  }

  // ---- Frame ----------------------------------------------------------------------------------

  private frame = (now: number) => {
    const dt = Math.min(0.1, this.lastNow ? (now - this.lastNow) / 1000 : 0);
    this.lastNow = now;
    this.real += dt;
    if (this.camTo) {
      const k = Math.min(1, dt * 5);
      this.cam.x += (this.camTo.x - this.cam.x) * k;
      this.cam.y += (this.camTo.y - this.cam.y) * k;
      this.cam.z += (this.camTo.z - this.cam.z) * k;
      if (Math.abs(this.camTo.x - this.cam.x) < 0.2 && Math.abs(this.camTo.y - this.cam.y) < 0.2 && Math.abs(this.camTo.z - this.cam.z) < 0.002) {
        this.cam = { ...this.camTo };
        this.camTo = null;
      }
    }
    this.draw();
    this.raf = requestAnimationFrame(this.frame);
  };

  private draw() {
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = C.night;
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    const s = this.cam.z * this.dpr;
    const ox = Math.round((this.cw / 2 - this.cam.x * this.cam.z) * this.dpr);
    const oy = Math.round((this.ch / 2 - this.cam.y * this.cam.z) * this.dpr);
    ctx.setTransform(s, 0, 0, s, ox, oy);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this.staticCv, 0, 0);

    const cyc = this.turb ? cycleAt(this.turb, this.s.t) : null;
    if (this.turb && cyc) {
      this.paintSky(cyc);
      ctx.drawImage(this.skyCv, this.L.sky.box.x, this.L.sky.box.y);
      this.drawReports(cyc);
      this.drawCellMark();
    }
    this.drawBoard("buoy");
    this.drawBoard("gauge");
    const gap = gapAt(this.s.gaps, this.s.t);
    if (gap) this.drawGapShade();

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.drawText(cyc, gap);
  }

  // ---- The sky --------------------------------------------------------------------------------

  /** Paint the sealed grid of one cycle and level into the sky canvas, dithered like pixel art:
   * the denser and warmer a cell, the higher its sealed probability. */
  private paintSky(c: TurbCycle) {
    const turb = this.turb!;
    const key = `${c.seq}:${this.s.level}`;
    if (key === this.skyKey) return;
    this.skyKey = key;
    const { lats, lons, resolution_deg } = turb.grid;
    const f = this.L.sky;
    const { w, h } = f.box;
    const ctx = this.skyCv.getContext("2d")!;
    ctx.clearRect(0, 0, w, h);
    const img = ctx.createImageData(w, h);
    const pmax = this.levelMax(c);
    const ramp = RAMP.map(hexRgb);
    const half = resolution_deg / 2;
    for (let i = 0; i < lats.length; i++) {
      for (let j = 0; j < lons.length; j++) {
        const p = riskAt(turb, c, this.s.level, i, j);
        if (p === null || pmax <= 0) continue;
        const u = Math.min(1, p / pmax);
        const a = project(f, lons[j] - half, lats[i] + half);
        const b = project(f, lons[j] + half, lats[i] - half);
        const col = ramp[Math.min(ramp.length - 1, Math.floor(u * ramp.length))];
        const density = 0.12 + 0.78 * u;
        for (let y = Math.max(0, Math.round(a.y - f.box.y)); y < Math.min(h, Math.round(b.y - f.box.y)); y++) {
          for (let x = Math.max(0, Math.round(a.x - f.box.x)); x < Math.min(w, Math.round(b.x - f.box.x)); x++) {
            if (BAYER[(y % 4) * 4 + (x % 4)] > density) continue;
            const k = (y * w + x) * 4;
            img.data[k] = col[0];
            img.data[k + 1] = col[1];
            img.data[k + 2] = col[2];
            img.data[k + 3] = 235;
          }
        }
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  /** The highest sealed probability on the shown level, the top of the legend. */
  levelMax(c: TurbCycle): number {
    const turb = this.turb!;
    const { lats, lons } = turb.grid;
    let m = 0;
    for (let i = 0; i < lats.length; i++) for (let j = 0; j < lons.length; j++) m = Math.max(m, riskAt(turb, c, this.s.level, i, j) ?? 0);
    return m;
  }

  private reportGraded(c: TurbCycle) {
    return c.graded !== null && c.graded <= this.s.t;
  }

  private drawReports(c: TurbCycle) {
    if (!this.reportGraded(c)) return;
    const ctx = this.ctx;
    const fls = this.turb!.grid.flight_levels;
    c.reports.forEach((r, k) => {
      const onLevel = nearestIndex(fls, r[2]) === this.s.level;
      const p = project(this.L.sky, r[1], r[0]);
      const x = Math.round(p.x);
      const y = Math.round(p.y);
      const cls = reportClass(r[3]);
      const color = cls === "pos" ? C.red : cls === "neg" ? C.green : C.mist;
      const hot = (this.hover?.type === "report" && this.hover.index === k) || (this.s.selected?.type === "report" && this.s.selected.index === k);
      ctx.globalAlpha = onLevel || hot ? 1 : 0.35;
      // A small aircraft: a cross with an ink outline.
      ctx.fillStyle = C.ink;
      ctx.fillRect(x - 3, y - 1, 7, 3);
      ctx.fillRect(x - 1, y - 3, 3, 7);
      ctx.fillStyle = hot ? C.snow : color;
      ctx.fillRect(x - 2, y, 5, 1);
      ctx.fillRect(x, y - 2, 1, 5);
      if (onLevel && cls === "pos") ctx.fillRect(x - 1, y - 1, 3, 3);
      ctx.globalAlpha = 1;
    });
  }

  private drawCellMark() {
    const turb = this.turb!;
    for (const t of [this.hover, this.s.selected]) {
      if (t?.type !== "cell") continue;
      const half = turb.grid.resolution_deg / 2;
      const a = project(this.L.sky, turb.grid.lons[t.j] - half, turb.grid.lats[t.i] + half);
      const b = project(this.L.sky, turb.grid.lons[t.j] + half, turb.grid.lats[t.i] - half);
      this.rectOutline(Math.round(a.x), Math.round(a.y), Math.round(b.x - a.x), Math.round(b.y - a.y), t === this.hover ? C.snow : C.gold);
    }
  }

  private rectOutline(x: number, y: number, w: number, h: number, color: string) {
    const ctx = this.ctx;
    ctx.fillStyle = color;
    ctx.fillRect(x, y, w, 1);
    ctx.fillRect(x, y + h - 1, w, 1);
    ctx.fillRect(x, y, 1, h);
    ctx.fillRect(x + w - 1, y, 1, h);
  }

  // ---- The coast ------------------------------------------------------------------------------

  private boardOf(kind: "gauge" | "buoy") {
    const sites = kind === "gauge" ? this.coast.water.stations : this.coast.waves.buoys;
    const rows = kind === "gauge" ? this.L.gaugeRows : this.L.buoyRows;
    const board = kind === "gauge" ? this.L.gaugeBoard : this.L.buoyBoard;
    const model = kind === "gauge" ? this.s.waterModel : this.s.wavesModel;
    const range = kind === "gauge" ? this.range.water : this.range.waves;
    return { sites, rows, board, model, range };
  }

  private rowBox(kind: "gauge" | "buoy", id: string): Box {
    const { rows, board, sites } = this.boardOf(kind);
    const y = rowTop(this.L, board, rows.get(id) ?? 0, sites.length);
    return { x: board.x, y, w: board.w, h: this.L.rowH };
  }

  /** Columns of a board row: name, bars, error. Gauges read left to right from the map; buoys mirror. */
  private cols(kind: "gauge" | "buoy", b: Box) {
    const nameW = 62;
    const errW = 26;
    const barW = b.w - nameW - errW - 6;
    return kind === "gauge"
      ? { name: { x: b.x + 4, w: nameW }, bars: { x: b.x + nameW + 2, w: barW }, err: { x: b.x + b.w - errW, w: errW - 3 } }
      : { name: { x: b.x + b.w - nameW - 4, w: nameW }, bars: { x: b.x + errW + 2, w: barW }, err: { x: b.x + 3, w: errW - 3 } };
  }

  private drawBoard(kind: "gauge" | "buoy") {
    const ctx = this.ctx;
    const { sites, model, range } = this.boardOf(kind);
    for (const site of sites) {
      const tgt: Target = { type: kind, id: site.id };
      const hot = same(this.hover, tgt);
      const sel = same(this.s.selected, tgt);
      const rb = this.rowBox(kind, site.id);
      const v = siteAt(site, model, this.s.t);
      const idx = (kind === "gauge" ? this.L.gaugeRows : this.L.buoyRows).get(site.id) ?? 0;
      ctx.fillStyle = sel ? C.night4 : hot ? C.night3 : idx % 2 ? C.boardAlt : C.board;
      ctx.fillRect(rb.x, rb.y, rb.w, rb.h);
      if (sel) {
        ctx.fillStyle = C.gold;
        ctx.fillRect(kind === "gauge" ? rb.x : rb.x + rb.w - 2, rb.y, 2, rb.h);
      }
      // Bars: the latest graded forecast in gold, the reading that graded it in glass blue, and a
      // gold tick where the newer forecast, sealed and still waiting, points.
      const col = this.cols(kind, rb);
      const [lo, hi] = range;
      const sx = (val: number) => col.bars.x + ((val - lo) / (hi - lo)) * col.bars.w;
      const zero = Math.round(sx(0));
      const barH = Math.max(2, Math.floor((rb.h - 4) / 2));
      const top = rb.y + Math.floor((rb.h - barH * 2 - 1) / 2);
      ctx.fillStyle = C.night4;
      ctx.fillRect(zero, rb.y + 1, 1, rb.h - 2);
      if (v.last && v.last[4] !== null) {
        const x = Math.round(sx(v.last[4]));
        ctx.fillStyle = C.gold;
        ctx.fillRect(Math.min(zero, x), top, Math.max(1, Math.abs(x - zero)), barH);
      }
      if (v.last && v.last[5] !== null) {
        const x = Math.round(sx(v.last[5]));
        ctx.fillStyle = C.glass;
        ctx.fillRect(Math.min(zero, x), top + barH + 1, Math.max(1, Math.abs(x - zero)), barH);
      } else {
        // Nothing graded yet: a dotted promise where the reading will go.
        ctx.fillStyle = C.lineHi;
        for (let x = col.bars.x; x < col.bars.x + col.bars.w; x += 3) ctx.fillRect(x, top + barH + Math.floor(barH / 2), 1, 1);
      }
      if (v.row && !v.graded && v.row[4] !== null) {
        const x = Math.round(sx(v.row[4]));
        ctx.fillStyle = C.ink;
        ctx.fillRect(x - 1, rb.y + 1, 3, rb.h - 2);
        ctx.fillStyle = C.goldHi;
        ctx.fillRect(x, rb.y + 1, 1, rb.h - 2);
      }
      // The leader line to the site on the map, and the site itself.
      const p = project(this.L.coast, site.lon, site.lat);
      const ax = kind === "gauge" ? rb.x - 1 : rb.x + rb.w;
      const ay = rb.y + Math.floor(rb.h / 2);
      this.line(Math.round(p.x), Math.round(p.y), ax, ay, hot || sel ? C.gold : C.line);
      this.drawSite(kind, Math.round(p.x), Math.round(p.y), v.row, v.graded, hot || sel);
    }
  }

  private drawSite(kind: "gauge" | "buoy", x: number, y: number, row: HistRow | null, graded: boolean, hot: boolean) {
    const ctx = this.ctx;
    const pending = row && !graded;
    const blink = pending && !this.reduced && Math.floor(this.real * 1.6) % 2 === 0;
    const fill = !row ? C.fog : graded ? C.glass : blink ? C.goldHi : C.gold;
    ctx.fillStyle = hot ? C.snow : C.ink;
    if (kind === "gauge") {
      // A tide staff: a post with a coloured head.
      ctx.fillRect(x - 2, y - 2, 5, 5);
      ctx.fillStyle = fill;
      ctx.fillRect(x - 1, y - 1, 3, 3);
    } else {
      // A buoy: a round float with a light on top.
      ctx.fillRect(x - 2, y - 1, 5, 4);
      ctx.fillRect(x - 1, y - 3, 3, 2);
      ctx.fillStyle = fill;
      ctx.fillRect(x - 1, y, 3, 2);
      ctx.fillStyle = C.red;
      ctx.fillRect(x, y - 2, 1, 1);
    }
  }

  /** A one-pixel line, stepped like pixel art. */
  private line(x0: number, y0: number, x1: number, y1: number, color: string) {
    const ctx = this.ctx;
    ctx.fillStyle = color;
    const n = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
    for (let k = 0; k <= n; k += 1) {
      const x = Math.round(x0 + ((x1 - x0) * k) / (n || 1));
      const y = Math.round(y0 + ((y1 - y0) * k) / (n || 1));
      if (k % 2 === 0 || color !== C.line) ctx.fillRect(x, y, 1, 1);
    }
  }

  private hatch: CanvasPattern | null = null;

  /** While the timeline is inside a gap, the scene is hatched over: nothing new was sealed. */
  private drawGapShade() {
    const ctx = this.ctx;
    if (!this.hatch) {
      const p = document.createElement("canvas");
      p.width = 8;
      p.height = 8;
      const pc = p.getContext("2d")!;
      pc.fillStyle = "rgba(133, 144, 186, 0.35)";
      for (let k = 0; k < 8; k++) pc.fillRect(k, 7 - k, 1, 1);
      this.hatch = ctx.createPattern(p, "repeat");
    }
    for (const b of [{ x: this.L.coastGroup.x, y: this.L.coast.box.y, w: this.L.coastGroup.w, h: this.L.coast.box.h }, this.L.sky.box]) {
      ctx.fillStyle = "rgba(22, 26, 51, 0.5)";
      ctx.fillRect(b.x, b.y, b.w, b.h);
      if (this.hatch) {
        ctx.fillStyle = this.hatch;
        ctx.fillRect(b.x, b.y, b.w, b.h);
      }
    }
  }

  // ---- Screen text ----------------------------------------------------------------------------

  private sign(text: string, x: number, y: number, band: string, align: CanvasTextAlign = "left", size = 15) {
    const ctx = this.ctx;
    ctx.font = `600 ${size}px "Pixelify Sans", system-ui, sans-serif`;
    const w = ctx.measureText(text).width + size * 1.1;
    const h = size * 1.6;
    const left = align === "left" ? x : align === "center" ? x - w / 2 : x - w;
    ctx.fillStyle = "rgba(22, 26, 51, 0.92)";
    ctx.fillRect(Math.round(left), Math.round(y - h / 2), Math.round(w), Math.round(h));
    ctx.fillStyle = band;
    ctx.fillRect(Math.round(left), Math.round(y + h / 2) - 2, Math.round(w), 2);
    ctx.fillStyle = C.snow;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(text, left + size * 0.55, y + 0.5);
  }

  private drawText(cyc: TurbCycle | null, gap: Gap | null) {
    const ctx = this.ctx;
    const z = this.cam.z;
    const sign = Math.max(11, Math.min(17, 5.4 * z));
    // Captions over each group.
    const wm = this.coast.water.models[this.s.waterModel];
    const cTop = this.toScreen(this.L.coastGroup.x, this.L.coast.box.y - 15);
    this.sign(`West Coast, ${this.leads.join(", ")} h ahead: ${modelName(wm)}`, cTop.x, cTop.y, C.gold, "left", sign);
    if (this.turb) {
      const s = this.toScreen(this.L.sky.box.x, this.L.sky.box.y - 15);
      const fl = this.turb.grid.flight_levels[this.s.level];
      const cap = cyc
        ? `Sky at FL${fl}: ${modelName(this.turb.featured).toLowerCase()}, valid ${fmtMin(cyc.valid)}`
        : `Sky at FL${fl}: nothing sealed yet`;
      this.sign(cap, s.x, s.y, C.red, "left", sign);
    }
    // Board text, only when the rows are tall enough to read.
    const fs = Math.max(9, Math.min(15, this.L.rowH * z * 0.62));
    if (this.L.rowH * z < 9) return this.drawGapSign(gap, sign);
    ctx.font = `500 ${fs}px "Atkinson Hyperlegible Next Variable", system-ui, sans-serif`;
    ctx.textBaseline = "middle";
    for (const kind of ["gauge", "buoy"] as const) {
      const { sites, model } = this.boardOf(kind);
      for (const site of sites) {
        const rb = this.rowBox(kind, site.id);
        const col = this.cols(kind, rb);
        const v = siteAt(site, model, this.s.t);
        const yMid = this.toScreen(0, rb.y + rb.h / 2).y;
        const n = this.toScreen(col.name.x, 0).x;
        const nw = col.name.w * z;
        ctx.fillStyle = same(this.s.selected, { type: kind, id: site.id }) ? C.goldHi : C.snow;
        ctx.textAlign = kind === "gauge" ? "left" : "right";
        this.fitText(shortName(site.name), kind === "gauge" ? n : n + nw, yMid, nw);
        const e = this.toScreen(kind === "gauge" ? col.err.x + col.err.w : col.err.x, 0).x;
        ctx.textAlign = kind === "gauge" ? "right" : "left";
        if (v.last && v.last[6] !== null) {
          ctx.fillStyle = C.mist;
          ctx.fillText(v.last[6].toFixed(1), e, yMid);
        } else if (v.row) {
          ctx.fillStyle = C.gold;
          ctx.fillText("due", e, yMid);
        } else {
          ctx.fillStyle = C.fog;
          ctx.fillText("none", e, yMid);
        }
      }
    }
    this.drawGapSign(gap, sign);
  }

  /** The gap, said plainly over the scene. */
  private drawGapSign(gap: Gap | null, sign: number) {
    if (!gap) return;
    const mid = this.toScreen(this.L.world.w / 2, this.L.world.h / 2);
    this.sign(`Nothing sealed for ${fmtHours(gap.hours)}: ${fmtMin(gap.from_min)} to ${fmtMin(gap.to_min)}`, mid.x, mid.y, C.fog, "center", Math.max(13, sign + 2));
  }

  private fitText(text: string, x: number, y: number, w: number) {
    const ctx = this.ctx;
    let s = text;
    while (s.length > 3 && ctx.measureText(s).width > w) s = s.slice(0, -2) + "…";
    ctx.fillText(s, x, y);
  }

  // ---- Input ----------------------------------------------------------------------------------

  private hitTest(sx: number, sy: number): Target | null {
    const w = this.toWorld(sx, sy);
    const near = 5 / Math.max(0.6, this.cam.z) + 2;
    // Pilot reports on the shown level first, then any report.
    const cyc = this.turb ? cycleAt(this.turb, this.s.t) : null;
    if (cyc && this.reportGraded(cyc)) {
      let best = -1;
      let bestD = near;
      cyc.reports.forEach((r, k) => {
        const p = project(this.L.sky, r[1], r[0]);
        const d = Math.hypot(p.x - w.x, p.y - w.y);
        const on = nearestIndex(this.turb!.grid.flight_levels, r[2]) === this.s.level;
        if (d < bestD + (on ? 1.5 : 0)) {
          bestD = d;
          best = k;
        }
      });
      if (best >= 0) return { type: "report", index: best };
    }
    const f = this.L.sky.box;
    if (this.turb && w.x >= f.x && w.x < f.x + f.w && w.y >= f.y && w.y < f.y + f.h) {
      const g = unproject(this.L.sky, w.x, w.y);
      const { lats, lons, resolution_deg } = this.turb.grid;
      const i = nearestIndex(lats, g.lat);
      const j = nearestIndex(lons, g.lon);
      if (Math.abs(lats[i] - g.lat) <= resolution_deg / 2 && Math.abs(lons[j] - g.lon) <= resolution_deg / 2) return { type: "cell", level: this.s.level, i, j };
      return null;
    }
    // Sites on the map.
    for (const kind of ["gauge", "buoy"] as const) {
      const sites = kind === "gauge" ? this.coast.water.stations : this.coast.waves.buoys;
      for (const s of sites) {
        const p = project(this.L.coast, s.lon, s.lat);
        if (Math.hypot(p.x - w.x, p.y - w.y) < near * 0.8) return { type: kind, id: s.id };
      }
    }
    // Board rows.
    for (const kind of ["gauge", "buoy"] as const) {
      const { sites, board } = this.boardOf(kind);
      if (w.x < board.x || w.x > board.x + board.w) continue;
      for (const s of sites) {
        const rb = this.rowBox(kind, s.id);
        if (w.y >= rb.y && w.y < rb.y + rb.h) return { type: kind, id: s.id };
      }
    }
    return null;
  }

  private pos(e: MouseEvent) {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private onMove = (e: PointerEvent) => {
    const p = this.pos(e);
    const hit = this.hitTest(p.x, p.y);
    if (!same(hit, this.hover)) this.hover = hit;
    this.cb.onHover(hit, p.x, p.y);
    this.canvas.style.cursor = hit ? "pointer" : "default";
  };

  private onLeave = () => {
    this.hover = null;
    this.cb.onHover(null, 0, 0);
  };

  private onClick = (e: MouseEvent) => {
    const p = this.pos(e);
    this.cb.onSelect(this.hitTest(p.x, p.y));
  };

  private bind() {
    this.canvas.addEventListener("pointermove", this.onMove);
    this.canvas.addEventListener("pointerleave", this.onLeave);
    this.canvas.addEventListener("click", this.onClick);
  }

  private unbind() {
    this.canvas.removeEventListener("pointermove", this.onMove);
    this.canvas.removeEventListener("pointerleave", this.onLeave);
    this.canvas.removeEventListener("click", this.onClick);
  }
}
