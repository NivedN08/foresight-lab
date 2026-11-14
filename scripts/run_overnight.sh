#!/bin/bash
# Runs the four experiment matrices one after another and writes a report after each.
# Order: the claim 1 ladder first (claim 2's warm arms carry from its researcher_memory arm),
# then memory, search, and transfer. Safe to re-run: finished runs are skipped and
# interrupted runs resume. See docs/experiments.md.
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
for m in claim1_ladder_coast claim4_memory_coast claim3_search_coast claim2_transfer_turb_v2; do
  echo "=== $(date -u +%FT%TZ) start $m"
  $PY -u -m lab.experiments.matrix run lab/experiments/configs/$m.json --parallel 2
  mkdir -p runs/exp/$m
  $PY -m lab.experiments.report $m > runs/exp/$m/report_stdout.txt 2>&1
  echo "=== $(date -u +%FT%TZ) done $m"
done
