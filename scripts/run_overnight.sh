#!/bin/bash
# Runs the four experiment matrices one after another and writes a report after each.
# Safe to re-run: finished runs are skipped and interrupted runs resume.
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
for m in claim1_coast claim2_transfer_turb claim4_memory_coast claim3_search_coast; do
  echo "=== $(date -u +%FT%TZ) start $m"
  $PY -u -m lab.experiments.matrix run lab/experiments/configs/$m.json --parallel 2
  $PY -m lab.experiments.report $m > runs/exp/$m/report_stdout.txt 2>&1
  echo "=== $(date -u +%FT%TZ) done $m"
done
