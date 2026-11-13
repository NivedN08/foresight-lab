import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from lab.core.ledger import LedgerError, SealedLedger
from lab.core.stats import auc, bootstrap_ci, paired_bootstrap
from lab.core.task import Case, time_split

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def _forecast(i, target_offset_h=6):
    return dict(task="t", model="m", case_id=f"c{i}", issued_at=T0,
                target_time=T0 + timedelta(hours=target_offset_h), prediction={"v": i})


def test_ledger_chain_verifies(tmp_path):
    led = SealedLedger(tmp_path)
    led.seal_many([_forecast(i) for i in range(5)], now=T0)
    ok, n, msg = led.verify()
    assert ok and n == 5, msg
    assert led.head()[0] == 5


def test_ledger_detects_edit_after_sealing(tmp_path):
    led = SealedLedger(tmp_path)
    led.seal_many([_forecast(i) for i in range(4)], now=T0)
    lines = led.sealed_path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["prediction"]["v"] = 999  # quietly improve a forecast
    lines[1] = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    led.sealed_path.write_text("\n".join(lines) + "\n")
    ok, where, msg = led.verify()
    assert not ok and where == 1 and "modified" in msg


def test_ledger_detects_deleted_record(tmp_path):
    led = SealedLedger(tmp_path)
    led.seal_many([_forecast(i) for i in range(4)], now=T0)
    lines = led.sealed_path.read_text().splitlines()
    del lines[2]
    led.sealed_path.write_text("\n".join(lines) + "\n")
    assert not led.verify()[0]


def test_cannot_seal_the_past(tmp_path):
    led = SealedLedger(tmp_path)
    with pytest.raises(LedgerError):
        led.seal_many([_forecast(0, target_offset_h=-1)], now=T0)
    with pytest.raises(LedgerError):
        led.seal_many([_forecast(0, target_offset_h=0)], now=T0)
    assert led.head()[0] == 0


def test_cannot_grade_early_or_twice(tmp_path):
    led = SealedLedger(tmp_path)
    led.seal_many([_forecast(0, target_offset_h=6)], now=T0)
    with pytest.raises(LedgerError):
        led.grade(0, {"v": 1.0}, now=T0 + timedelta(hours=5))
    led.grade(0, {"v": 1.0}, now=T0 + timedelta(hours=6))
    with pytest.raises(LedgerError):
        led.grade(0, {"v": 2.0}, now=T0 + timedelta(hours=7))
    assert led.verify()[0]
    assert led.joined()[0]["grade"]["truth"] == {"v": 1.0}


def test_ungraded_due(tmp_path):
    led = SealedLedger(tmp_path)
    led.seal_many([_forecast(0, 1), _forecast(1, 6)], now=T0)
    due = led.ungraded_due(now=T0 + timedelta(hours=2))
    assert [r["case_id"] for r in due] == ["c0"]


def test_time_split_never_leaks_future_into_training():
    cases = [Case(f"c{i}", T0 + timedelta(hours=i), T0 + timedelta(hours=i + 6)) for i in range(200)]
    tr, va, te = time_split(cases)
    assert tr and va and te
    assert max(c.target_time for c in tr) < min(c.issue_time for c in va)
    assert max(c.target_time for c in va) < min(c.issue_time for c in te)


def test_bootstrap_and_paired():
    rng = np.random.default_rng(1)
    base = rng.normal(1.0, 0.2, 400)
    better = base - 0.1
    iv = bootstrap_ci(base)
    assert iv.low < iv.mean < iv.high and iv.n == 400
    res = paired_bootstrap(better, base)
    assert res.diff.high < 0 and res.p_a_better > 0.99
    same = paired_bootstrap(base, base)
    assert same.diff.mean == 0


def test_block_bootstrap_is_wider_than_case_bootstrap_on_correlated_errors():
    from lab.core.stats import paired_block_bootstrap
    rng = np.random.default_rng(3)
    n_blocks, per = 40, 50
    block_effect = rng.normal(0.0, 1.0, n_blocks)          # shared within a block
    blocks = np.repeat(np.arange(n_blocks), per)
    a = 5 + block_effect[blocks] + rng.normal(0, 0.1, n_blocks * per)
    b = 5 + rng.normal(0, 0.1, n_blocks * per)
    naive = paired_bootstrap(a, b)
    blocked = paired_block_bootstrap(a, b, blocks)
    assert abs(naive.diff.mean - blocked.diff.mean) < 1e-9
    assert (blocked.diff.high - blocked.diff.low) > 3 * (naive.diff.high - naive.diff.low)
    assert blocked.diff.n == n_blocks and blocked.n == n_blocks * per


def test_splits_reject_overlap():
    from lab.core.problem import Splits, Window
    def win(start_h, n):
        cs = [Case(f"c{start_h + i}", T0 + timedelta(hours=start_h + i), T0 + timedelta(hours=start_h + i + 1))
              for i in range(n)]
        return Window(cs, [{"v": 0.0}] * n)
    Splits(win(0, 5), win(10, 5), win(20, 5), win(30, 5)).check_order()
    with pytest.raises(ValueError):
        Splits(win(0, 12), win(10, 5), win(20, 5), win(30, 5)).check_order()


def test_auc():
    assert auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0
    assert abs(auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]) - 0.5) < 1e-9
