// What one gauge, buoy, sky cell or pilot report shows at the timeline's moment: the sealed
// forecast, the hash it was sealed under, when it was graded, and the error.

import type { Coast, Data, HistRow, Site, Turb, TurbCycle } from "../data/types";
import { brierOf, CODE_LABEL, cycleAt, fmtMin, fmtNum, isLab, modelName, nearestIndex, pct, peersOf, reportClass, riskAt, siteAt } from "../data/derive";
import { shortName } from "../scene/layout";
import type { Target } from "../scene/engine";

type Props = { data: Data; t: number; target: Target; waterModel: number; wavesModel: number; compact?: boolean };

export function Details({ data, t, target, waterModel, wavesModel, compact }: Props) {
  if ((target.type === "gauge" || target.type === "buoy") && data.coast) {
    return <SiteDetails coast={data.coast} t={t} kind={target.type} id={target.id} seq={target.seq} model={target.type === "gauge" ? waterModel : wavesModel} compact={compact} />;
  }
  if (data.turb) {
    const c = cycleAt(data.turb, t);
    if (!c) return <p className="empty">No turbulence forecast had been sealed at {fmtMin(t)}.</p>;
    if (target.type === "cell") return <CellDetails turb={data.turb} c={c} t={t} level={target.level} i={target.i} j={target.j} compact={compact} />;
    if (target.type === "report") return <ReportDetails turb={data.turb} c={c} index={target.index} />;
  }
  return null;
}

function Hash({ hash, seq }: { hash: string; seq: number }) {
  return (
    <span className="hash" title="The first characters of the SHA-256 hash this record was sealed under">
      {hash.slice(0, 12)}… <small>record {seq}</small>
    </span>
  );
}

function SiteDetails({ coast, t, kind, id, seq, model, compact }: { coast: Coast; t: number; kind: "gauge" | "buoy"; id: string; seq?: number; model: number; compact?: boolean }) {
  const sites: Site[] = kind === "gauge" ? coast.water.stations : coast.waves.buoys;
  const models = kind === "gauge" ? coast.water.models : coast.waves.models;
  const site = sites.find((s) => s.id === id);
  if (!site) return null;
  const v = siteAt(site, model, t);
  const what = kind === "gauge" ? "water above the tide table" : "wave height";
  // The record in focus: one the presentation pinned, else the latest one graded by now, else the
  // latest one sealed.
  const pinned = seq !== undefined ? site.hist.find((r) => r[8] === seq && r[1] <= t) ?? null : null;
  const row = pinned ?? v.last ?? v.row;
  const waiting = v.row && !v.graded && v.row !== row ? v.row : null;
  return (
    <div className="details">
      <h2>{shortName(site.name)}</h2>
      <p className="sub">
        {kind === "gauge" ? "NOAA tide gauge" : "NDBC buoy"} {site.id}, {fmtNum(site.lat, 2)}° N {fmtNum(Math.abs(site.lon), 2)}° W
      </p>
      {!row ? (
        <p className="now-line">
          <small>{modelName(models[model])}</small>
          Nothing sealed for this {kind} by {fmtMin(t)}.
        </p>
      ) : (
        <>
          <RecordView row={row} t={t} what={what} model={models[row[3]]} />
          {waiting && (
            <p className="note">
              Newer forecast, sealed {fmtMin(waiting[1])} and waiting: {fmtNum(waiting[4])} cm at {fmtMin(waiting[2])}, record {waiting[8]}.
            </p>
          )}
          {!compact && <Peers peers={peersOf(site, row, t)} models={models} t={t} featured={row[3]} />}
        </>
      )}
    </div>
  );
}

function RecordView({ row, t, what, model }: { row: HistRow; t: number; what: string; model: string }) {
  const graded = row[7] !== null && row[7] <= t;
  return (
    <>
      <p className="now-line">
        <small>
          {modelName(model)}, {row[10]} h ahead
        </small>
        Forecast {what}: <b className="fc">{fmtNum(row[4])} cm</b> at {fmtMin(row[2])}
      </p>
      <dl className="kv">
        <dt>Sealed</dt>
        <dd>{fmtMin(row[1])}</dd>
        <dt>Hash</dt>
        <dd>
          <Hash hash={row[9]} seq={row[8]} />
        </dd>
        <dt>Graded</dt>
        <dd>{graded ? fmtMin(row[7]) : row[2] > t ? `not yet: due ${fmtMin(row[2])}` : "waiting for the reading"}</dd>
        {graded && (
          <>
            <dt>Observed</dt>
            <dd>
              <b className="obs">{fmtNum(row[5])} cm</b>
            </dd>
            <dt>Error</dt>
            <dd>
              <b>{fmtNum(row[6])} cm</b>
            </dd>
          </>
        )}
      </dl>
    </>
  );
}

function Peers({ peers, models, t, featured }: { peers: HistRow[]; models: string[]; t: number; featured: number }) {
  const rows = [...peers].sort((a, b) => (a[7] !== null && a[7] <= t ? (a[6] ?? Infinity) : Infinity) - (b[7] !== null && b[7] <= t ? (b[6] ?? Infinity) : Infinity));
  return (
    <section className="section">
      <h3>
        Every model, same issue time <span>{rows.length} sealed</span>
      </h3>
      <table className="mini">
        <thead>
          <tr>
            <th>Model</th>
            <th>Forecast</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const graded = r[7] !== null && r[7] <= t;
            return (
              <tr key={r[8]} className={r[3] === featured ? "on" : isLab(models[r[3]]) ? "lab" : ""}>
                <td>{modelName(models[r[3]])}</td>
                <td>{fmtNum(r[4])}</td>
                <td>{graded ? fmtNum(r[6]) : "due"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="note">Centimetres. Errors appear once the reading that grades them has come in.</p>
    </section>
  );
}

function CellDetails({ turb, c, t, level, i, j, compact }: { turb: Turb; c: TurbCycle; t: number; level: number; i: number; j: number; compact?: boolean }) {
  const { lats, lons, flight_levels, levels_hpa, resolution_deg } = turb.grid;
  const p = riskAt(turb, c, level, i, j);
  const graded = c.graded !== null && c.graded <= t;
  const half = resolution_deg / 2;
  const inCell = (r: TurbCycle["reports"][number]) => Math.abs(r[0] - lats[i]) <= half && Math.abs(r[1] - lons[j]) <= half && nearestIndex(flight_levels, r[2]) === level;
  const here = graded ? c.reports.filter(inCell) : [];
  const all = graded ? brierOf(turb, c) : null;
  const cell = graded ? brierOf(turb, c, inCell) : null;
  return (
    <div className="details">
      <h2>
        FL{flight_levels[level]} over {fmtNum(lats[i], 1)}° N {fmtNum(Math.abs(lons[j]), 1)}° W
      </h2>
      <p className="sub">
        {levels_hpa[level]} hPa, a {resolution_deg}° cell
      </p>
      <p className="now-line">
        <small>{modelName(turb.featured)}</small>
        Chance of moderate or worse turbulence: <b className="fc">{p === null ? "no value" : pct(p, 1)}</b>
      </p>
      <dl className="kv">
        <dt>Valid</dt>
        <dd>{fmtMin(c.valid)}</dd>
        <dt>Sealed</dt>
        <dd>
          {fmtMin(c.sealed)}
          {c.lead_min !== null ? `, ${Math.round(c.lead_min)} min ahead` : ""}
        </dd>
        <dt>Hash</dt>
        <dd>
          <Hash hash={c.hash} seq={c.seq} />
        </dd>
        <dt>Graded</dt>
        <dd>{graded ? `${fmtMin(c.graded)}, ${c.n_reports ?? c.reports.length} pilot reports in the window` : c.graded ? `not yet at this moment (${fmtMin(c.graded)})` : "not yet"}</dd>
        {graded && c.corrected && (
          <>
            <dt>Corrected</dt>
            <dd className="fixed">
              {fmtMin(c.corrected.at)}: first graded against {c.corrected.before_n ?? "an unknown number of"} reports, now {c.n_reports ?? c.reports.length}. {c.corrected.reason}. The reports shown are the corrected ones; the first grade stays in the ledger.
            </dd>
          </>
        )}
        {graded && (
          <>
            <dt>In this cell</dt>
            <dd>
              {here.length === 0
                ? "no reports at this level"
                : here.map((r) => CODE_LABEL[r[3]] ?? r[3]).join(", ")}
            </dd>
            <dt>Error</dt>
            <dd>
              {cell?.brier !== null && cell?.brier !== undefined ? `Brier ${fmtNum(cell.brier, 3)} on ${cell.n} classed reports here; ` : ""}
              {all?.brier !== null && all?.brier !== undefined ? `Brier ${fmtNum(all.brier, 3)} on all ${all.n} classed reports of this record` : "no classed reports"}
            </dd>
          </>
        )}
      </dl>
      {!compact && graded && (
        <p className="note">
          Brier score: the mean of (sealed chance − outcome)², outcome 1 for moderate or worse and 0 for light or smooth, read here at each report's nearest grid cell and level. The live job's own scoreboard ranks reports by sealed risk instead.
        </p>
      )}
      {c.coverage === null && !c.corrected && graded && !compact && <p className="note">This grade predates the coverage check, so it does not record which report sources covered its window.</p>}
    </div>
  );
}

function ReportDetails({ turb, c, index }: { turb: Turb; c: TurbCycle; index: number }) {
  const r = c.reports[index];
  if (!r) return null;
  const { lats, lons, flight_levels } = turb.grid;
  const lv = nearestIndex(flight_levels, r[2]);
  const p = riskAt(turb, c, lv, nearestIndex(lats, r[0]), nearestIndex(lons, r[1]));
  const cls = reportClass(r[3]);
  return (
    <div className="details">
      <h2>Pilot report, FL{r[2]}</h2>
      <p className="sub">
        {fmtMin(r[4])}, {fmtNum(r[0], 2)}° N {fmtNum(Math.abs(r[1]), 2)}° W
      </p>
      <p className="now-line">
        <small>What the pilot felt</small>
        <b className={cls === "pos" ? "bad" : cls === "neg" ? "good" : ""}>{CODE_LABEL[r[3]] ?? `code ${r[3]}`}</b>
        {cls === "mid" ? ": dropped from scoring as ambiguous" : cls === "pos" ? ": counts as moderate or worse" : ": counts as a negative"}
      </p>
      <dl className="kv">
        <dt>Sealed chance</dt>
        <dd>
          {p === null ? "no value" : pct(p, 1)} at FL{flight_levels[lv]}, nearest cell
        </dd>
        <dt>Record</dt>
        <dd>
          <Hash hash={c.hash} seq={c.seq} />
        </dd>
        <dt>Sealed</dt>
        <dd>{fmtMin(c.sealed)}</dd>
        <dt>Graded</dt>
        <dd>{fmtMin(c.graded)}</dd>
      </dl>
    </div>
  );
}
