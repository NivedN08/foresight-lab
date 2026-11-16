// The research loop from its own run records: what was proposed, what passed the code-settled
// rule and was kept, what was rejected; then the four experiments' results and the one-look test.

import { useMemo, useState } from "react";
import type { Experiment, LabRun, Look, Research as R } from "../data/types";
import { fmtInt, fmtIso, fmtNum, fmtSigned, modelName } from "../data/derive";

export function defaultRun(r: R): LabRun | null {
  const live = r.promoted.find((p) => r.runs.some((x) => x.run_id === p.run_id));
  return r.runs.find((x) => x.run_id === live?.run_id) ?? r.runs[0] ?? null;
}

export function Research({ research }: { research: R }) {
  const [runId, setRunId] = useState(defaultRun(research)?.run_id ?? "");
  const run = research.runs.find((x) => x.run_id === runId) ?? null;
  const current = research.experiments.filter((e) => e.current).sort((a, b) => a.claim.localeCompare(b.claim, "en", { numeric: true }));
  return (
    <div className="research-panel">
      <section className="section">
        <h3>
          The research loop <span>{research.runs.length} runs on disk</span>
        </h3>
        <p className="lede">One change to the forecasting formula at a time, tested by code on later data, kept only if it wins there and does not lose on still later data.</p>
        <div className="seg wrap" role="radiogroup" aria-label="Run">
          {research.runs.map((x) => {
            const live = research.promoted.filter((p) => p.run_id === x.run_id).map((p) => p.name);
            return (
              <button key={x.run_id} className="seg-btn" role="radio" aria-checked={x.run_id === runId} onClick={() => setRunId(x.run_id)} title={x.run_id}>
                {x.problem.replace("_", " ")}, {fmtIso(x.started_at)}
                {live.length ? ` (${live.join(", ")})` : ""}
              </button>
            );
          })}
        </div>
        {run && <RunView run={run} promotedFrom={research.promoted.filter((p) => p.run_id === run.run_id)} />}
      </section>
      <section className="section">
        <h3>
          The experiments <span>{current.length} matrices</span>
        </h3>
        <p className="lede">Each arm is one way of running the loop, repeated over seeds with the same budget. Bars span the seeds' final confirm loss; the tick is the mean; the dashed line is the starting formula.</p>
        {current.map((e) => (
          <ExperimentView key={e.matrix} e={e} />
        ))}
      </section>
      {research.final_test.length > 0 && <TestView looks={research.final_test} />}
    </div>
  );
}

function RunView({ run, promotedFrom }: { run: LabRun; promotedFrom: R["promoted"] }) {
  const [filter, setFilter] = useState<"all" | "kept" | "rejected">("all");
  const passed = run.proposals.filter((p) => p.accepted).length;
  const cost = run.curve.length ? run.curve[run.curve.length - 1].cost_usd : null;
  const adoptedGens = new Set(run.adopted.map((a) => a.generation));
  const shown = run.proposals.filter((p) => (filter === "all" ? true : filter === "kept" ? p.accepted : !p.accepted));
  return (
    <div className="run">
      <div className="score">
        <div>
          <b>{fmtInt(run.proposals.length)}</b>
          <span>proposed</span>
        </div>
        <div>
          <b className="good">{fmtInt(passed)}</b>
          <span>passed the rule</span>
        </div>
        <div>
          <b className="gold">{fmtInt(run.adopted.length)}</b>
          <span>became the forecaster</span>
        </div>
      </div>
      <Curve run={run} />
      <p className="note">
        {run.generations_done} generations of {run.proposals_per_generation} proposals, status “{run.status.replaceAll("_", " ")}”
        {cost !== null ? `, $${fmtNum(cost, 2)} in model calls` : ""}. {run.counts.lint_failed} replies had no usable code.{" "}
        {promotedFrom.length ? `Live forecasters from this run: ${promotedFrom.map((p) => `${p.name} (generation ${p.generation})`).join(", ")}.` : ""}
      </p>
      <div className="seg" role="radiogroup" aria-label="Which proposals">
        {(["all", "kept", "rejected"] as const).map((f) => (
          <button key={f} className="seg-btn" role="radio" aria-checked={filter === f} onClick={() => setFilter(f)}>
            {f === "all" ? "All" : f === "kept" ? "Passed" : "Rejected"}
          </button>
        ))}
      </div>
      <ol className="proposals">
        {shown.map((p, k) => (
          <li key={k} className={p.accepted ? "kept" : "rejected"}>
            <span className="gen">{p.generation}</span>
            <span className="idea">
              {p.idea}
              <small>
                {p.origin === "search" ? `from search${p.source_title && p.source_title !== "N/A" ? `: ${p.source_title}` : ""}` : p.origin === "own" ? "the model's own idea" : p.origin ? `from ${p.origin.replaceAll("_", " ")}` : ""}
                {adoptedGens.has(p.generation) && p.accepted ? ", in a generation whose winner was adopted" : ""}
              </small>
            </span>
            <span className="delta" title="Change in confirm loss against the forecaster it challenged; negative is better">
              {fmtSigned(p.confirm_delta, 3)}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Confirm loss of the forecaster in use after each generation, as a pixel step line. */
function Curve({ run }: { run: LabRun }) {
  const pts = run.curve.filter((c) => c.confirm !== null && c.confirm !== undefined);
  if (pts.length < 2) return null;
  const W = 300;
  const H = 90;
  const lo = Math.min(...pts.map((p) => p.confirm));
  const hi = Math.max(...pts.map((p) => p.confirm));
  const gx = (g: number) => 8 + (g / Math.max(1, pts[pts.length - 1].generation)) * (W - 16);
  const gy = (v: number) => 10 + ((hi - v) / Math.max(1e-9, hi - lo)) * (H - 24);
  let d = `M${gx(pts[0].generation)} ${gy(pts[0].confirm)}`;
  for (let k = 1; k < pts.length; k++) d += `H${gx(pts[k].generation)}V${gy(pts[k].confirm)}`;
  const adopted = new Set(run.adopted.map((a) => a.generation));
  return (
    <figure className="curve">
      <svg viewBox={`0 0 ${W} ${H}`} shapeRendering="crispEdges" role="img" aria-label={`Confirm loss from ${fmtNum(pts[0].confirm, 3)} to ${fmtNum(pts[pts.length - 1].confirm, 3)}`}>
        <path d={d} fill="none" stroke="var(--gold)" strokeWidth="2" />
        {pts
          .filter((p) => adopted.has(p.generation))
          .map((p) => (
            <rect key={p.generation} x={gx(p.generation) - 3} y={gy(p.confirm) - 3} width="6" height="6" fill="var(--gold)" stroke="var(--ink)" strokeWidth="1" />
          ))}
        <text x="8" y={H - 2} className="axis">
          generation 0
        </text>
        <text x={W - 8} y={H - 2} textAnchor="end" className="axis">
          {pts[pts.length - 1].generation}
        </text>
      </svg>
      <figcaption>
        Confirm loss {fmtNum(pts[0].confirm, 3)} at the start, {fmtNum(pts[pts.length - 1].confirm, 3)} at the end. Squares mark generations that adopted a new forecaster.
      </figcaption>
    </figure>
  );
}

function title(matrix: string) {
  const m = matrix.match(/^claim(\d+)_(.*)$/);
  if (!m) return matrix.replaceAll("_", " ");
  return `Claim ${m[1]}: ${m[2].replaceAll("_", " ")}`;
}

function ExperimentView({ e }: { e: Experiment }) {
  const arms = e.arms.filter((a) => a.confirm);
  const vals = arms.flatMap((a) => [a.confirm!.min, a.confirm!.max]).concat(e.seed_confirm ?? []);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const x = (v: number) => `${((v - lo) / Math.max(1e-9, hi - lo)) * 100}%`;
  const best = useMemo(() => [...arms].sort((a, b) => a.confirm!.mean - b.confirm!.mean)[0], [arms]);
  const overlaps = best ? arms.filter((a) => a !== best && a.confirm!.min <= best.confirm!.max) : [];
  return (
    <div className="exp">
      <div className="exp-head">
        <b>{title(e.matrix)}</b>
        <span>
          {e.problem.replace("_", " ")}, {e.runs_finished} of {e.planned_runs} runs finished
        </span>
      </div>
      <div className="arms">
        {arms.map((a) => (
          <div key={a.arm} className={`arm ${a === best ? "best" : ""}`}>
            <span className="arm-name">
              {a.arm.replaceAll("_", " ")}
              <small>
                {a.finished} seed{a.finished === 1 ? "" : "s"}
                {a.cost_usd !== null ? `, $${fmtNum(a.cost_usd, 2)} a run` : ""}
              </small>
            </span>
            <span className="arm-bar">
              {e.seed_confirm !== null && <i className="seedline" style={{ left: x(e.seed_confirm) }} />}
              <i className="range" style={{ left: x(a.confirm!.min), width: `calc(${x(a.confirm!.max)} - ${x(a.confirm!.min)} + 3px)` }} />
              <i className="mean" style={{ left: x(a.confirm!.mean) }} />
            </span>
            <span className="arm-val">{fmtNum(a.confirm!.mean, 3)}</span>
          </div>
        ))}
      </div>
      <p className="note">
        {best && `Lowest mean: ${best.arm.replaceAll("_", " ")}. `}
        {best && overlaps.length > 0 && `Its seeds overlap ${overlaps.length} other arm${overlaps.length === 1 ? "" : "s"}, so the ranking is not settled. `}
        {e.paired.a && e.paired.b && e.paired.test && `${e.paired.a.replaceAll("_", " ")} against ${e.paired.b.replaceAll("_", " ")}: ${e.paired.test}.`}
      </p>
    </div>
  );
}

function candidateName(k: string) {
  if (k.startsWith("baseline:")) {
    const b = k.slice(9);
    const map: Record<string, string> = { tide_table: "TideTable", persistence: "Persistence", damped_persistence: "DampedPersistence" };
    return modelName(map[b] ?? b);
  }
  const f = k.split("/").pop()!.replace(/\.py$/, "");
  if (f.startsWith("live_")) return `Live forecaster ${f.slice(5)}`;
  if (f === "starting_formula") return modelName("SeedPhysics");
  return f.replaceAll("_", " ");
}

function TestView({ looks }: { looks: Look[] }) {
  const merged = new Map<string, Look["scores"][string]>();
  for (const l of looks) for (const [k, v] of Object.entries(l.scores)) if (!merged.has(k)) merged.set(k, v);
  const rows = [...merged.entries()].sort((a, b) => a[1].mean - b[1].mean);
  const w = looks.find((l) => l.test_window)?.test_window;
  const lo = Math.min(...rows.map(([, v]) => v.low));
  const hi = Math.max(...rows.map(([, v]) => v.high));
  // The tide table is far off the others; give it its own row without stretching the axis.
  const axisHi = Math.min(hi, (rows[rows.length - 2]?.[1].high ?? hi) * 1.15);
  const x = (v: number) => `${(Math.min(axisHi, v) - lo) / Math.max(1e-9, axisHi - lo) * 100}%`;
  return (
    <section className="section">
      <h3>
        The one-look test <span>{looks.length} logged looks</span>
      </h3>
      {w && (
        <p className="lede">
          Data no run had seen: {fmtInt(w.cases)} cases, {fmtInt(w.blocks)} station-days, {fmtIso(w.from, false)} to {fmtIso(w.to)}. Mean absolute error in cm with 95% intervals over station-days.
        </p>
      )}
      <div className="arms">
        {rows.map(([k, v]) => (
          <div key={k} className={`arm ${k.includes("live_") ? "best" : ""}`}>
            <span className="arm-name">
              {candidateName(k)}
              <small>{fmtInt(v.n)} cases</small>
            </span>
            <span className="arm-bar">
              <i className="range" style={{ left: x(v.low), width: `calc(${x(v.high)} - ${x(v.low)} + 3px)` }} />
              <i className="mean" style={{ left: x(v.mean) }} />
              {v.mean > axisHi && <i className="off">off scale</i>}
            </span>
            <span className="arm-val">{fmtNum(v.mean, 2)}</span>
          </div>
        ))}
      </div>
      {looks.length > 1 && <p className="note">The scorer's first look left the starting formula out, so it was scored in a second logged look; nothing was chosen after seeing it.</p>}
    </section>
  );
}
