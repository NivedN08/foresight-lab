// The lab's forecasters against the baselines, on live sealed forecasts that have been graded,
// with the counts behind every number and a plain note wherever the lab is not better.

import { useState } from "react";
import type { Data, PairedLead, Turb } from "../data/types";
import { fmtInt, fmtIso, fmtNum, fmtSigned, isLab, isoMin, modelName, pct } from "../data/derive";
import { correctionText } from "./tour";

type Verdict = "lower" | "higher" | "level";

function verdictOf(v: PairedLead["vs_best"]): Verdict {
  if (!v || v.too_few_blocks || v.low === undefined || v.high === undefined) return "level";
  if (v.high < 0) return "lower";
  if (v.low > 0) return "higher";
  return "level";
}

const VERDICT_TEXT: Record<Verdict, string> = { lower: "lab lower", higher: "lab higher", level: "not separated" };

export function Scoreboard({ data }: { data: Data }) {
  return (
    <div className="board-panel">
      {data.coast && <CoastPaired data={data} />}
      {data.turb && <TurbBoard turb={data.turb} data={data} />}
      {data.coast && <WavesBoard data={data} />}
    </div>
  );
}

function CoastPaired({ data }: { data: Data }) {
  const coast = data.coast!;
  const paired = coast.water.scoreboard_paired ?? {};
  const labs = Object.keys(paired);
  const all = labs.flatMap((m) => Object.entries(paired[m]).map(([lead, p]) => ({ m, lead, p, v: verdictOf(p.vs_best) })));
  const n = { lower: all.filter((x) => x.v === "lower").length, higher: all.filter((x) => x.v === "higher").length, level: all.filter((x) => x.v === "level").length };
  // One axis for every interval, so the bars can be compared.
  const ext = Math.max(0.1, ...all.flatMap((x) => [Math.abs(x.p.vs_best?.low ?? 0), Math.abs(x.p.vs_best?.high ?? 0), Math.abs(x.p.vs_best?.diff_cm ?? 0)]));
  const pos = (v: number) => `${50 + (v / ext) * 50}%`;
  const notBetter = all.filter((x) => x.v !== "lower");
  return (
    <section className="section">
      <h3>
        Coast water level, live <span>{fmtInt(coast.ledger.graded)} graded of {fmtInt(coast.ledger.records)} sealed</span>
      </h3>
      <p className="lede">
        Each lab forecaster against the best hand-written model on the very same graded forecasts. Below zero, the lab's error is lower. Bars are 95% intervals that resample whole station-days.
      </p>
      {labs.length === 0 ? (
        <p className="empty">No lab forecaster has graded forecasts shared with every hand-written model yet.</p>
      ) : (
        <>
          <p className={`verdict ${n.lower > n.level + n.higher ? "good" : ""}`}>
            Of {all.length} comparisons, the lab is clearly lower in {n.lower}, clearly higher in {n.higher}, and not separated in {n.level}.
          </p>
          {labs.map((m) => (
            <div key={m} className="paired">
              <div className="paired-head">
                <b>{modelName(m)}</b>
              </div>
              {Object.entries(paired[m]).map(([lead, p]) => {
                const v = p.vs_best;
                const verdict = verdictOf(v);
                return (
                  <div key={lead} className={`prow ${verdict}`}>
                    <span className="lead">{lead} h</span>
                    <span className="nums">
                      {fmtNum(v?.lab_mae_cm, 2)} vs {fmtNum(v?.best_mae_cm, 2)} cm
                      <small>
                        {v ? modelName(v.model).toLowerCase() : "no baseline"}, {fmtInt(p.n)} forecasts, {fmtInt(p.blocks)} station-days
                      </small>
                    </span>
                    <span className="ci" aria-label={`difference ${fmtSigned(v?.diff_cm)} cm`}>
                      <i className="zero" />
                      {v && v.low !== undefined && v.high !== undefined && <i className="bar" style={{ left: pos(v.low), width: `${((v.high - v.low) / ext) * 50}%` }} />}
                      {v && <i className="pt" style={{ left: pos(v.diff_cm) }} />}
                    </span>
                    <span className="vd">
                      {fmtSigned(v?.diff_cm)}
                      <small>{v?.too_few_blocks ? "too few days" : VERDICT_TEXT[verdict]}</small>
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
          {notBetter.length > 0 && (
            <p className="honest">
              Where the lab is not better: {notBetter.map((x) => `${x.m.slice(4)} at ${x.lead} h${x.v === "higher" ? " (higher error)" : ""}`).join(", ")}. The one-look test window, under Research, is the cleaner comparison.
            </p>
          )}
        </>
      )}
    </section>
  );
}

function TurbBoard({ turb, data }: { turb: Turb; data: Data }) {
  const keys = Object.keys(turb.scoreboard?.[turb.featured] ?? {}).sort();
  const [k, setK] = useState(keys[0] ?? "");
  const rows = Object.entries(turb.scoreboard ?? {})
    .map(([m, s]) => ({ m, s: s[k] }))
    .filter((x) => x.s)
    .sort((a, b) => b.s.auc - a.s.auc);
  const feat = rows.find((x) => x.m === turb.featured);
  const better = feat ? rows.filter((x) => x.s.auc > feat.s.auc) : [];
  const hasLab = turb.models.some(isLab);
  const longest = turb.first_cycle_after_gap.reduce<(typeof turb.first_cycle_after_gap)[number] | null>((a, f) => (!a || f.grades > a.grades ? f : a), null);
  const lastFix = Math.max(0, ...turb.corrections.items.map((x) => x.time_min ?? 0));
  const maxAuc = Math.max(...rows.map((x) => x.s.auc), 0.5);
  return (
    <section className="section">
      <h3>
        Turbulence, live <span>{fmtInt(turb.ledger.graded)} graded of {fmtInt(turb.ledger.records)} sealed</span>
      </h3>
      <p className="lede">
        How well each model's sealed grid ranks the pilot reports that came true: area under the curve, where 0.5 is chance. {(turb.notes ?? []).find((x) => x.startsWith("A hit means")) ?? ""}
      </p>
      {keys.length > 1 && (
        <div className="seg" role="radiogroup" aria-label="Sealed at least this long before">
          {keys.map((x) => (
            <button key={x} className="seg-btn" role="radio" aria-checked={x === k} onClick={() => setK(x)}>
              {x.replace("at_least_", "≥ ")}
            </button>
          ))}
        </div>
      )}
      <table className="mini wide">
        <thead>
          <tr>
            <th>Model</th>
            <th>AUC</th>
            <th>Hit</th>
            <th>False alarm</th>
            <th>Layers</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ m, s }) => (
            <tr key={m} className={m === turb.featured ? "on" : ""}>
              <td>
                {modelName(m)}
                <i className="aucbar" style={{ width: `${Math.max(0, Math.min(1, (s.auc - 0.4) / (maxAuc - 0.4))) * 100}%` }} />
              </td>
              <td>{fmtNum(s.auc, 3)}</td>
              <td>{pct(s.hit_rate)}</td>
              <td>{pct(s.false_alarm_rate)}</td>
              <td>
                {fmtInt(s.n)}
                <small> ({fmtInt(s.positives)} mod+)</small>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {feat && better.length > 0 && (
        <p className="honest">
          Not better: {better.map((x) => `${modelName(x.m).toLowerCase()} (${fmtNum(x.s.auc, 3)})`).join(", ")} {better.length === 1 ? "ranks" : "rank"} the reports better than the {modelName(turb.featured).toLowerCase()} ({fmtNum(feat.s.auc, 3)}) on these {fmtInt(feat.s.n)} report layers.
        </p>
      )}
      {!hasLab && <p className="note">No lab-written turbulence forecaster is live. The research loop's turbulence runs were experiments; the live job seals the hand-written formula and the indices it is compared with.</p>}
      {longest && (
        <p className="note">
          The first cycle after the long gap wrote {fmtInt(longest.grades)} grades at once{longest.with_no_reports ? `, ${fmtInt(longest.with_no_reports)} of them against no pilot reports` : ""}. {correctionText(data, longest)} These scores are as the live job last computed them, at {fmtIso(turb.generated_at)}
          {turb.corrections.count && lastFix && lastFix > (isoMin(turb.generated_at) ?? 0) ? ", before the corrections were written, so they still use the first grades." : "."}
        </p>
      )}
      {data.turb?.backtest?.verdict && <p className="note">Backtest on hindsight fields, not a forecast test: {data.turb.backtest.verdict}</p>}
    </section>
  );
}

function WavesBoard({ data }: { data: Data }) {
  const waves = data.coast!.waves;
  const sb = waves.scoreboard ?? {};
  const models = Object.keys(sb);
  const leads = Array.from(new Set(models.flatMap((m) => Object.keys(sb[m])))).sort((a, b) => Number(a) - Number(b));
  const hasLab = models.some(isLab);
  return (
    <section className="section">
      <h3>
        Coast wave height, live <span>mean error, cm</span>
      </h3>
      <table className="mini wide">
        <thead>
          <tr>
            <th>Model</th>
            {leads.map((l) => (
              <th key={l}>{l} h</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {models.map((m) => (
            <tr key={m} className={isLab(m) ? "on" : ""}>
              <td>{modelName(m)}</td>
              {leads.map((l) => (
                <td key={l}>
                  {fmtNum(sb[m][l]?.mae_cm, 1)}
                  <small> {fmtInt(sb[m][l]?.graded)}</small>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note">Small numbers are graded forecasts. {hasLab ? "" : "No lab forecaster runs on waves; every model here is hand-written."}</p>
    </section>
  );
}
