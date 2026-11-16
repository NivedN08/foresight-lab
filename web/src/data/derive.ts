// Small lookups over the snapshot: what had been sealed and graded by a moment on the timeline.

import type { Coast, Data, Gap, HistRow, Site, Turb, TurbCycle } from "./types";

export const MIN_MS = 60_000;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const pad = (n: number) => String(n).padStart(2, "0");

/** "Nov 16, 10:15 UTC" */
export function fmtMin(m: number | null | undefined, withZone = true): string {
  if (m === null || m === undefined) return "not yet";
  const d = new Date(m * MIN_MS);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${withZone ? " UTC" : ""}`;
}

export function fmtIso(s: string | null | undefined, withZone = true): string {
  return s ? fmtMin(isoMin(s), withZone) : "not recorded";
}

export function fmtDay(m: number): string {
  const d = new Date(m * MIN_MS);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

export function fmtClock(m: number): string {
  const d = new Date(m * MIN_MS);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

export function isoMin(s: string): number {
  return Math.floor(Date.parse(s) / MIN_MS);
}

export function fmtHours(h: number): string {
  return `${Number.isInteger(h) ? h : h.toFixed(1)} h`;
}

export function fmtInt(n: number | null | undefined): string {
  return n === null || n === undefined ? "n/a" : n.toLocaleString("en-US");
}

export function fmtNum(n: number | null | undefined, dp = 1): string {
  return n === null || n === undefined || Number.isNaN(n) ? "n/a" : n.toFixed(dp);
}

export function fmtSigned(n: number | null | undefined, dp = 2): string {
  if (n === null || n === undefined) return "n/a";
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(dp)}`;
}

export function pct(n: number | null | undefined, dp = 0): string {
  return n === null || n === undefined ? "n/a" : `${(n * 100).toFixed(dp)}%`;
}

/** The project's own names for its models, said plainly. */
export function modelName(m: string): string {
  if (m.startsWith("Lab:")) return `Lab forecaster ${m.slice(4)}`;
  const names: Record<string, string> = {
    TideTable: "Official tide table",
    Persistence: "The current gap continues",
    DampedPersistence: "Damped version",
    SeedPhysics: "Hand-written starting formula",
    SeedSwell: "Hand-written starting formula",
    WaveModel: "Open-Meteo wave model",
    SeedBlend: "Hand-written starting formula",
    Climatology: "Climatology",
    WindSpeed: "Wind speed",
    Shear: "Wind shear",
    Richardson: "Richardson number",
    Ellrod1: "Ellrod index",
    GAirmet: "Official G-AIRMET",
  };
  return names[m] ?? m;
}

export const isLab = (m: string) => m.startsWith("Lab:");

/** The time span the page covers: an hour before the first seal to the snapshot. */
export function span(d: Data): { lo: number; hi: number } {
  const firsts = [d.coast?.first_seal, d.turb?.first_seal].filter(Boolean).map((s) => isoMin(s as string));
  const lasts = [d.coast?.generated_at, d.turb?.generated_at, d.meta.snapshot_at].filter(Boolean).map((s) => isoMin(s as string));
  const lo = Math.min(...firsts) - 60;
  const hi = Math.max(...lasts, ...firsts);
  return { lo, hi };
}

// ---- Coast ------------------------------------------------------------------------------------

export type SiteView = {
  /** The model's latest forecast sealed by t, if any (with one-hour leads, usually still waiting). */
  row: HistRow | null;
  /** Graded by t? (the grade exists and its time has passed) */
  graded: boolean;
  /** The model's latest forecast already graded by t: what the boards draw against its reading. */
  last: HistRow | null;
  /** Every model's forecast for the same issue time as `row`. */
  peers: HistRow[];
};

/** The latest forecast of `model` sealed at or before t, its grade as known at t, and the latest
 * forecast already graded by t. */
export function siteAt(site: Site, model: number, t: number): SiteView {
  let row: HistRow | null = null;
  let last: HistRow | null = null;
  for (const r of site.hist) {
    if (r[1] > t) break;
    if (r[3] !== model) continue;
    row = r;
    if (r[7] !== null && r[7] <= t && (!last || r[2] >= last[2])) last = r;
  }
  if (!row) return { row: null, graded: false, last: null, peers: [] };
  return { row, graded: row[7] !== null && row[7] <= t, last, peers: peersOf(site, row, t) };
}

/** Every model's forecast for the same issue time and lead as `row`, sealed by t. */
export function peersOf(site: Site, row: HistRow, t: number): HistRow[] {
  return site.hist.filter((r) => r[0] === row[0] && r[10] === row[10] && r[1] <= t);
}

export function coastModelIndex(c: Coast, kind: "water" | "waves", name: string): number {
  return c[kind].models.indexOf(name);
}

/** The featured water model: the first promoted lab forecaster, else the starting formula. */
export function featuredWater(c: Coast): string {
  return c.water.promoted[0]?.model ?? c.water.models.find((m) => m.startsWith("Seed")) ?? c.water.models[0];
}

export function featuredWaves(c: Coast): string {
  return c.waves.models.find(isLab) ?? c.waves.models.find((m) => m.startsWith("Seed")) ?? c.waves.models[0];
}

// ---- Turbulence -------------------------------------------------------------------------------

/** The grid the sky shows at t: the latest one graded by t, so its pilot reports can be drawn,
 * else the latest one sealed. */
export function cycleAt(turb: Turb, t: number): TurbCycle | null {
  let sealed: TurbCycle | null = null;
  let graded: TurbCycle | null = null;
  for (const x of turb.cycles) {
    if (x.sealed > t) continue;
    sealed = x;
    if (x.graded !== null && x.graded <= t && (!graded || x.valid >= graded.valid)) graded = x;
  }
  return graded ?? sealed;
}

/** The newest grid sealed by t, when it is newer than the one shown and still waiting for reports. */
export function waitingCycleAt(turb: Turb, t: number): TurbCycle | null {
  let sealed: TurbCycle | null = null;
  for (const x of turb.cycles) if (x.sealed <= t) sealed = x;
  const shown = cycleAt(turb, t);
  return sealed && shown && sealed !== shown && !(sealed.graded !== null && sealed.graded <= t) ? sealed : null;
}

export function riskAt(turb: Turb, c: TurbCycle, level: number, i: number, j: number): number | null {
  const { lats, lons } = turb.grid;
  const v = c.risk[level * lats.length * lons.length + i * lons.length + j];
  return v === undefined || v < 0 ? null : v / 1000;
}

export function nearestIndex(xs: number[], x: number): number {
  let best = 0;
  for (let k = 1; k < xs.length; k++) if (Math.abs(xs[k] - x) < Math.abs(xs[best] - x)) best = k;
  return best;
}

/** Pilot report codes as the project labels them: 4 and up is moderate or greater, 2 and below
 * is a negative, 3 (light to moderate) is dropped as ambiguous. */
export function reportClass(code: number): "pos" | "neg" | "mid" {
  return code >= 4 ? "pos" : code <= 2 ? "neg" : "mid";
}

export const CODE_LABEL: Record<number, string> = {
  0: "smooth",
  1: "smooth to light",
  2: "light",
  3: "light to moderate",
  4: "moderate",
  5: "moderate to severe",
  6: "severe",
  7: "severe to extreme",
  8: "extreme",
};

/** Brier score of a sealed grid on the classed reports of its grade, each read at the nearest grid
 * cell and level. The live job's own scoring interpolates; this is the page's simpler reading. */
export function brierOf(turb: Turb, c: TurbCycle, filter?: (rep: TurbCycle["reports"][number]) => boolean) {
  const { lats, lons, flight_levels } = turb.grid;
  let sum = 0;
  let n = 0;
  for (const rep of c.reports) {
    if (filter && !filter(rep)) continue;
    const cls = reportClass(rep[3]);
    if (cls === "mid") continue;
    const p = riskAt(turb, c, nearestIndex(flight_levels, rep[2]), nearestIndex(lats, rep[0]), nearestIndex(lons, rep[1]));
    if (p === null) continue;
    const y = cls === "pos" ? 1 : 0;
    sum += (p - y) ** 2;
    n++;
  }
  return { n, brier: n ? sum / n : null };
}

// ---- Gaps -------------------------------------------------------------------------------------

export function allGaps(d: Data): Gap[] {
  return [...(d.coast?.gaps ?? []), ...(d.turb?.gaps ?? [])];
}

/** The longest gap of either job. */
export function longestGap(d: Data): Gap | null {
  return allGaps(d).reduce<Gap | null>((a, g) => (!a || g.hours > a.hours ? g : a), null);
}

export function gapAt(gaps: Gap[], t: number): Gap | null {
  return gaps.find((g) => t > g.from_min && t < g.to_min) ?? null;
}
