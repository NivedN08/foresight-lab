import json
from pathlib import Path

import numpy as np
import pytest

from lab.tasks.turb.pireps import (dedupe, intensity_from_group, label, parse_awc_json,
                                   parse_iem_csv, parse_raw)

FIX = Path(__file__).parent / "fixtures" / "turb"
RAW = json.loads((FIX / "pirep_raw.json").read_text())


def test_fixture_has_enough_real_reports():
    assert len(RAW) >= 15


@pytest.mark.parametrize("case", RAW, ids=[c["raw"][:40] for c in RAW])
def test_parse_raw_reports(case):
    p = parse_raw(case["raw"])
    assert p["code"] == case["code"], case.get("note")
    assert p["fl"] == case["fl"]
    assert p["tb_fl"] == case["tb_fl"]
    assert p["tm"] == case["tm"]


def test_intensity_words_and_ranges():
    assert intensity_from_group("NEG") == 0
    assert intensity_from_group("SMOOTH") == 0
    assert intensity_from_group("LGT") == 2
    assert intensity_from_group("LGT-MOD") == 3
    assert intensity_from_group("MOD") == 4
    assert intensity_from_group("MOD-SEV") == 5
    assert intensity_from_group("SEV") == 6
    assert intensity_from_group("EXTRM") == 8
    assert intensity_from_group("CHOP") is None
    assert intensity_from_group("NEG BLO 300 MOD ABV 340") == 4  # layered, not adjacent: worst


def test_label_classes():
    assert label(0) == 0 and label(1) == 0 and label(2) == 0
    assert np.isnan(label(3))          # light-moderate dropped as ambiguous
    assert label(4) == 1 and label(5) == 1 and label(6) == 1 and label(8) == 1
    assert np.isnan(label(None))


def test_iem_csv_with_quote_marks():
    df = parse_iem_csv((FIX / "iem_sample.csv").read_text())
    # only reports with a turbulence group are kept
    assert set(df["code"]) == {0, 4}
    den = df[df["raw"].str.startswith("DEN")].iloc[0]
    assert den["code"] == 0 and den["fl"] == 180
    assert len(dedupe(df[df["raw"].str.contains("EAU UA /OV EAU010025")])) == 1


def test_awc_json_sample():
    items = json.loads((FIX / "awc_sample.json").read_text())
    df = parse_awc_json(items)
    assert len(df) >= 6
    assert df["time"].dt.tz is not None
    assert df["lat"].between(20, 55).all()
    # the AIREP "TB IC MOD RIME" is not a turbulence report
    assert not df["raw"].str.contains("TB IC MOD RIME").any()


def test_same_report_from_both_sources_is_kept_once():
    import pandas as pd
    t = pd.Timestamp("2026-11-13T22:07:00Z")
    df = pd.DataFrame({
        "time": [t, t, t],
        "lat": [26.7875, 26.9844, 30.0], "lon": [-93.0027, -92.6715, -90.0], "fl": [300.0, 300.0, 300.0],
        "code": [0.0, 0.0, 0.0],
        "raw": ["GLS UA /OV VUH140179/TM 2207/FL300/TP B738/TB NEG/RM ZHU",
                "000 UBUS31 KWBC 132210 GLS UA /OV VUH140179 /TM 2207 /FL300/TP B738/TB NEG/RM ZHU",
                "MSY UA /OV LEV135330/TM 2207/FL300/TP B39M/TB NEG"],
        "source": ["iem", "awc", "awc"]})
    out = dedupe(df)
    assert len(out) == 2 and out.iloc[0]["source"] == "iem"
