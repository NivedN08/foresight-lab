import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { loadSnapshot, refreshLive, type Live } from "./data/load";
import type { Data } from "./data/types";
import { allGaps, cycleAt, featuredWater, featuredWaves, fmtHours, fmtInt, fmtIso, fmtMin, gapAt, modelName, nearestIndex, span, waitingCycleAt } from "./data/derive";
import { SceneEngine, type Target, type View } from "./scene/engine";
import { RAMP, C } from "./scene/palette";
import { Details } from "./ui/Details";
import { Research } from "./ui/Research";
import { Scoreboard } from "./ui/Scoreboard";
import { Timeline, type Track } from "./ui/Timeline";
import { buildTour, type Tab } from "./ui/tour";
import { DataPanel } from "./ui/DataPanel";

const PANELS: [Tab, string][] = [
  ["scoreboard", "Scoreboard"],
  ["research", "Research"],
  ["details", "Details"],
  ["data", "Data"],
];

const PLAY_MIN_PER_S = 180; // three hours of ledger per second of playback
const AUTO_MS = 16000;

export function App() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<Live | null>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [view, setView] = useState<View>("all");
  const [level, setLevel] = useState(0);
  const [waterModel, setWaterModel] = useState(0);
  const [wavesModel, setWavesModel] = useState(0);
  const [hover, setHover] = useState<{ target: Target; x: number; y: number } | null>(null);
  const [selected, setSelected] = useState<Target | null>(null);
  /** The open side panel, or null when it is closed and the scene has the whole stage. */
  const [tab, setTab] = useState<Tab | null>("scoreboard");
  const [tour, setTour] = useState<number | null>(null);
  const [auto, setAuto] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<SceneEngine | null>(null);

  // ---- Loading ----------------------------------------------------------------------------------
  useEffect(() => {
    loadSnapshot()
      .then((d) => {
        setData(d);
        const { hi } = span(d);
        setT(hi);
        if (d.coast) {
          setWaterModel(Math.max(0, d.coast.water.models.indexOf(featuredWater(d.coast))));
          setWavesModel(Math.max(0, d.coast.waves.models.indexOf(featuredWaves(d.coast))));
        }
        if (d.turb) {
          // Start on the level where most graded pilot reports sit.
          const counts = d.turb.grid.flight_levels.map(() => 0);
          for (const c of d.turb.cycles) for (const r of c.reports) counts[nearestIndex(d.turb.grid.flight_levels, r[2])]++;
          setLevel(counts.indexOf(Math.max(...counts)));
        }
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  // The dashboard server, when it runs, refreshes the live numbers every minute.
  const snapshotRef = useRef<Data | null>(null);
  useEffect(() => {
    // Only the dev server proxies /api; a built copy is a static snapshot and never asks.
    if (!import.meta.env.DEV || !data || snapshotRef.current) return;
    snapshotRef.current = data;
    let stop = false;
    const go = () =>
      refreshLive(snapshotRef.current!).then((r) => {
        if (stop || !r) return;
        setData(r.data);
        setLive(r.live);
      });
    go();
    const timer = window.setInterval(go, 60000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [data]);

  const range = useMemo(() => (data ? span(data) : { lo: 0, hi: 1 }), [data]);
  const gaps = useMemo(() => (data ? allGaps(data) : []), [data]);
  const stops = useMemo(() => (data ? buildTour(data) : []), [data]);

  // ---- The scene --------------------------------------------------------------------------------
  const ready = !!data?.coast && !!data?.geo;
  useEffect(() => {
    if (!ready || !canvasRef.current || engineRef.current) return;
    const d = data!;
    engineRef.current = new SceneEngine(canvasRef.current, d.coast!, d.turb, d.geo!, { t, level, waterModel, wavesModel, selected, gaps }, {
      onHover: (target, x, y) => setHover(target ? { target, x, y } : null),
      onSelect: (target) => {
        setSelected(target);
        if (target) setTab("details");
      },
    });
    if (import.meta.env.DEV) (window as unknown as { __scene: SceneEngine }).__scene = engineRef.current;
    return () => {
      engineRef.current?.destroy();
      engineRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  useEffect(() => {
    engineRef.current?.set({ t, level, waterModel, wavesModel, selected, gaps });
  }, [t, level, waterModel, wavesModel, selected, gaps]);

  useEffect(() => {
    engineRef.current?.focus(view);
  }, [view]);

  // The panel sits over the stage's right edge; keep the scene in the part it leaves uncovered.
  const colRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const fit = () => {
      const stage = stageRef.current;
      const side = colRef.current;
      if (!engineRef.current || !stage) return;
      const overlaps = !!side && getComputedStyle(side).position === "absolute";
      const r = overlaps ? stage.getBoundingClientRect().right - side!.getBoundingClientRect().left : 0;
      engineRef.current.setInset(r);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [tab, ready, tour === null]);

  // ---- Playback ---------------------------------------------------------------------------------
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const step = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      setT((cur) => {
        const next = Math.min(range.hi, cur + dt * PLAY_MIN_PER_S);
        if (next >= range.hi) setPlaying(false);
        return next;
      });
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, range.hi]);

  const seek = useCallback((m: number) => setT(Math.max(range.lo, Math.min(range.hi, m))), [range]);

  /** Jump to the previous or next moment anything was sealed. */
  const stepSeal = useCallback(
    (dir: 1 | -1) => {
      if (!data) return;
      const times = Array.from(new Set([...(data.coast?.events.seals ?? []), ...(data.turb?.events.seals ?? [])].map(([m]) => m))).sort((a, b) => a - b);
      const cur = Math.round(t);
      const next = dir === 1 ? times.find((m) => m > cur) : [...times].reverse().find((m) => m < cur);
      if (next !== undefined) seek(next);
    },
    [data, t, seek],
  );

  // ---- Tour -------------------------------------------------------------------------------------
  const goStop = useCallback(
    (i: number | null) => {
      setTour(i);
      if (i === null) return;
      const s = stops[i];
      if (!s) return;
      setPlaying(false);
      seek(s.t);
      setView(s.view);
      if (s.level !== undefined) setLevel(s.level);
      setSelected(s.select ?? null);
      setTab(s.tab);
    },
    [stops, seek],
  );

  useEffect(() => {
    if (tour === null || !auto) return;
    const timer = window.setTimeout(() => goStop(tour < stops.length - 1 ? tour + 1 : null), AUTO_MS);
    return () => window.clearTimeout(timer);
  }, [tour, auto, stops.length, goStop]);

  // Keyboard: arrows walk the tour when it is open and the seals otherwise; space plays.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      const el = ev.target as HTMLElement;
      if (el?.tagName === "INPUT" || el?.tagName === "SELECT" || el?.tagName === "TEXTAREA") return;
      if (tour !== null) {
        if (ev.key === "ArrowRight" || ev.key === "PageDown") {
          ev.preventDefault();
          goStop(tour < stops.length - 1 ? tour + 1 : null);
        } else if (ev.key === "ArrowLeft" || ev.key === "PageUp") {
          ev.preventDefault();
          if (tour > 0) goStop(tour - 1);
        } else if (ev.key === "Escape") goStop(null);
        return;
      }
      if (ev.key === " " && el?.tagName !== "BUTTON") {
        ev.preventDefault();
        setPlaying((p) => !p);
      } else if (ev.key === "ArrowRight") stepSeal(1);
      else if (ev.key === "ArrowLeft") stepSeal(-1);
      else if (ev.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tour, stops.length, goStop, stepSeal]);

  // ---- Render -----------------------------------------------------------------------------------
  if (error) {
    return (
      <div className="fatal">
        <h1 className="wordmark">Foresight Lab</h1>
        <p>{error}</p>
      </div>
    );
  }
  if (!data) return <div className="fatal">Loading the snapshot…</div>;

  const turb = data.turb;
  const cyc = turb ? cycleAt(turb, t) : null;
  const cycGraded = !!cyc && cyc.graded !== null && cyc.graded <= t;
  const waiting = turb ? waitingCycleAt(turb, t) : null;
  const gapNow = gapAt(gaps, t);
  const tracks: Track[] = [];
  if (data.coast) tracks.push({ key: "coast", label: "Coast", events: data.coast.events, gaps: data.coast.gaps, firstAfter: data.coast.first_cycle_after_gap, corrections: data.coast.corrections });
  if (turb) tracks.push({ key: "turb", label: "Turbulence", events: turb.events, gaps: turb.gaps, firstAfter: turb.first_cycle_after_gap, corrections: turb.corrections });
  const pmax = cyc && engineRef.current ? engineRef.current.levelMax(cyc) : null;
  const levelCounts = turb && cyc && cycGraded ? turb.grid.flight_levels.map((_, k) => cyc.reports.filter((r) => nearestIndex(turb.grid.flight_levels, r[2]) === k).length) : null;
  const verified = [data.coast?.ledger.verify, turb?.ledger.verify].filter(Boolean);
  const allOk = verified.length > 0 && verified.every((v) => v!.ok);
  const stop = tour !== null ? stops[tour] : null;
  const anyCorrections = (data.coast?.corrections.count ?? 0) + (turb?.corrections.count ?? 0);

  return (
    <div className={`app ${tour !== null ? "presenting" : ""} ${tab ? "panel-open" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <b>Foresight Lab</b>
          <span className="hide-sm">forecasts sealed before the future, graded by it</span>
        </div>
        <button className={`stamp ${live ? "live" : ""}`} onClick={() => setTab("data")} title="Where these numbers come from">
          <span className={live ? "live-dot" : "snap-dot"} />
          {live ? `Live counts and scores, server checked ${fmtIso(live.at)}` : `Snapshot taken ${fmtIso(data.meta.snapshot_at)}`}
        </button>
        <span className={`chain hide-sm ${allOk ? "ok" : "bad"}`} title="Recomputing every hash in both ledgers">
          {fmtInt((data.coast?.ledger.records ?? 0) + (turb?.ledger.records ?? 0))} sealed, {allOk ? "chains verified" : "a chain failed to verify"}
        </span>
        <div className="seg views" role="radiogroup" aria-label="Scene view">
          {(["all", "coast", "sky"] as const).map((v) => (
            <button key={v} className="seg-btn" role="radio" aria-checked={view === v} onClick={() => setView(v)}>
              {v === "all" ? "Both" : v === "coast" ? "Coast" : "Sky"}
            </button>
          ))}
        </div>
        <div className="seg panels" role="group" aria-label="Side panel">
          {PANELS.map(([k, label]) => (
            <button key={k} className="seg-btn" aria-pressed={tab === k} onClick={() => setTab(tab === k ? null : k)}>
              {label}
            </button>
          ))}
        </div>
        <button className="btn gold" onClick={() => goStop(0)} disabled={!stops.length}>
          Present
        </button>
      </header>

      <main className="stage" ref={stageRef}>
        {ready ? (
          <canvas ref={canvasRef} className="scene" aria-label="The coast's gauges and buoys, and the sky over the US, at the moment chosen on the timeline. Hover or click a gauge, a buoy, a sky cell or a pilot report for its sealed record." />
        ) : (
          <p className="empty pad">The coast snapshot or the map outlines are missing, so the scene cannot be drawn. {data.meta.missing.join(", ")}</p>
        )}
        <div className="legend">
          {turb && (
            <div className="ladder" role="radiogroup" aria-label="Flight level shown in the sky">
              <span className="ladder-note">{levelCounts ? "Flight level, reports" : "Flight level"}</span>
              {turb.grid.flight_levels.map((fl, k) => (
                <button key={fl} role="radio" aria-checked={level === k} className="rung" onClick={() => setLevel(k)} title={`${turb.grid.levels_hpa[k]} hPa`}>
                  {fl}
                  {levelCounts && <small>{levelCounts[k]}</small>}
                </button>
              ))}
            </div>
          )}
          {data.coast && (
            <label className="picker">
              <span>Coast forecaster</span>
              <select value={waterModel} onChange={(e) => setWaterModel(Number(e.target.value))}>
                {data.coast.water.models.map((m, k) => (
                  <option key={m} value={k}>
                    {modelName(m)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <span>
            <i className="sw" style={{ background: C.gold }} /> latest graded forecast
          </span>
          <span>
            <i className="sw" style={{ background: C.glass }} /> reading that graded it
          </span>
          <span>
            <i className="sw waiting" style={{ background: C.goldHi }} /> newer forecast, still waiting
          </span>
          {pmax !== null && (
            <span className="ramp" title="Sealed chance of moderate or worse turbulence on the shown level">
              <i className="sw" style={{ background: `linear-gradient(90deg, ${RAMP.join(",")})`, width: 54 }} /> 0 to {Math.round(pmax * 100)}% chance
            </span>
          )}
          {cycGraded && (
            <>
              <span>
                <i className="sw" style={{ background: C.red }} /> moderate or worse
              </span>
              <span>
                <i className="sw" style={{ background: C.green }} /> light or smooth
              </span>
            </>
          )}
          {turb && cyc && !cycGraded && <span className="dim">pilot reports appear once this grid is graded</span>}
          {waiting && (
            <span className="dim">
              a newer grid, sealed {fmtMin(waiting.sealed)} for {fmtMin(waiting.valid)}, waits for its reports
            </span>
          )}
        </div>
        {gapNow && (
          <div className="gapnote box" role="status">
            {fmtHours(gapNow.hours)} with nothing sealed ({gapNow.task === "coast" ? "coast" : "turbulence"}), {fmtMin(gapNow.from_min)} to {fmtMin(gapNow.to_min)}.{" "}
            {gapNow.origin === "recorded" ? "Recorded by the job beside its ledger." : "Found by scanning the ledger's seal times."}
          </div>
        )}
        {hover && !stop && (
          <div className="hovercard box" style={cardPos(hover, stageRef.current)}>
            <Details data={data} t={t} target={hover.target} waterModel={waterModel} wavesModel={wavesModel} compact />
          </div>
        )}
      </main>

      {(tab || stop) && (
        <div className="rightcol" ref={colRef}>
          {stop && tour !== null && (
            <div className="tour box" role="dialog" aria-label="Presentation">
              <div className="tour-top">
                <span className="count">
                  {tour + 1} of {stops.length}
                </span>
                <label className="auto">
                  <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> Auto-advance
                </label>
              </div>
              <h2>{stop.title}</h2>
              <p>{stop.text}</p>
              {auto && <div key={tour} className="autobar" style={{ animationDuration: `${AUTO_MS}ms` }} />}
              <div className="row">
                <button className="btn quiet" onClick={() => goStop(null)}>
                  End
                </button>
                {tour > 0 && (
                  <button className="btn" onClick={() => goStop(tour - 1)}>
                    Back
                  </button>
                )}
                <button className="btn gold" onClick={() => goStop(tour < stops.length - 1 ? tour + 1 : null)} autoFocus>
                  {tour < stops.length - 1 ? "Next" : "Finish"}
                </button>
              </div>
            </div>
          )}
          {tab && (
            <aside className="side box" aria-label={PANELS.find(([k]) => k === tab)?.[1]}>
              <div className="side-head">
                <b>{PANELS.find(([k]) => k === tab)?.[1]}</b>
                <button className="btn quiet" onClick={() => setTab(null)} aria-label="Close the panel">
                  Close
                </button>
              </div>
              <div className="side-body">
                {tab === "details" &&
                  (selected ? (
                    <Details data={data} t={t} target={selected} waterModel={waterModel} wavesModel={wavesModel} />
                  ) : (
                    <p className="empty">Click a gauge, a buoy, a sky cell or a pilot report to see its sealed forecast, the hash it was sealed under, when it was graded and the error.</p>
                  ))}
                {tab === "scoreboard" && <Scoreboard data={data} />}
                {tab === "research" && (data.research ? <Research research={data.research} /> : <p className="empty">No research runs or experiment reports were found under runs/ when the snapshot was taken.</p>)}
                {tab === "data" && <DataPanel data={data} live={live} corrections={anyCorrections} />}
              </div>
            </aside>
          )}
        </div>
      )}

      <Timeline tracks={tracks} t={t} lo={range.lo} hi={range.hi} playing={playing} onPlay={() => setPlaying((p) => (t >= range.hi - 1 ? (seek(range.lo), true) : !p))} onSeek={seek} onStep={stepSeal} />
    </div>
  );
}

function cardPos(h: { x: number; y: number }, stage: HTMLDivElement | null): React.CSSProperties {
  const w = stage?.clientWidth ?? 1000;
  const hh = stage?.clientHeight ?? 800;
  const left = h.x + 340 > w ? Math.max(8, h.x - 336) : h.x + 16;
  const top = h.y + 260 > hh ? Math.max(8, h.y - 250) : h.y + 16;
  return { left, top };
}
