// The ledger as a timeline: one track per job, seals above the line in gold and grades below it in
// blue, the stretches with nothing sealed drawn as gaps with their length, and corrected grades
// marked. Dragging the head moves the scene to that moment.

import { useMemo, useRef } from "react";
import type { Corrections, Events, FirstCycle, Gap } from "../data/types";
import { fmtClock, fmtDay, fmtHours, fmtInt, fmtMin } from "../data/derive";

export type Track = {
  key: string;
  label: string;
  events: Events;
  gaps: Gap[];
  firstAfter: FirstCycle[];
  corrections: Corrections;
};

type Props = {
  tracks: Track[];
  t: number;
  lo: number;
  hi: number;
  playing: boolean;
  onPlay: () => void;
  onSeek: (t: number) => void;
  onStep: (dir: 1 | -1) => void;
};

export function Timeline({ tracks, t, lo, hi, playing, onPlay, onSeek, onStep }: Props) {
  const rail = useRef<HTMLDivElement>(null);
  const spanMin = Math.max(1, hi - lo);
  const at = (m: number) => `${Math.max(0, Math.min(1, (m - lo) / spanMin)) * 100}%`;
  const width = (a: number, b: number) => `${(Math.max(0, Math.min(hi, b) - Math.max(lo, a)) / spanMin) * 100}%`;
  const seekAt = (clientX: number) => {
    const r = rail.current!.getBoundingClientRect();
    onSeek(Math.round(lo + Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * spanMin));
  };
  const days = useMemo(() => {
    const out: number[] = [];
    for (let m = Math.ceil(lo / 1440) * 1440; m <= hi; m += 1440) out.push(m);
    return out;
  }, [lo, hi]);
  const counts = tracks.map((tr) => ({
    seals: tr.events.seals.filter(([m]) => m <= t).reduce((s, [, n]) => s + n, 0),
    grades: tr.events.grades.filter(([m]) => m <= t).reduce((s, [, n]) => s + n, 0),
  }));

  return (
    <div className="ledger box" role="group" aria-label="The ledger over time">
      <div className="ledger-controls">
        <button className="btn icon gold" onClick={onPlay} aria-label={playing ? "Pause" : "Play the ledger"} title={playing ? "Pause (space)" : "Play (space)"}>
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <div className="clock">
          <b>{fmtClock(t)}</b>
          <span>{fmtDay(t)}, UTC</span>
        </div>
        <button className="btn icon" onClick={() => onStep(-1)} aria-label="Previous seal" title="Previous seal (←)">
          <PrevIcon />
        </button>
        <button className="btn icon" onClick={() => onStep(1)} aria-label="Next seal" title="Next seal (→)">
          <NextIcon />
        </button>
      </div>
      <div className="ledger-body">
        <div
          className="scrubber"
          role="slider"
          tabIndex={0}
          aria-label="Moment on the ledger"
          aria-valuemin={lo}
          aria-valuemax={hi}
          aria-valuenow={t}
          aria-valuetext={fmtMin(t)}
          onPointerDown={(e) => {
            (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
            seekAt(e.clientX);
          }}
          onPointerMove={(e) => {
            if (e.buttons) seekAt(e.clientX);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight") {
              e.preventDefault();
              e.stopPropagation();
              onStep(1);
            }
            if (e.key === "ArrowLeft") {
              e.preventDefault();
              e.stopPropagation();
              onStep(-1);
            }
          }}
        >
          {tracks.map((tr, k) => (
            <TrackRow key={tr.key} tr={tr} at={at} width={width} lo={lo} hi={hi} count={counts[k]} />
          ))}
          <div className="rail" ref={rail} aria-hidden="true">
            {days.map((m) => (
              <span key={m} className="day" style={{ left: at(m) }}>
                {fmtDay(m)}
              </span>
            ))}
            <div className="head" style={{ left: at(t) }} />
          </div>
        </div>
      </div>
    </div>
  );
}

function TrackRow({ tr, at, width, lo, hi, count }: { tr: Track; at: (m: number) => string; width: (a: number, b: number) => string; lo: number; hi: number; count: { seals: number; grades: number } }) {
  const maxSeal = Math.max(1, ...tr.events.seals.map(([, n]) => n));
  const maxGrade = Math.max(1, ...tr.events.grades.map(([, n]) => n));
  const h = (n: number, max: number) => `${3 + Math.sqrt(n / max) * 15}px`;
  const longest = tr.gaps.reduce((a, g) => Math.max(a, g.hours), 0);
  const byMin = new Map<number, number>();
  for (const c of tr.corrections.items) if (c.time_min !== null) byMin.set(c.time_min, (byMin.get(c.time_min) ?? 0) + 1);
  const fixes = [...byMin.entries()].filter(([m]) => m >= lo && m <= hi);
  return (
    <div className="track">
      <div className="track-label">
        <b>{tr.label}</b>
        <span>
          {fmtInt(count.seals)} sealed, {fmtInt(count.grades)} graded
        </span>
      </div>
      <div className="lane">
        <div className="mid" />
        {tr.gaps.map((g) => (
          <div
            key={g.from}
            className={`gap ${g.hours === longest ? "big" : ""}`}
            style={{ left: at(g.from_min), width: width(g.from_min, g.to_min) }}
            title={`Nothing sealed for ${fmtHours(g.hours)}, ${fmtMin(g.from_min)} to ${fmtMin(g.to_min)}. ${g.origin === "recorded" ? "Recorded by the job in gaps.jsonl." : "Found by scanning the ledger's seal times."}`}
          >
            {g.hours === longest && <span>{fmtHours(g.hours)} with nothing sealed</span>}
          </div>
        ))}
        {tr.events.seals
          .filter(([m]) => m >= lo && m <= hi)
          .map(([m, n]) => (
            <i key={`s${m}`} className="tick seal" style={{ left: at(m), height: h(n, maxSeal) }} title={`${fmtMin(m)}: ${n} sealed`} />
          ))}
        {tr.events.grades
          .filter(([m]) => m >= lo && m <= hi)
          .map(([m, n]) => (
            <i key={`g${m}`} className="tick grade" style={{ left: at(m), height: h(n, maxGrade) }} title={`${fmtMin(m)}: ${n} graded`} />
          ))}
        {tr.firstAfter
          .filter((f) => tr.gaps.some((g) => g.from === f.gap_from && g.hours === longest))
          .map((f) => (
            <span key={f.graded_at} className="after" style={{ left: at(f.graded_at_min) }} title={`First grading after the gap: ${f.grades} grades at once`}>
              {fmtInt(f.grades)} graded at once{f.with_no_reports ? `, ${fmtInt(f.with_no_reports)} with no reports` : ""}
              {f.corrected ? `; ${fmtInt(f.corrected)} corrected later` : ""}
            </span>
          ))}
        {fixes.map(([m, n]) => (
          <span key={`c${m}`} className="fix" style={{ left: at(m) }} title={`${fmtMin(m)}: ${n} corrected grades appended beside the ledger`}>
            {fmtInt(n)} corrected
          </span>
        ))}
      </div>
    </div>
  );
}

const PlayIcon = () => (
  <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
    <path d="M1 0h1v7H1zM2 1h1v5H2zM3 2h1v3H3zM4 3h1v1H4z" fill="currentColor" />
  </svg>
);
const PauseIcon = () => (
  <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
    <path d="M1 0h2v7H1zM4 0h2v7H4z" fill="currentColor" />
  </svg>
);
const NextIcon = () => (
  <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
    <path d="M0 0h1v7H0zM1 1h1v5H1zM2 2h1v3H2zM3 3h1v1H3zM5 0h2v7H5z" fill="currentColor" />
  </svg>
);
const PrevIcon = () => (
  <svg width="14" height="14" viewBox="0 0 7 7" shapeRendering="crispEdges" aria-hidden="true">
    <path d="M6 0h1v7H6zM5 1h1v5H5zM4 2h1v3H4zM3 3h1v1H3zM0 0h2v7H0z" fill="currentColor" />
  </svg>
);
