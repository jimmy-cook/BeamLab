import copy
import csv
import json
from pathlib import Path

import pytest

from beam_lab.config import Config, patterns, plan_rows
from beam_lab.dataset import normalized_design, normalized_forces, size_labels, split_for, summarize, write_csv
from beam_lab.etabs import EtabsError, arrays, checked
from beam_lab.pipeline import run


def configuration():
    return Config.parse({"scenarios": [{"name": "test", "spans_m": [6, 4],
                                        "dead_super_kN_m": [15, 15], "live_kN_m": [10, 10]}],
                         "sections_mm": [[250, 450], [300, 600]]})


def test_self_weight_and_strength_units():
    rows = list(plan_rows(configuration()))
    assert rows[0]["self_weight_kN_m"] == pytest.approx(2.7)
    assert rows[0]["max_applied_gravity_factored_UDL_kN_m"] == pytest.approx(37.24)
    assert rows[1]["left_span_ratio"] == 1.5
    assert len(patterns(4)) == 16


@pytest.mark.parametrize("replacement", [float("nan"), float("inf"), -1, True])
def test_invalid_numeric_inputs(replacement):
    data = configuration().to_dict()
    data["scenarios"][0]["spans_m"][0] = replacement
    with pytest.raises(ValueError):
        Config.parse(data)


def test_invalid_shape_and_unknown_fields():
    data = configuration().to_dict()
    data["scenarios"][0]["live_kN_m"] = [10]
    with pytest.raises(ValueError):
        Config.parse(data)
    with pytest.raises(ValueError):
        Config.parse(dict(configuration().to_dict(), stations_per_span=20))
    with pytest.raises(ValueError):
        Config.parse(dict(configuration().to_dict(), unknown=1))


def test_api_return_guard():
    assert checked(["value", 0], "op") == ["value"]
    with pytest.raises(EtabsError):
        checked(-1, "op")
    with pytest.raises(EtabsError):
        arrays([2, [1], 0], ["a"], "op")


def force(combo="U1", moment=100, shear=20, axial=1):
    return dict(object="B1", station_m=3., element="1", element_station_m=3., combination=combo,
                step_type="", step_number=0., P_kN=axial, V2_kN=shear, V3_kN=0., T_kNm=0., M2_kNm=0., M3_kNm=moment)


def design(error="", warning="", location=0.):
    return dict(frame="B1", location_m=location, top_combo="U1", top_area_m2=0.001,
                bottom_combo="U2", bottom_area_m2=0.0015, shear_combo="U1", shear_area_m2_per_m=0.0004,
                torsion_long_combo="U1", torsion_long_area_m2=0., torsion_trans_combo="U1",
                torsion_trans_area_m2_per_m=0., error=error, warning=warning)


def test_concurrent_demands_never_mix_combinations():
    base = list(plan_rows(configuration()))[0]
    fs = normalized_forces(base, [force("M_governs", 100, 5, 2), force("V_governs", 10, 50, 8)])
    ds = normalized_design(base, [design()])
    row = summarize(base, fs, ds)
    assert row["max_abs_Mu3_kNm"] == 100
    assert row["at_max_abs_Mu3_kNm_Vu2_kN"] == 5
    assert row["at_max_abs_Vu2_kN_Pu_kN"] == 8
    assert row["full_aci_verified"] == 0


def test_rebar_conversion_and_failed_design():
    base = list(plan_rows(configuration()))[0]
    ds = normalized_design(base, [design("Section fails", location=0.), design(location=3.)])
    assert ds[0]["As_top_mm2"] == 1000
    assert ds[0]["Av_over_s_mm2_per_m"] == pytest.approx(400)
    row = summarize(base, normalized_forces(base, [force()]), ds)
    assert row["etabs_summary_clear"] == 0
    assert row["design_status"] == "errors"
    assert row["As_top_mm2_right"] is None


def test_whole_model_sizing_labels_and_group_split():
    bases = list(plan_rows(configuration()))
    rows = [summarize(b, normalized_forces(b, [force()]), normalized_design(b, [design()])) for b in bases]
    rows[1]["etabs_summary_clear"] = 0
    labels = size_labels(rows)
    assert all(x["preliminary_b_mm"] == 300 for x in labels)
    assert len({x["split"] for x in rows}) == 1
    assert all(x["complete_design_verified"] == 0 for x in labels)
    assert "dead_total_kN_m" not in labels[0]  # size-dependent self-weight is a target leak


def test_pipeline_does_not_fabricate_results_on_startup_failure(tmp_path):
    class Unavailable:
        def __init__(self, *args): pass
        def __enter__(self): raise EtabsError("License/startup unavailable")
        def __exit__(self, *args): pass
    manifest = run(configuration(), tmp_path, adapter_factory=Unavailable)
    assert manifest["state"] == "failed"
    assert manifest["completed_candidates"] == 0
    assert list(csv.DictReader((tmp_path/"beams.csv").open(encoding="utf-8-sig"))) == []
    assert len(list(csv.DictReader((tmp_path/"candidate_plan.csv").open(encoding="utf-8-sig")))) == 4
    with pytest.raises(ValueError):
        run(configuration(), tmp_path, adapter_factory=Unavailable)


def test_csv_preserves_commas_quotes_and_unicode(tmp_path):
    path = tmp_path/"test.csv"
    write_csv(path, [{"warning": 'Check, "beam" – λ', "value": None}])
    assert next(csv.DictReader(path.open(encoding="utf-8-sig")))["warning"] == 'Check, "beam" – λ'


def test_failed_design_retains_error_when_an_area_is_unavailable():
    base = list(plan_rows(configuration()))[0]
    failed = design(error="Reinforcing required exceeds maximum allowed")
    failed["top_area_m2"] = -1
    rows = normalized_design(base, [failed])
    assert rows[0]["As_top_mm2"] is None
    assert rows[0]["error"] == failed["error"]
    assert summarize(base, normalized_forces(base, [force()]), rows)["As_top_mm2_left"] is None


def test_invalid_design_area_without_failure_evidence_is_not_a_valid_result():
    base = list(plan_rows(configuration()))[0]
    invalid = design()
    invalid["top_area_m2"] = float("nan")
    with pytest.raises(ValueError):
        normalized_design(base, [invalid])
