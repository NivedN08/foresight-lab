// Where every number on the page comes from: the snapshot, the files it read, the chain checks,
// every stretch with nothing sealed, and whether any grade has been corrected.

import type { Live } from "../data/load";
import type { Data } from "../data/types";
import { allGaps, fmtHours, fmtInt, fmtIso, fmtMin } from "../data/derive";

export function DataPanel({ data, live, corrections }: { data: Data; live: Live | null; corrections: number }) {
  const m = data.meta;
  const gaps = allGaps(data);
  const kb = (b: number) => `${fmtInt(Math.round(b / 1024))} KB`;
  return (
    <div className="data-panel">
      <section className="section">
        <h3>This page is a snapshot</h3>
        <p className="lede">
          Taken {fmtIso(m.snapshot_at)} from <code>{m.root}</code>. The scene, the timeline and the research views show the ledgers as they stood then. Run <code>python3 web/build_data.py</code> to take a new one.
        </p>
        <p className="note">
          {live
            ? `The dashboard server answered at ${fmtIso(live.at)}, so the record counts and scoreboards above are live${live.coast && live.turb ? "" : live.coast ? " for the coast only" : " for turbulence only"}. The history drawn in the scene still comes from the snapshot.`
            : "The dashboard server did not answer, so every number comes from the snapshot."}
        </p>
      </section>

      <section className="section">
        <h3>The hash chains</h3>
        <dl className="kv">
          {data.coast && (
            <>
              <dt>Coast</dt>
              <dd>
                {fmtInt(data.coast.ledger.records)} sealed, {fmtInt(data.coast.ledger.graded)} graded. Chain check: “{data.coast.ledger.verify.message}”
              </dd>
            </>
          )}
          {data.turb && (
            <>
              <dt>Turbulence</dt>
              <dd>
                {fmtInt(data.turb.ledger.records)} sealed, {fmtInt(data.turb.ledger.graded)} graded. Chain check: “{data.turb.ledger.verify.message}”
              </dd>
            </>
          )}
        </dl>
      </section>

      <section className="section">
        <h3>
          Stretches with nothing sealed <span>{gaps.length}</span>
        </h3>
        {gaps.length === 0 ? (
          <p className="empty">None in this snapshot.</p>
        ) : (
          <ul className="gaplist">
            {gaps.map((g) => (
              <li key={`${g.task}${g.from}`}>
                <b>{fmtHours(g.hours)}</b> {g.task === "coast" ? "coast" : "turbulence"}, {fmtMin(g.from_min)} to {fmtMin(g.to_min)}
                <small>{g.origin === "recorded" ? `Recorded in the job's gaps file${g.cause ? `: ${g.cause}` : ""}.` : "Found by scanning the ledger's seal times; no gaps file was in the snapshot."}</small>
              </li>
            ))}
          </ul>
        )}
        <p className="note">{corrections ? `${fmtInt(corrections)} corrected grades are recorded beside the ledgers; the originals stay untouched.` : "No corrected grades were found beside the ledgers."}</p>
      </section>

      <section className="section">
        <h3>
          Files read <span>{m.sources.length}</span>
        </h3>
        <ul className="sources">
          {m.sources.map((s) => (
            <li key={s.path}>
              <code>{s.path}</code>
              <small>
                {kb(s.bytes)}, changed {fmtIso(s.modified)}
              </small>
            </li>
          ))}
        </ul>
        {m.missing.length > 0 && (
          <>
            <h3 className="sub-h">Not found when the snapshot was taken</h3>
            <ul className="sources missing">
              {m.missing.map((p) => (
                <li key={p}>
                  <code>{p}</code>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  );
}
