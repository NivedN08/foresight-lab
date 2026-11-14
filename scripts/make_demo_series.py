"""Write a SYNTHETIC hourly GPU rental price table for plumbing tests of lab.tasks.series.

    .venv/bin/python scripts/make_demo_series.py

Writes cache/series/demo_prices_synthetic.csv and cache/series/demo_config_synthetic.json.
Every number in the file is made up by this script. It exists only so the series
adapter can be exercised before the real data arrives; no number computed from it
may be presented as a result.

Structure: 8 GPU types x 5 regions = 40 series, hourly, 120 days. Each price is
    base(type) x region factor x slow trend x (1 + daily cycle + weekly cycle)
    x shared shocks (per type and per region) x occasional step changes x noise,
with a few missing hours and a few multi-hour outages per series.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "cache" / "series"
CSV_NAME = "demo_prices_synthetic.csv"
CONFIG_NAME = "demo_config_synthetic.json"
TYPES = {"H100": 2.9, "A100-80G": 1.7, "A100-40G": 1.3, "L40S": 1.0, "A6000": 0.75, "L4": 0.45,
         "RTX4090": 0.4, "V100": 0.35}
REGIONS = {"us-east": 1.00, "us-west": 1.04, "eu-west": 1.10, "eu-north": 0.96, "asia-east": 1.15}
HEADER = ("# SYNTHETIC DEMO DATA, made up by scripts/make_demo_series.py for plumbing tests only. "
          "Not real prices; do not report any number computed from this file.\n")


def shocks(rng, n, rate_per_day, size, decay_h):
    """Random multiplicative jumps that decay back: exp(sum of decaying kicks)."""
    kicks = np.zeros(n)
    hits = rng.random(n) < rate_per_day / 24.0
    kicks[hits] = rng.normal(0, size, hits.sum())
    out = np.zeros(n)
    a = np.exp(-1.0 / decay_h)
    acc = 0.0
    for k in range(n):
        acc = acc * a + kicks[k]
        out[k] = acc
    return out


def make(days: int = 120, seed: int = 7, start: str = "2026-07-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    n = len(t)
    hour = t.hour.to_numpy()
    dow = t.day_of_week.to_numpy()
    days_f = np.arange(n) / 24.0
    type_shock = {g: shocks(rng, n, 0.15, 0.10, 36.0) for g in TYPES}
    region_shock = {r: shocks(rng, n, 0.10, 0.06, 24.0) for r in REGIONS}
    rows = []
    for g, base in TYPES.items():
        trend_rate = rng.normal(-0.0015, 0.0015)            # per day, e.g. newer GPUs getting cheaper
        for r, rf in REGIONS.items():
            daily_amp = rng.uniform(0.0, 0.12)
            weekly_amp = rng.uniform(0.0, 0.08)
            phase = rng.uniform(0, 2 * np.pi)
            daily = daily_amp * np.sin(2 * np.pi * (hour - 14) / 24.0 + 0.3 * phase)
            weekly = weekly_amp * (np.where(dow >= 5, -1.0, 0.4))
            step = np.zeros(n)
            for _ in range(rng.poisson(1.2)):                  # occasional permanent price changes
                at = rng.integers(n // 10, n)
                step[at:] += rng.normal(0, 0.08)
            noise = rng.normal(0, 0.012, n)
            log_p = (np.log(base * rf) + trend_rate * days_f + np.log1p(daily + weekly)
                     + type_shock[g] + region_shock[r] + step + noise)
            price = np.round(np.exp(log_p), 4)
            units = np.round(np.clip(50 * rf - 60 * (np.exp(log_p) / (base * rf) - 1) + rng.normal(0, 3, n), 0, None))
            keep = rng.random(n) > 0.01                         # about 1% single missing hours
            for _ in range(rng.integers(0, 3)):                 # 0-2 outages of 3-30 hours
                a = rng.integers(0, n - 30)
                keep[a:a + rng.integers(3, 31)] = False
            rows.append(pd.DataFrame({"timestamp": t[keep].strftime("%Y-%m-%dT%H:%M:%SZ"), "gpu_type": g,
                                      "region": r, "price_usd_hr": price[keep],
                                      "available_units": units[keep],
                                      "is_weekend": (dow[keep] >= 5).astype(int)}))
    return pd.concat(rows, ignore_index=True).sort_values(["timestamp", "gpu_type", "region"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = make(a.days, a.seed)
    csv = out / CSV_NAME
    with open(csv, "w") as fh:
        fh.write(HEADER)
        df.to_csv(fh, index=False)
    cfg = {"path": CSV_NAME, "time_col": "timestamp", "value_col": "price_usd_hr",
           "id_cols": ["gpu_type", "region"], "leads": [1, 3, 7], "lead_unit": "steps", "history": 56,
           "covariate_cols": ["available_units"], "known_future_cols": ["is_weekend"], "loss": "mae",
           "name": "series_demo_synthetic",
           "title": "SYNTHETIC demo: forecast made-up hourly GPU rental prices 1, 3 and 7 hours ahead."}
    (out / CONFIG_NAME).write_text(json.dumps(cfg, indent=1) + "\n")
    print(f"wrote {csv} ({len(df)} rows, {df.groupby(['gpu_type', 'region']).ngroups} series; SYNTHETIC)")
    print(f"wrote {out / CONFIG_NAME}")


if __name__ == "__main__":
    main()
