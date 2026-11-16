// Shapes of the snapshot files written by build_data.py. Times in arrays are minutes since the
// Unix epoch; ISO strings are UTC.

export type Verify = { ok: boolean; checked: number; message: string };

export type Gap = {
  task: string;
  from: string;
  to: string;
  from_min: number;
  to_min: number;
  hours: number;
  cause: string | null;
  due_while_away: number | null;
  graded_late: number | null;
  unrecoverable: number | null;
  recorded_at: string | null;
  /** "recorded": from the job's gaps.jsonl or state; "derived": found by scanning seal times. */
  origin: "recorded" | "derived";
};

export type FirstCycle = {
  gap_from: string;
  graded_at: string;
  graded_at_min: number;
  grades: number;
  with_no_reports?: number;
  /** how many of this batch were later corrected, and how many still have no reports after that */
  corrected?: number;
  no_reports_after_correction?: number;
};

export type Corrections = {
  source: string | null;
  count: number;
  /** [reason, count] */
  reasons?: [string, number][];
  /** [graded_at of the replaced grades, count] */
  replaced_graded_at?: [string | null, number][];
  items: { seq: number | null; time: string | null; time_min: number | null; before_n?: number | null; after_n?: number | null }[];
};

export type Events = { seals: [number, number][]; grades: [number, number][] };

/** [issued, sealed, target, model index, forecast cm, truth cm, error cm, graded, seq, hash, lead h] */
export type HistRow = [number, number, number, number, number | null, number | null, number | null, number | null, number, string, number];

export type Site = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  latest_obs_time: string | null;
  current_residual_m?: number | null;
  latest_wave_height_m?: number | null;
  latest_dominant_period_s?: number | null;
  hist: HistRow[];
};

export type LeadScore = { graded: number; mae_cm: number };
export type Scoreboard = Record<string, Record<string, LeadScore>>;

export type PairedLead = {
  n: number;
  blocks: number;
  mae_cm: Record<string, number>;
  vs_best: { model: string; lab_mae_cm: number; best_mae_cm: number; diff_cm: number; too_few_blocks: boolean; low?: number; high?: number } | null;
};

export type Promoted = {
  name: string;
  model: string;
  run_id: string;
  generation: number;
  select_loss: number;
  confirm_loss: number;
  promoted_at: string;
  source_sha256: string;
  status: { last_run: string | null; ok: boolean | null; error: string | null; sealed: number | null };
};

export type BacktestTable = Record<string, Record<string, { mae_cm: number; low: number; high: number; n: number }>>;

export type Coast = {
  generated_at: string;
  ledger: { path: string; records: number; graded: number; head_hash: string; verify: Verify };
  cycle: Record<string, number | string | null>;
  leads: number[];
  water: {
    quantity: string;
    leads_h: number[];
    models: string[];
    listed_models: string[];
    promoted: Promoted[];
    scoreboard: Scoreboard;
    scoreboard_paired: Record<string, Record<string, PairedLead>>;
    stations: Site[];
    stale: number;
    backtest: { days: number; unit: string; table: BacktestTable; generated_at: string };
  };
  waves: {
    quantity: string;
    leads_h: number[];
    models: string[];
    listed_models: string[];
    scoreboard: Scoreboard;
    buoys: Site[];
    backtest: { days: number; unit: string; table: BacktestTable; generated_at: string };
  };
  alerts: { event: string; area: string; severity: string; onset: string; ends: string; sender: string }[];
  gaps: Gap[];
  first_cycle_after_gap: FirstCycle[];
  corrections: Corrections;
  events: Events;
  tasks: Record<string, number>;
  first_seal: string;
  last_seal: string;
};

export type TurbScore = { n: number; positives: number; auc: number; hit_rate: number; false_alarm_rate: number };

export type TurbCycle = {
  seq: number;
  hash: string;
  issued: number;
  sealed: number;
  valid: number;
  offset: number | null;
  lead_min: number | null;
  /** risk x 1000 as [level][lat][lon] flattened; -1 where the field was missing */
  risk: number[];
  graded: number | null;
  n_reports: number | null;
  coverage: string | null;
  report_sources: string[] | null;
  /** [lat, lon, flight level, code, minute] */
  reports: [number, number, number, number, number][];
  /** set when this grade was corrected; the reports above are then the corrected ones */
  corrected?: { at: number | null; before_n: number | null; before_coverage: string | null; reason: string | null } | null;
};

export type Turb = {
  generated_at: string;
  ledger: { records: number; graded: number; head: string; verify: Verify };
  grid: { lats: number[]; lons: number[]; levels_hpa: number[]; flight_levels: number[]; resolution_deg: number };
  issued_at: string;
  risk_units: Record<string, string>;
  models: string[];
  featured: string;
  scoreboard: Record<string, Record<string, TurbScore>>;
  notes: string[];
  backtest: {
    generated_at: string;
    days: number;
    labelled_cases: number;
    positives: number;
    base_rate: number;
    verdict: string;
    table: { model: string; auc: number; low: number; high: number; n: number; positives: number }[];
    brier: Record<string, number | string>;
    test: { n: number; positives: number; from: string; to: string };
  };
  reports_12h: unknown[];
  cycles: TurbCycle[];
  gaps: Gap[];
  first_cycle_after_gap: FirstCycle[];
  corrections: Corrections;
  events: Events;
  first_seal: string;
  last_seal: string;
};

export type Proposal = {
  generation: number;
  idea: string;
  origin: string;
  accepted: boolean;
  select_delta: number | null;
  confirm_delta: number | null;
  source_title: string | null;
  time: string;
};

export type LabRun = {
  problem: string;
  run_id: string;
  status: string;
  started_at: string;
  updated_at: string;
  generations_done: number;
  proposals_per_generation: number;
  incumbent: { select: number; confirm: number } | null;
  curve: { generation: number; select: number; confirm: number; accepted: number; proposals: number; cost_usd: number }[];
  adopted: { generation: number; hash: string; time: string; confirm: number | null }[];
  proposals: Proposal[];
  counts: { evaluations: number; decisions: number; lint_failed: number; new_incumbent: number; cost_cap: number };
};

export type Stat = { mean: number; min: number; max: number; n: number };

export type Experiment = {
  matrix: string;
  claim: string;
  current: boolean;
  problem: string;
  modified: string;
  planned_runs: number;
  runs_finished: number;
  partial: boolean;
  skipped: unknown[];
  target: number | null;
  seed_confirm: number | null;
  arms: { arm: string; planned: number; finished: number; seeds: number[]; confirm: Stat | null; proposals: number; accepted: number; became_incumbent: number | null; cost_usd: number | null }[];
  paired: { a: string | null; b: string | null; seeds: number | null; a_wins: number | null; b_wins: number | null; mean_diff: number | null; test: string | null };
  board: { arm: string; offered: number; used: number; used_passed: number; used_adopted: number }[];
  search: { arm: string; backend: string; searches: number; failed: number }[];
};

export type Look = {
  time: string;
  problem: string;
  test_window: { cases: number; blocks: number; from: string; to: string } | null;
  best_baseline: string | null;
  scores: Record<string, { mean: number; low: number; high: number; n: number; by_lead: Record<string, number> }>;
  paired: Record<string, { vs: string; diff: number; low: number; high: number; blocks: number; n: number }> | null;
  candidates: { path: string; sha256: string }[];
};

export type Research = {
  runs: LabRun[];
  promoted: { name: string; model: string; run_id: string; run_dir: string; generation: number; select_loss: number; confirm_loss: number; promoted_at: string; problem: string }[];
  experiments: Experiment[];
  final_test: Look[];
};

export type Geo = {
  source: string;
  sky_box: [number, number, number, number];
  coast_box: [number, number, number, number];
  sky_land: [number, number][][];
  sky_borders: [number, number][][];
  coast_land: [number, number][][];
  coast_borders: [number, number][][];
};

export type Meta = {
  snapshot_at: string;
  root: string;
  files: Record<string, number>;
  sources: { path: string; bytes: number; modified: string }[];
  missing: string[];
  coast_leads: number[];
};

export type Data = { meta: Meta; coast: Coast | null; turb: Turb | null; research: Research | null; geo: Geo | null };
