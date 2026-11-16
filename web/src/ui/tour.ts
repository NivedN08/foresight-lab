// The presentation: six stops through the story, every number in a caption read from the snapshot.

import type { Data, FirstCycle, HistRow, Site } from "../data/types";
import { fmtHours, fmtInt, fmtIso, fmtMin, fmtNum, fmtSigned, cycleAt, featuredWater, longestGap, modelName, nearestIndex, span } from "../data/derive";
import type { Target, View } from "../scene/engine";
import { shortName } from "../scene/layout";
import { defaultRun } from "./Research";

export type Tab = "details" | "scoreboard" | "research" | "data";

export type Stop = { title: string; text: string; t: number; view: View; tab: Tab | null; select?: Target; level?: number };

export function buildTour(d: Data): Stop[] {
  const stops: Stop[] = [];
  const { hi } = span(d);
  const coast = d.coast;
  const turb = d.turb;
  const sealed = (coast?.ledger.records ?? 0) + (turb?.ledger.records ?? 0);
  const graded = (coast?.ledger.graded ?? 0) + (turb?.ledger.graded ?? 0);
  const first = [coast?.first_seal, turb?.first_seal].filter(Boolean).sort()[0] ?? null;

  stops.push({
    title: "Forecasts sealed before the future happens",
    text:
      `An AI research lab cut itself off at a date, learned only from before it, and now seals forecasts before the future happens. ` +
      `Since ${fmtIso(first)} it has sealed ${fmtInt(sealed)} forecasts and ${fmtInt(graded)} have been graded by what really happened. ` +
      (coast ? `On the left, ${coast.water.stations.length} tide gauges and ${coast.waves.buoys.length} wave buoys on the US West Coast. ` : "") +
      (turb ? `On the right, the sky over the continental US at ${turb.grid.flight_levels.length} flight levels.` : ""),
    t: hi,
    view: "all",
    tab: null,
  });

  // Sealing and grading, on the gauge with the largest error the featured forecaster has had graded.
  if (coast) {
    const model = featuredWater(coast);
    const mi = coast.water.models.indexOf(model);
    let pick: { site: Site; row: HistRow } | null = null;
    for (const site of coast.water.stations)
      for (const row of site.hist)
        if (row[3] === mi && row[7] !== null && row[6] !== null && (!pick || row[6] > (pick.row[6] ?? 0))) pick = { site, row };
    if (pick) {
      const { site, row } = pick;
      const name = shortName(site.name);
      stops.push({
        title: "Sealing: the forecast is locked first",
        text:
          `At ${fmtMin(row[1])} the coast job sealed ${modelName(model).toLowerCase()}'s forecast for ${name}: water ${fmtNum(row[4])} cm above the tide table at ${fmtMin(row[2])}. ` +
          `The record is hashed together with the hash of the record before it (this one begins ${row[9].slice(0, 8)}), so editing or deleting any sealed forecast breaks the chain. ` +
          `All ${fmtInt(coast.ledger.verify.checked)} coast records check out: “${coast.ledger.verify.message}”.`,
        t: row[1],
        view: "coast",
        tab: "details",
        select: { type: "gauge", id: site.id, seq: row[8] },
      });
      const sky = turb ? skyAt(d, row[7]!) : null;
      stops.push({
        title: "Grading: the real world answers",
        text:
          `At ${fmtMin(row[7])} the ${name} gauge reported ${fmtNum(row[5])} cm. The sealed forecast was off by ${fmtNum(row[6])} cm, the largest miss ${modelName(model).toLowerCase()} has had graded. ` +
          (sky
            ? sky.graded
              ? `In the sky, the grid sealed at ${fmtMin(sky.c.sealed)} for ${fmtMin(sky.c.valid)} was graded with ${fmtInt(sky.c.reports.length)} pilot reports; red marks felt moderate or worse, green light or smooth.`
              : `In the sky, the grid sealed at ${fmtMin(sky.c.sealed)} for ${fmtMin(sky.c.valid)} is still waiting for its pilot reports.`
            : ""),
        t: row[7]!,
        view: "all",
        tab: "details",
        select: { type: "gauge", id: site.id, seq: row[8] },
        level: sky?.level,
      });
    }
  }

  // The scoreboard, said honestly.
  {
    const bits: string[] = [];
    if (coast) {
      let best: { m: string; lead: string; diff: number; low?: number; high?: number; n: number; lab: number; base: number; baseModel: string } | null = null;
      for (const [m, byLead] of Object.entries(coast.water.scoreboard_paired ?? {}))
        for (const [lead, p] of Object.entries(byLead))
          if (p.vs_best && (!best || p.n > best.n || (p.n === best.n && p.vs_best.diff_cm < best.diff)))
            best = { m, lead, diff: p.vs_best.diff_cm, low: p.vs_best.low, high: p.vs_best.high, n: p.n, lab: p.vs_best.lab_mae_cm, base: p.vs_best.best_mae_cm, baseModel: p.vs_best.model };
      if (best) {
        const sep = best.low !== undefined && best.high !== undefined && (best.high < 0 || best.low > 0);
        bits.push(
          `On ${fmtInt(best.n)} identical ${best.lead}-hour coast forecasts, ${best.m.slice(4)} averages ${fmtNum(best.lab, 2)} cm against ${fmtNum(best.base, 2)} for the ${modelName(best.baseModel).toLowerCase()} ` +
            `(difference ${fmtSigned(best.diff)}, interval ${fmtSigned(best.low)} to ${fmtSigned(best.high)}): ${sep ? (best.diff < 0 ? "the lab is lower." : "the lab is higher.") : "not separated yet."}`,
        );
      }
    }
    if (turb) {
      const k = Object.keys(turb.scoreboard?.[turb.featured] ?? {}).sort()[0];
      const rows = Object.entries(turb.scoreboard ?? {})
        .filter(([, s]) => s[k])
        .sort((a, b) => b[1][k].auc - a[1][k].auc);
      const f = turb.scoreboard?.[turb.featured]?.[k];
      if (f && rows.length) {
        const [top, ts] = rows[0];
        bits.push(
          top === turb.featured
            ? `For turbulence, the ${modelName(turb.featured).toLowerCase()} ranks ${fmtInt(f.n)} graded report layers best, AUC ${fmtNum(f.auc, 3)}.`
            : `For turbulence it is not better: ${modelName(top).toLowerCase()} alone scores AUC ${fmtNum(ts[k].auc, 3)} against ${fmtNum(f.auc, 3)} for the ${modelName(turb.featured).toLowerCase()}, on ${fmtInt(f.n)} report layers.`,
        );
      }
    }
    stops.push({ title: "The scoreboard, on live graded forecasts", text: bits.join(" "), t: hi, view: "all", tab: "scoreboard" });
  }

  // The gap.
  const gap = longestGap(d);
  if (gap) {
    const fc = turb?.first_cycle_after_gap.find((f) => turb.gaps.some((g) => g.from === f.gap_from && g.hours >= gap.hours - 1));
    const other = [...(coast?.gaps ?? []), ...(turb?.gaps ?? [])].filter((g) => g !== gap && Math.abs(g.from_min - gap.from_min) < 60 && g.hours > gap.hours / 2);
    stops.push({
      title: "The gap, and how it is recorded",
      text:
        `Nothing was sealed from ${fmtMin(gap.from_min)} to ${fmtMin(gap.to_min)}: ${fmtHours(gap.hours)}${other.length ? ` (${other.map((g) => `${fmtHours(g.hours)} for ${g.task === "coast" ? "the coast" : "turbulence"}`).join(", ")})` : ""}. ` +
        (gap.origin === "recorded"
          ? `The job wrote the gap to a file beside the ledger: ${fmtInt(gap.due_while_away)} forecasts came due meanwhile and were graded late. `
          : `This snapshot found it by scanning the ledger's seal times; no gaps file was in the snapshot. `) +
        `Nothing was back-dated. ` +
        (fc ? `When grading resumed, ${fmtInt(fc.grades)} turbulence grades were written at once${fc.with_no_reports ? `, ${fmtInt(fc.with_no_reports)} against no pilot reports` : ""}. ` : "") +
        correctionText(d, fc ?? null),
      t: Math.round((gap.from_min + gap.to_min) / 2),
      view: "all",
      tab: null,
    });
  }

  // What the lab learned.
  const r = d.research;
  if (r) {
    const bits: string[] = [];
    const run = defaultRun(r);
    if (run) {
      const passed = run.proposals.filter((p) => p.accepted).length;
      bits.push(`In the run behind the live forecasters, the loop proposed ${fmtInt(run.proposals.length)} changes; ${fmtInt(passed)} passed the code-settled rule and ${fmtInt(run.adopted.length)} became the forecaster.`);
    }
    const look = r.final_test.find((l) => Object.keys(l.scores).some((k) => k.includes("live_")));
    const start = r.final_test.flatMap((l) => Object.entries(l.scores)).find(([k]) => k.endsWith("starting_formula.py"));
    const liveRow = look ? Object.entries(look.scores).find(([k]) => k.includes("live_")) : null;
    if (liveRow && start) {
      const name = liveRow[0].split("/").pop()!.replace(/^live_/, "").replace(/\.py$/, "");
      bits.push(`On the one-look test window, live forecaster ${name} scored ${fmtNum(liveRow[1].mean, 2)} cm against ${fmtNum(start[1].mean, 2)} for the hand-written starting formula.`);
    }
    const tested = r.experiments.filter((e) => e.current && e.paired.test);
    if (tested.length) {
      const ps = tested.map((e) => Number((e.paired.test ?? "").match(/p = ([\d.]+)/)?.[1] ?? NaN)).filter((p) => !Number.isNaN(p));
      const sig = ps.filter((p) => p < 0.05).length;
      const tooFew = tested.filter((e) => /too few/.test(e.paired.test ?? "")).length;
      bits.push(
        sig === 0
          ? `Which way of running the loop is best is not settled: in ${tested.length} experiment matrices, ${tooFew} had too few seeds to test${ps.length ? ` and the tested comparison gave p = ${Math.min(...ps)}` : ""}.`
          : `${sig} of the experiments' paired comparisons reached significance: ${tested.map((e) => e.paired.test).join("; ")}.`,
      );
    }
    stops.push({ title: "What the lab learned", text: bits.join(" "), t: hi, view: "all", tab: "research" });
  }
  return stops;
}

/** What happened to the grades written blind after the gap, said from the corrections file. */
export function correctionText(d: Data, fc: FirstCycle | null): string {
  const tc = d.turb?.corrections;
  const total = (d.coast?.corrections.count ?? 0) + (tc?.count ?? 0);
  if (!total) return "Corrections go in a separate file beside the ledger; none are in this snapshot yet.";
  const reason = tc?.reasons?.[0]?.[0];
  if (fc?.corrected)
    return (
      `${fc.corrected === fc.grades ? `All ${fmtInt(fc.corrected)}` : fmtInt(fc.corrected)} were later corrected${reason ? ` (“${reason}”)` : ""}; ${fmtInt(fc.no_reports_after_correction ?? 0)} are still without reports. ` +
      `Corrections are appended to their own file; the first grades stay in the ledger.`
    );
  return `${fmtInt(total)} corrected grades are appended to their own file; the first grades stay in the ledger.`;
}

/** The turbulence cycle showing at t, whether it was graded by then, and the level most of its
 * pilot reports sit on. */
function skyAt(d: Data, t: number) {
  const turb = d.turb!;
  const c = cycleAt(turb, t);
  if (!c) return null;
  const counts = turb.grid.flight_levels.map(() => 0);
  for (const rep of c.reports) counts[nearestIndex(turb.grid.flight_levels, rep[2])]++;
  const level = counts.indexOf(Math.max(...counts));
  return { c, level, graded: c.graded !== null && c.graded <= t };
}
