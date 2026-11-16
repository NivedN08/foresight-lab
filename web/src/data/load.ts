// Loads the snapshot written by build_data.py, and, when the project's dashboard server answers
// on /api (proxied in development), refreshes the live numbers from it.

import type { Coast, Data, Geo, Meta, Research, Turb } from "./types";

async function get<T>(name: string): Promise<T | null> {
  try {
    const r = await fetch(`data/${name}`, { cache: "no-cache" });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export async function loadSnapshot(): Promise<Data> {
  const meta = await get<Meta>("meta.json");
  if (!meta) throw new Error("No snapshot found. Run python3 web/build_data.py first.");
  const [coast, turb, research, geo] = await Promise.all([get<Coast>("coast.json"), get<Turb>("turb.json"), get<Research>("research.json"), get<Geo>("geo.json")]);
  return { meta, coast, turb, research, geo };
}

export type Live = { at: string; coast: boolean; turb: boolean };

async function api(path: string): Promise<Record<string, unknown> | null> {
  try {
    const ctl = new AbortController();
    const timer = window.setTimeout(() => ctl.abort(), 4000);
    const r = await fetch(path, { cache: "no-store", signal: ctl.signal });
    window.clearTimeout(timer);
    if (!r.ok || !(r.headers.get("content-type") ?? "").includes("json")) return null;
    const body = (await r.json()) as Record<string, unknown>;
    // A server with no live job answers {"empty": true}; that is not live data.
    return body && !body.empty && body.ledger ? body : null;
  } catch {
    return null;
  }
}

/** Copy the live jobs' newest summary numbers over the snapshot. The ledger history, the scene and
 * the timeline stay as snapshotted; only counts, hashes and scoreboards move. Returns null when
 * the dashboard server does not answer. */
export async function refreshLive(d: Data): Promise<{ data: Data; live: Live } | null> {
  const [c, t] = await Promise.all([api("/api/coast/state"), api("/api/turb/state")]);
  if (!c && !t) return null;
  const next: Data = { ...d };
  if (c && d.coast) {
    const water = (c.water ?? {}) as Record<string, unknown>;
    const waves = (c.waves ?? {}) as Record<string, unknown>;
    next.coast = {
      ...d.coast,
      generated_at: (c.generated_at as string) ?? d.coast.generated_at,
      ledger: (c.ledger as Coast["ledger"]) ?? d.coast.ledger,
      water: {
        ...d.coast.water,
        scoreboard: (water.scoreboard as Coast["water"]["scoreboard"]) ?? d.coast.water.scoreboard,
        scoreboard_paired: (water.scoreboard_paired as Coast["water"]["scoreboard_paired"]) ?? d.coast.water.scoreboard_paired,
      },
      waves: { ...d.coast.waves, scoreboard: (waves.scoreboard as Coast["waves"]["scoreboard"]) ?? d.coast.waves.scoreboard },
    };
  }
  if (t && d.turb) {
    next.turb = {
      ...d.turb,
      generated_at: (t.generated_at as string) ?? d.turb.generated_at,
      ledger: (t.ledger as Turb["ledger"]) ?? d.turb.ledger,
      scoreboard: (t.scoreboard as Turb["scoreboard"]) ?? d.turb.scoreboard,
    };
  }
  return { data: next, live: { at: new Date().toISOString(), coast: !!c, turb: !!t } };
}
