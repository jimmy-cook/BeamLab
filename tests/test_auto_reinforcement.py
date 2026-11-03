import json
from pathlib import Path

import pytest

from beam_lab.config import Config, plan_rows
from beam_lab.dataset import normalized_design, normalized_forces, summarize
from beam_lab.checks import case_rows
from beam_lab.ann_export import ann_beams, ann_stations


def study():
    return Config.load(Path(__file__).parents[1] / "examples/research_sample.json")


def test_required_api_areas_reach_both_ann_exports_without_manual_steel():
    base = list(plan_rows(study()))[0]
    combo = "U_1p2D_1p6L_FULL"
    raw_design = [dict(location_m=x, top_combo=combo, top_area_m2=0.0003215,
                       bottom_combo=combo, bottom_area_m2=0.0008452, shear_combo=combo,
                       shear_area_m2_per_m=0.000278, torsion_long_combo=combo,
                       torsion_long_area_m2=0., torsion_trans_combo=combo,
                       torsion_trans_area_m2_per_m=0., error="", warning="") for x in (0, 3, 6)]
    ds = normalized_design(base, raw_design)
    for d in ds:
        d["combination"] = combo
        assert d["reinforcement_source"] == "ETABS_required"
        assert d["As_top_mm2"] == pytest.approx(321.5)
        assert d["As_bottom_mm2"] == pytest.approx(845.2)
        assert d["Av_over_s_mm2_per_m"] == pytest.approx(278.)
    fs = normalized_forces(base, [dict(object="B1", element="B1", station_m=x,
        element_station_m=x, combination=combo, step_type="", step_number=0,
        P_kN=0., V2_kN=50., V3_kN=0., T_kNm=0., M2_kNm=0., M3_kNm=100.) for x in (0, 3, 6)])
    check, stations = case_rows(base, fs, ds, dict(flexure=[dict(UniqueName="B1", Status=None)],
                                                 shear=[dict(UniqueName="B1", Status=None)]))
    beam = summarize(base, fs, ds)
    beam.update(check)
    a = ann_beams([beam])[0]
    assert a["section_pass"] == 1
    assert a["reinforcement_regression_eligible"] == 1
    for zone in ("left", "mid", "right"):
        assert a[f"As_top_mm2_{zone}"] == pytest.approx(321.5)
        assert a[f"As_bottom_mm2_{zone}"] == pytest.approx(845.2)
        assert a[f"Av_over_s_mm2_per_m_{zone}"] == pytest.approx(278.)
    for r in ann_stations([beam], fs, ds):
        assert r["As_top_mm2"] == pytest.approx(321.5)
        assert r["As_bottom_mm2"] == pytest.approx(845.2)
        assert r["Av_over_s_mm2_per_m"] == pytest.approx(278.)
    for r in [a, check, *stations, *ds]:
        assert not any("provided" in key.lower() or "phiMn" in key for key in r)
    ds[1]["Av_over_s_mm2_per_m"] = None
    assert ann_stations([beam], fs, ds)[1]["section_station_status"] == "REVIEW"
    beam["Av_over_s_mm2_per_m_mid"] = None
    assert ann_beams([beam])[0]["reinforcement_regression_eligible"] == 0


def test_old_manual_settings_are_discarded_on_import():
    data = study().to_dict()
    data["provided_reinforcement"] = {"top_count": 10, "bottom_count": 2}
    clean = Config.parse(data).to_dict()
    assert "provided_reinforcement" not in clean
    assert clean == study().to_dict()
