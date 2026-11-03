import csv
from dataclasses import replace
from pathlib import Path

import pytest

from beam_lab.config import Config, planned_model_count, search_groups
from beam_lab.dataset import candidate_clear
from beam_lab.pipeline import run, smallest_passing
from beam_lab.sampling import ResearchRanges, sample_study


@pytest.mark.parametrize("n", range(0, 40))
def test_bisection_finds_the_smallest_passing_depth_within_the_log2_bound(n):
    depths = [350+25*i for i in range(n)]
    for need in range(n+1):
        calls = []
        def passes(h):
            calls.append(h)
            return need < n and h >= depths[need]
        answer, probes, finished = smallest_passing(depths, passes)
        assert finished and answer == (depths[need] if need < n else None)
        assert len(calls) == len(probes) <= n.bit_length()


def test_bisection_stops_when_a_probe_cannot_be_judged():
    answer, probes, finished = smallest_passing([350, 375, 400], lambda h: None)
    assert (answer, finished) == (None, False) and probes == [(375, None)]


def test_sampler_lists_every_allowed_depth_of_each_search_width():
    config = sample_study(ResearchRanges(count=10, seed=4, size_search=True))
    groups = dict(search_groups(config.sections_mm))
    assert sorted(groups) == [300, 400, 500, 600]
    # 0.3h ≤ b ≤ h on the 25 mm grid
    assert groups[300] == list(range(350, 1001, 25)) and groups[600] == list(range(600, 1201, 25))
    assert [len(d) for d in groups.values()] == [27, 33, 29, 25]
    assert config.size_search and config.planned_models() == 10*(5+6+5+5) == config.max_models


def test_size_search_keeps_the_sampled_beam_lines_reproducible():
    ranges = ResearchRanges(count=20, seed=8, size_search=True, equal_spans=False, max_adjacent_span_ratio=1.5)
    assert sample_study(ranges).to_dict() == sample_study(ranges).to_dict()


@pytest.mark.parametrize("widths,match", [((310,), "not on the section grid"), ((300, 300), "distinct"), ((), "distinct"),
                                          ((300.0,), "distinct")])
def test_invalid_search_widths_are_rejected(widths, match):
    with pytest.raises(ValueError, match=match):
        ResearchRanges(size_search=True, search_widths_mm=widths).validate()


def test_size_search_budget_uses_the_worst_case_run_count():
    with pytest.raises(ValueError, match="up to 21,000"):
        ResearchRanges(count=1000, size_search=True).validate()
    assert planned_model_count(3, [[300, 350], [300, 375], [300, 400]], True) == 3*2
    assert planned_model_count(3, [[300, 350], [300, 375], [300, 400]], False) == 9


def study(needs, depth_ratio=0., serviceability=None, **extra):
    """One 6 m span; width b passes from depth needs[b] upward."""
    sections = [[b, h] for b in sorted(needs) for h in range(max(350, b), 1201, 25)]
    data = dict(scenarios=[dict(name="Search line", spans_m=[6], dead_kN_m=[20], live_kN_m=[10], fc_MPa=30, fy_MPa=420)],
                sections_mm=sections, loading_protocol="research_full_udl", dead_load_basis="total_including_self_weight",
                min_span_depth_ratio=depth_ratio, size_search=True, max_models=100, **extra)
    if serviceability:
        data["serviceability"] = serviceability
    return Config.parse(data)


def adapter(needs, built, error_at=None):
    class Adapter:
        version = "test"
        close_error = None
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def build(self, scenario, b, h, *args, **kwargs):
            built.append((b, h))
            if (b, h) == error_at:
                raise RuntimeError("ETABS stopped responding")
            self.passing = h >= needs[b]
            # Like ETABS: self-weight is added only when D excludes it.
            dead = scenario.dead_kN_m[0] + (24*b*h/1e6 if kwargs["dead_load_basis"] == "superimposed_plus_self_weight" else 0)
            live, settings = scenario.live_kN_m[0], kwargs.get("serviceability")
            fraction = settings.sustained_live_fraction or 0 if settings else 0
            self.q = {"SLS_D": dead, "SLS_DL": dead+live, "SLS_SUSTAINED": dead+live*fraction, "U_1p2D_1p6L_FULL": 1.2*dead+1.6*live}
            return ["B1"], ["U_1p2D_1p6L_FULL"], ["LAB_DEAD", "LAB_LIVE"]
        def analyze(self, cases): return [dict(case=c, status=4) for c in cases]
        def forces(self, frame, combos):
            rows = []
            for combo in combos:
                q = self.q[combo]
                for i in range(21):
                    x = i*.3
                    rows.append(dict(object="B1", element="B1", station_m=x, element_station_m=x, combination=combo,
                                     step_type="", step_number=0, P_kN=0, V2_kN=q*(x-3), V3_kN=0, T_kNm=0,
                                     M2_kNm=0, M3_kNm=q*x*(6-x)/2))
            return rows
        def start_design(self): pass
        def design(self, frame):
            return [dict(frame="B1", location_m=i*.3, top_combo="U", top_area_m2=.0005, bottom_combo="U", bottom_area_m2=.0012,
                         shear_combo="U", shear_area_m2_per_m=.00025, torsion_long_combo="U", torsion_long_area_m2=0,
                         torsion_trans_combo="U", torsion_trans_area_m2_per_m=0, error="", warning="") for i in range(21)]
        def combination_designs(self, frames, combos, progress):
            status = None if self.passing else "O/S #3"
            return {combos[0]: dict(designs={"B1": self.design("B1")}, flexure=[dict(UniqueName="B1", Status=status)],
                                    shear=[dict(UniqueName="B1", Status=None)])}
        def table_catalog(self): return []
    return Adapter


def read(path, name):
    with (path/name).open(encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def test_search_runs_only_bisection_probes_and_targets_the_smallest_area(tmp_path):
    needs = {300: 650, 400: 575}
    built = []
    manifest = run(study(needs), tmp_path, adapter_factory=adapter(needs, built))
    assert manifest["state"] == "completed"
    depths = dict(search_groups(study(needs).sections_mm))
    for b in needs:
        assert len([h for w, h in built if w == b]) <= len(depths[b]).bit_length()
    assert len(built) < len(study(needs).sections_mm)/4
    rows = {int(r["b_mm"]): r for r in read(tmp_path, "size_search.csv")}
    assert {b: (r["search_status"], float(r["smallest_passing_h_mm"])) for b, r in rows.items()} == {300: ("found", 650), 400: ("found", 575)}
    assert int(rows[300]["etabs_runs"]) == len([h for w, h in built if w == 300])
    # 300 × 650 = 195,000 mm² beats 400 × 575 = 230,000 mm²
    target = read(tmp_path, "size_targets.csv")[0]
    assert (float(target["preliminary_b_mm"]), float(target["preliminary_h_mm"])) == (300, 650)
    assert int(target["candidates_tested"]) == len(built)
    assert manifest["total_candidates"] == manifest["completed_candidates"] == len(built)
    assert len(read(tmp_path, "candidate_plan.csv")) == len(read(tmp_path, "runs.csv")) == len(built)


def test_deep_beam_depths_are_excluded_from_the_search(tmp_path):
    needs = {300: 650, 400: 900}
    built = []
    # span 6 m ≤ 8h for h ≥ 750: only 350–725 can be searched
    run(study(needs, depth_ratio=8.), tmp_path, adapter_factory=adapter(needs, built))
    assert built and all(h < 750 for _, h in built)
    rows = {int(r["b_mm"]): r for r in read(tmp_path, "size_search.csv")}
    assert rows[300]["search_status"] == "found" and float(rows[300]["max_valid_h_mm"]) == 725
    assert rows[400]["search_status"] == "none_passed" and rows[400]["smallest_passing_h_mm"] == ""


def test_a_failed_model_ends_only_that_width_search(tmp_path):
    needs = {300: 650, 400: 575}
    depths = dict(search_groups(study(needs).sections_mm))[300]
    first = depths[(len(depths)-1)//2]
    built = []
    manifest = run(study(needs), tmp_path, adapter_factory=adapter(needs, built, error_at=(300, first)))
    assert manifest["state"] == "completed_with_failures"
    rows = {int(r["b_mm"]): r for r in read(tmp_path, "size_search.csv")}
    assert rows[300]["search_status"] == "run_error" and rows[300]["probes"] == f"{first}:RUN_ERROR"
    assert rows[400]["search_status"] == "found"


def test_stop_request_ends_the_search_and_keeps_completed_models(tmp_path):
    needs = {300: 650, 400: 575}
    built = []
    manifest = run(study(needs), tmp_path, adapter_factory=adapter(needs, built), stop_requested=lambda: len(built) >= 2)
    assert manifest["state"] == "stopped" and len(built) == 2
    rows = read(tmp_path, "size_search.csv")
    assert len(rows) == 1 and rows[0]["search_status"] == "stopped" and rows[0]["probes"].endswith("NOT_RUN")


def test_deflection_checks_join_the_pass_rule_and_the_ann_size_target(tmp_path):
    needs = {300: 650, 400: 575}
    built = []
    sls = dict(enabled=True, category="beam_sensitive", sustained_live_fraction=.25, duration_months=60,
               attachment_timing="after_immediate_dead")
    manifest = run(study(needs, serviceability=sls), tmp_path, adapter_factory=adapter(needs, built))
    assert manifest["size_search_pass_rule"] == "strength_and_deflection"
    beams = read(tmp_path, "beams.csv")
    for row in read(tmp_path, "size_search.csv"):
        b = float(row["b_mm"])
        clear = sorted(float(r["h_mm"]) for r in beams if float(r["b_mm"]) == b and
                       candidate_clear([dict(r, number_spans=1, etabs_summary_clear=int(r["etabs_summary_clear"]))], True))
        assert row["smallest_passing_h_mm"] == "" if not clear else float(row["smallest_passing_h_mm"]) == clear[0]
    size = read(tmp_path, "ann_size_wide.csv")
    target = read(tmp_path, "size_targets_sls.csv")[0]
    if target["preliminary_h_mm"]:
        assert (size[0]["target_b_mm"], size[0]["target_h_mm"]) == (target["preliminary_b_mm"], target["preliminary_h_mm"])


def test_verification_study_example_loads_as_a_small_search():
    config = Config.load(Path(__file__).parent.parent/"examples"/"verify_size_search_study.json")
    assert config.size_search and len(config.scenarios) == 10 and config.serviceability.enabled
    assert sorted(dict(search_groups(config.sections_mm))) == [300, 400] and config.planned_models() == 110


def verification_runs(tmp_path, needs, full_needs=None):
    from beam_lab.verify_search import compare
    config = study(needs)
    run(config, tmp_path/"search", adapter_factory=adapter(needs, []))
    run(replace(config, size_search=False), tmp_path/"full", adapter_factory=adapter(full_needs or needs, []))
    return compare(tmp_path/"search", tmp_path/"full")


def test_verification_confirms_a_search_that_matches_testing_every_size(tmp_path):
    report, summary = verification_runs(tmp_path, {300: 650, 400: 575})
    assert summary["verdict"].startswith("PASS") and summary["same_smallest_depth"] == summary["widths_judged"] == 2
    assert summary["lines_with_same_target"] == 1 and summary["search_etabs_runs"] < summary["full_etabs_runs"]
    assert {r["full_smallest_h_mm"] for r in report} == {650, 575} and all(r["full_target"] == "300 x 650" for r in report)


def test_verification_flags_a_deeper_beam_that_fails(tmp_path):
    # Full run: 300 wide passes at 450–500, fails 525–600, passes again from 625: not monotonic.
    weird = {300: 0, 400: 575}
    from beam_lab.verify_search import compare
    config = study({300: 650, 400: 575})
    run(config, tmp_path/"search", adapter_factory=adapter({300: 650, 400: 575}, []))
    full = adapter(weird, [])
    original = full.build
    def build(self, scenario, b, h, *args, **kwargs):
        result = original(self, scenario, b, h, *args, **kwargs)
        self.passing = h >= 575 if b == 400 else (450 <= h <= 500 or h >= 625)
        return result
    full.build = build
    run(replace(config, size_search=False), tmp_path/"full", adapter_factory=full)
    report, summary = compare(tmp_path/"search", tmp_path/"full")
    row = next(r for r in report if r["b_mm"] == 300)
    assert row["deeper_always_passes"] == 0 and row["violations"].startswith("450 PASS but 525 FAIL")
    assert row["same_answer"] == 0 and row["full_smallest_h_mm"] == 450
    assert summary["verdict"].startswith("CHECK") and summary["widths_where_deeper_failed"] == 1


def test_verification_refuses_runs_of_different_studies(tmp_path):
    from beam_lab.verify_search import compare
    run(study({300: 650}), tmp_path/"search", adapter_factory=adapter({300: 650}, []))
    run(replace(study({400: 575}), size_search=False), tmp_path/"full", adapter_factory=adapter({400: 575}, []))
    with pytest.raises(ValueError, match="different studies"):
        compare(tmp_path/"search", tmp_path/"full")


def test_ann_size_exports_superimposed_d_when_etabs_adds_self_weight(tmp_path):
    needs = {300: 650, 400: 575}
    sls = dict(enabled=True, limit_mode="user_defined", total_basis="immediate", dead_limit_divisor=120., total_limit_divisor=240.)
    config = replace(study(needs, serviceability=sls), dead_load_basis="superimposed_plus_self_weight")
    built = []
    seen = []
    factory = adapter(needs, built)
    original = factory.build
    def build(self, *args, **kwargs):
        seen.append(kwargs["dead_load_basis"])
        return original(self, *args, **kwargs)
    factory.build = build
    run(config, tmp_path, adapter_factory=factory)
    assert set(seen) == {"superimposed_plus_self_weight"}
    size = read(tmp_path, "ann_size_wide.csv")
    assert len(size) == 1 and size[0]["dead_load_basis"] == "superimposed_plus_self_weight"
    # The ANN input is the entered superimposed D, not D plus the chosen beam's weight.
    assert float(size[0]["dead_input_kN_m"]) == 20 and size[0]["target_b_mm"]


def test_unticking_the_search_runs_every_listed_size(tmp_path):
    needs = {300: 650}
    built = []
    config = replace(study(needs), size_search=False, max_models=100)
    manifest = run(config, tmp_path, adapter_factory=adapter(needs, built))
    assert manifest["state"] == "completed" and len(built) == len(config.sections_mm)
    assert not (tmp_path/"size_search.csv").exists()
    target = read(tmp_path, "size_targets.csv")[0]
    assert float(target["preliminary_h_mm"]) == 650
