"""Read-only experiment endpoints: /api/exp, /api/exp/{matrix}, /api/exp/{matrix}/{arm}/{seed},
and experiment runs listed by /api/lab/runs."""
import json

import pytest
from fastapi.testclient import TestClient

import lab.server as server


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj))


def _run(d, status, confirm, run_id):
    _write(d / "summary.json", {"run_id": run_id, "problem": "toy", "mode": "researcher", "status": status,
                                "started_at": "2026-11-14T01:00:00Z", "updated_at": "2026-11-14T01:05:00Z",
                                "generations_done": 2, "generations_planned": 2,
                                "incumbent": {"select": confirm - 0.1, "confirm": confirm}, "cost_usd": 0.5,
                                "loss_name": "absolute error, cm",
                                "baseline_incumbent": {"select": 2.0, "confirm": 2.5}})
    _write(d / "config.json", {"run_id": run_id, "problem": "toy", "mode": "researcher"})
    _write(d / "findings.jsonl", '{"id": "f1", "accepted": true}\n{"id": "f2", "acc')   # partial last line
    _write(d / "incumbent.py", "# Change: x\nprint(1)\n")


@pytest.fixture
def exp_root(tmp_path, monkeypatch):
    lab, exp = tmp_path / "lab", tmp_path / "exp"
    lab.mkdir()
    m = exp / "m1"
    _write(m / "manifest.json", {
        "name": "m1", "updated_at": "2026-11-14T01:06:00Z",
        "config": {"name": "m1", "problem": "toy", "seeds": [1, 2],
                   "arms": [{"name": "plain"}, {"name": "full"}]},
        "runs": [{"arm": "plain", "seed": 1, "status": "finished"},
                 {"arm": "plain", "seed": 2, "status": "running"},
                 {"arm": "full", "seed": 1, "status": "queued"},
                 {"arm": "full", "seed": 2, "status": "skipped", "message": "needs EXA_API_KEY"}]})
    _run(m / "plain" / "seed1", "finished", 2.3, "r-plain-1")
    _run(m / "plain" / "seed2", "running", 2.4, "r-plain-2")
    _write(m / "report.json", {"matrix": "m1", "partial": True})
    _write(exp / "m2" / "manifest.json", {"name": "m2", "config": {"arms": []}, "runs": []})
    _write(exp / "m2" / "report.json", '{"matrix": "m2", "par')             # caught mid-write
    monkeypatch.setattr(server, "LAB_RUNS", lab)
    monkeypatch.setattr(server, "EXP_RUNS", exp)
    return exp


def test_matrix_list_counts_runs(exp_root):
    c = TestClient(server.app)
    r = c.get("/api/exp")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    rows = {x["name"]: x for x in r.json()["matrices"]}
    m1 = rows["m1"]
    assert m1["problem"] == "toy" and m1["arms"] == ["plain", "full"] and m1["seeds"] == [1, 2]
    assert (m1["runs_planned"], m1["runs_finished"], m1["runs_running"]) == (4, 1, 1)
    assert m1["has_report"] is True and rows["m2"]["runs_planned"] == 0


def test_matrix_detail_and_mid_write_report(exp_root):
    c = TestClient(server.app)
    d = c.get("/api/exp/m1").json()
    assert d["report"] == {"matrix": "m1", "partial": True} and d["manifest"]["name"] == "m1"
    runs = {(r["arm"], r["seed"]): r for r in d["runs"]}
    assert runs[("plain", 1)]["final_confirm"] == 2.3 and runs[("plain", 1)]["run_id"] == "r-plain-1"
    assert runs[("full", 2)]["status"] == "skipped" and runs[("full", 2)]["has_run"] is False
    assert [r["arm"] for r in d["runs"]] == ["plain", "plain", "full", "full"]    # config order
    m2 = c.get("/api/exp/m2").json()
    assert m2["report"] is None and m2["report_exists"] is True and m2["report_readable"] is False
    assert c.get("/api/exp/nope").status_code == 404
    assert c.get("/api/exp/..%2Fm1").status_code == 404


def test_experiment_runs_open_like_lab_runs(exp_root):
    c = TestClient(server.app)
    rows = c.get("/api/lab/runs").json()["runs"]
    assert {(r["matrix"], r["arm"], r["seed"]) for r in rows} == {("m1", "plain", 1), ("m1", "plain", 2)}
    assert all(r["source"] == "exp" and r["problem"] == "toy" for r in rows)
    for seed in ("1", "seed1"):
        d = c.get(f"/api/exp/m1/plain/{seed}").json()
        assert d["run_id"] == "r-plain-1" and d["summary"]["status"] == "finished"
        assert len(d["findings"]) == 1 and d["incumbent_source"].startswith("# Change:")
    assert c.get("/api/exp/m1/plain/9").status_code == 404
    assert c.get("/api/exp/m1/plain/x1").status_code == 404
