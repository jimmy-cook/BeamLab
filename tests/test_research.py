import csv
from dataclasses import replace

import pytest

from beam_lab.config import Config, plan_rows, strength_combinations
from beam_lab.sampling import ResearchRanges, sample_study, factorial_counts
from beam_lab.pipeline import run
from beam_lab.ann_export import ann_beams, binary_status
from beam_lab.dataset import summarize


def research_data():
    return dict(scenarios=[dict(name="shared UDL", spans_m=[6, 8], dead_kN_m=[20, 20], live_kN_m=[10, 10],
                               fc_MPa=30, fy_MPa=420)], sections_mm=[[300,600]], loading_protocol="research_full_udl",
                dead_load_basis="total_including_self_weight", enforce_research_ranges=True)


def test_total_dead_is_not_double_counted_and_spans_have_identical_udl():
    config = Config.parse(research_data())
    rows = list(plan_rows(config))
    assert {r["Wu_kN_m"] for r in rows} == {40}
    assert {r["dead_total_kN_m"] for r in rows} == {20}
    assert rows[0]["self_weight_kN_m"] == pytest.approx(4.32)
    assert rows[0]["dead_super_kN_m"] == pytest.approx(15.68)
    assert strength_combinations(2, config.loading_protocol) == {"U_1p2D_1p6L_FULL": {"LAB_DEAD": 1.2, "LAB_LIVE": 1.6}}


def test_unequal_span_loads_are_rejected_in_research_protocol():
    data = research_data()
    data["scenarios"][0]["live_kN_m"][1] = 11
    with pytest.raises(ValueError, match="same D"):
        Config.parse(data)


@pytest.mark.parametrize("field,value", [("spans_m", [2.999, 8]), ("dead_kN_m", [9.9, 9.9]),
                                          ("live_kN_m", [150.1,150.1]), ("fc_MPa", 19.9), ("fy_MPa", 601)])
def test_research_limits_are_enforced(field, value):
    data = research_data()
    data["scenarios"][0][field] = value
    with pytest.raises(ValueError):
        Config.parse(data)


def test_research_endpoints_and_section_limits():
    data = research_data()
    data["scenarios"][0].update(spans_m=[3,12], fc_MPa=20, fy_MPa=200)
    data["sections_mm"] = [[300,350],[900,1200]]
    Config.parse(data)
    data["sections_mm"] = [[299,350]]
    with pytest.raises(ValueError):
        Config.parse(data)


def test_sampler_reproduces_all_user_ranges_and_shared_loads():
    config = sample_study(ResearchRanges(count=100, seed=7))
    assert config.to_dict() == sample_study(ResearchRanges(count=100, seed=7)).to_dict()
    assert [300,350] in config.sections_mm and [900,1200] in config.sections_mm
    assert len(config.sections_mm) == 6
    assert config.loading_protocol == "research_full_udl"
    assert config.dead_load_basis == "superimposed_plus_self_weight"
    for s in config.scenarios:
        assert len(set(s.dead_kN_m)) == len(set(s.live_kN_m)) == 1
        assert 10 <= s.dead_kN_m[0] <= 150
        assert 10 <= s.live_kN_m[0] <= 150
        assert all(3 <= length <= 12 for length in s.spans_m)
        assert 20 <= s.fc_MPa <= 50
        assert 200 <= s.fy_MPa <= 600
        assert 200 <= s.fyt_MPa <= 420


def test_default_25mm_grid_samples_intermediate_sizes_and_keeps_endpoints():
    ranges = ResearchRanges(count=1, candidate_count=100, seed=7)
    assert ranges.section_step_mm == 25
    config = sample_study(ranges)
    assert [300,350] in config.sections_mm and [900,1200] in config.sections_mm
    assert all(b % 25 == 0 and h % 25 == 0 for b,h in config.sections_mm)
    assert any(b % 50 != 0 or h % 50 != 0 for b,h in config.sections_mm)


def test_first_stage_defaults_are_three_equal_spans_with_values_sampled_within_ranges():
    config=sample_study(ResearchRanges(count=30,seed=9))
    for s in config.scenarios:
        assert len(s.spans_m)==3 and len(set(s.spans_m))==1
    assert any(s.fc_MPa!=int(s.fc_MPa) for s in config.scenarios)
    assert not ResearchRanges().whole_unit_parameters


def test_whole_unit_full_factorial_count_is_exact_without_enumerating_models():
    counts=factorial_counts(ResearchRanges(whole_unit_parameters=True,span_step_mm=1,min_width_depth_ratio=0,max_width_depth_ratio=0,
                                           min_live_dead_ratio=0,max_live_dead_ratio=0,fc_max=40,fy_max=517,stirrup_matches_fy=True))
    assert counts['span_length_levels']==9001
    assert counts['section_pairs']==875
    assert counts['base_scenarios']==1195020627318
    assert counts['candidate_models']==1045643048903250
    assert counts['beam_candidate_rows']==3136929146709750


def test_factorial_equal_and_independent_span_counts_differ():
    ranges=ResearchRanges(span_min_mm=3000,span_max_mm=3001,dead_min=10,dead_max=10,live_min=10,live_max=10,
                          fc_min=20,fc_max=20,fy_min=200,fy_max=200,fyt_min=200,fyt_max=200,width_max_mm=325,depth_max_mm=375,candidate_count=1)
    equal=factorial_counts(ranges)
    independent=factorial_counts(replace(ranges,equal_spans=False))
    assert equal['candidate_models']==8 and independent['candidate_models']==32
    assert equal['beam_candidate_rows']==24 and independent['beam_candidate_rows']==96


def neighbour_ratios(spans):
    return [max(a,b)/min(a,b) for a,b in zip(spans,spans[1:])]


def test_default_span_grid_is_250mm_with_endpoints():
    ranges=ResearchRanges(count=500,seed=3,equal_spans=False)
    assert ranges.span_step_mm==250 and ranges.max_adjacent_span_ratio==0
    lengths={round(length*1000) for s in sample_study(ranges).scenarios for length in s.spans_m}
    assert lengths<={3000+250*i for i in range(37)}
    assert {3000,12000}<=lengths


@pytest.mark.parametrize("equal", [True, False])
def test_1mm_span_step_reproduces_the_previous_sampler(equal):
    import itertools, random
    ranges=ResearchRanges(count=20,seed=11,span_step_mm=1,equal_spans=equal,min_width_depth_ratio=0,max_width_depth_ratio=0,
                          min_live_dead_ratio=0,max_live_dead_ratio=0,fc_max=40,fy_max=517,stirrup_matches_fy=True)
    config=sample_study(ranges)
    # Replay the 0.7.0 draw order: sections, then per scenario n, D, L, fy, randint span(s), fc.
    legacy=random.Random(ranges.seed)
    sections=list(itertools.product(range(300,901,25),range(350,1201,25)))
    legacy.sample(sections[1:-1],ranges.candidate_count-2)
    for s in config.scenarios:
        n=legacy.randint(3,3)
        legacy.randint(10000,150000), legacy.randint(10000,150000), legacy.randint(200000,517000)
        lengths=[legacy.randint(3000,12000)/1000 for _ in range(1 if equal else n)]
        legacy.randint(20000,40000)
        assert s.spans_m==(lengths*n if equal else lengths)


def test_neighbour_ratio_limit_is_respected_for_unequal_spans():
    config=sample_study(ResearchRanges(count=300,seed=5,equal_spans=False,max_adjacent_span_ratio=1.5))
    ratios=[r for s in config.scenarios for r in neighbour_ratios(s.spans_m)]
    assert max(ratios)<=1.5+1e-12
    assert any(len(set(s.spans_m))>1 for s in config.scenarios)


def test_neighbour_ratio_sampling_is_uniform_over_allowed_arrangements():
    import itertools
    from collections import Counter
    ranges=ResearchRanges(count=1000,seed=1,equal_spans=False,span_min_mm=3000,span_max_mm=3500,
                          max_adjacent_span_ratio=1.1,candidate_count=1)
    allowed={a for a in itertools.product((3.0,3.25,3.5),repeat=3) if max(neighbour_ratios(a))<=1.1}
    seen=Counter(tuple(s.spans_m) for s in sample_study(ranges).scenarios)
    assert set(seen)==allowed
    expected=ranges.count/len(allowed)
    assert all(0.6*expected<n<1.4*expected for n in seen.values())


@pytest.mark.parametrize("ratio,unequal", [(0.,50653),(2.0,31465),(1.5,15353),(1.2,4079)])
def test_span_arrangement_counts_on_250mm_grid(ratio,unequal):
    ranges=ResearchRanges(max_adjacent_span_ratio=ratio)
    assert factorial_counts(ranges)['span_arrangements']==37
    assert factorial_counts(replace(ranges,equal_spans=False))['span_arrangements']==unequal


@pytest.mark.parametrize("field,value", [("span_step_mm",0),("span_step_mm",2.5),("max_adjacent_span_ratio",0.9),
                                          ("max_adjacent_span_ratio",4.5),("max_adjacent_span_ratio",True)])
def test_invalid_span_grid_settings_are_rejected(field,value):
    with pytest.raises(ValueError):
        replace(ResearchRanges(),**{field:value}).validate()


def test_finer_factorial_levels_use_decimal_resolution_exactly():
    ranges=ResearchRanges(whole_unit_parameters=False,fc_min=20.001,fc_max=20.003)
    assert factorial_counts(ranges)['fc']==3


def test_complete_25mm_section_grid_can_be_requested_without_running_etabs():
    config=sample_study(ResearchRanges(count=1,candidate_count=875,min_width_depth_ratio=0,max_width_depth_ratio=0))
    assert len(config.sections_mm)==875
    assert len({tuple(pair) for pair in config.sections_mm})==875


@pytest.mark.parametrize("low,high,pairs", [(0.3,1.,607),(0,1.,622),(0.3,2.,840),(0,0,875)])
def test_width_depth_rule_counts_section_pairs(low,high,pairs):
    counts=factorial_counts(ResearchRanges(min_width_depth_ratio=low,max_width_depth_ratio=high))
    assert counts['grid_section_pairs']==875 and counts['section_pairs']==pairs


def test_default_sampler_keeps_sections_within_03h_and_h():
    ranges=ResearchRanges(count=1,candidate_count=607)
    assert (ranges.min_width_depth_ratio,ranges.max_width_depth_ratio)==(0.3,1.)
    config=sample_study(ranges)
    assert len({tuple(pair) for pair in config.sections_mm})==607
    assert all(0.3*h<=b<=h for b,h in config.sections_mm)
    assert [300,350] in config.sections_mm and [900,1200] in config.sections_mm
    assert (config.min_width_depth_ratio,config.max_width_depth_ratio)==(0.3,1.)
    with pytest.raises(ValueError, match="607 section pairs"):
        sample_study(replace(ranges,candidate_count=608))


@pytest.mark.parametrize("section", [[700,400],[300,1200]])
def test_config_rejects_sections_outside_its_width_depth_rule(section):
    data=research_data()
    data.update(sections_mm=[section],min_width_depth_ratio=0.3,max_width_depth_ratio=1.)
    with pytest.raises(ValueError, match="b/h"):
        Config.parse(data)
    data.update(min_width_depth_ratio=0,max_width_depth_ratio=0)
    assert Config.parse(data).sections_mm==[section]


@pytest.mark.parametrize("low,high", [(1.2,1.),(-0.1,1.),(0.3,True)])
def test_invalid_width_depth_limits_are_rejected(low,high):
    with pytest.raises(ValueError):
        replace(ResearchRanges(),min_width_depth_ratio=low,max_width_depth_ratio=high).validate()


def deep_beam_data(sections, ratio=4.):
    data=research_data()
    data["scenarios"][0].update(spans_m=[3,3],dead_kN_m=[20,20],live_kN_m=[10,10])
    data.update(sections_mm=sections,min_span_depth_ratio=ratio)
    return data


def test_span_of_4h_or_less_is_an_invalid_deep_beam():
    rows=list(plan_rows(Config.parse(deep_beam_data([[300,750],[300,725]]))))
    deep=[r for r in rows if r["h_mm"]==750]
    normal=[r for r in rows if r["h_mm"]==725]
    assert all(r["input_valid"]==0 and "Deep beam: span 3000 mm ≤ 4h = 3000 mm" in r["input_validation_error"] for r in deep)
    assert all(r["input_valid"]==1 and r["input_validation_error"]=="" for r in normal)
    assert all(r["input_valid"]==1 for r in plan_rows(Config.parse(deep_beam_data([[300,750]],ratio=0))))


def test_deep_beam_and_self_weight_reasons_are_both_recorded():
    data=deep_beam_data([[900,1200]])
    data["scenarios"][0].update(dead_kN_m=[10,10])
    error=list(plan_rows(Config.parse(data)))[0]["input_validation_error"]
    assert "self-weight" in error and "Deep beam" in error


def test_deep_beams_never_reach_etabs_or_training_rows(tmp_path):
    class NoAnalysis:
        version = "22.5.0"
        close_error = None
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def build(self, *args, **kwargs): raise AssertionError("Deep beams must not reach ETABS")
    manifest = run(Config.parse(deep_beam_data([[300,1000]])), tmp_path, adapter_factory=NoAnalysis)
    assert manifest["state"] == "completed_with_exclusions" and manifest["deep_beam_candidates"] == 1
    with (tmp_path/"runs.csv").open(encoding="utf-8-sig") as stream:
        assert "Deep beam" in list(csv.DictReader(stream))[0]["error"]
    with (tmp_path/"ann_beams.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert rows and all(r["input_valid"]=="0" and r["reinforcement_regression_eligible"]=="0"
                        and r["classification_eligible"]=="0" for r in rows)


def test_sampler_records_the_deep_beam_rule():
    ranges=ResearchRanges(count=3)
    assert ranges.min_span_depth_ratio==4.
    assert sample_study(ranges).min_span_depth_ratio==4.


@pytest.mark.parametrize("ratio", [-1, True, 31])
def test_invalid_span_depth_rule_is_rejected(ratio):
    with pytest.raises(ValueError):
        replace(ResearchRanges(),min_span_depth_ratio=ratio).validate()
    with pytest.raises(ValueError):
        Config.parse(deep_beam_data([[300,600]],ratio=ratio))


def test_invalid_total_dead_below_self_weight_is_not_structural_failure(tmp_path):
    data = research_data()
    data["scenarios"][0]["dead_kN_m"] = [10,10]
    data["sections_mm"] = [[900,1200]]
    config = Config.parse(data)
    class NoAnalysis:
        version = "22.5.0"
        close_error = None
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def build(self, *args, **kwargs): raise AssertionError("Invalid loads must not reach ETABS")
    manifest = run(config, tmp_path, adapter_factory=NoAnalysis)
    assert manifest["state"] == "completed_with_exclusions"
    with (tmp_path/"ann_beams.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert all(r["input_valid"] == "0" and r["section_pass"] == "" and r["classification_eligible"] == "0" for r in rows)


def test_ann_pass_fail_does_not_turn_missing_results_into_zero_demands():
    base = list(plan_rows(Config.parse(research_data())))[0]
    row = summarize(base, [], [])
    row.update(section_overall_status="NOT_EVALUATED", provided_overall_status="NOT_EVALUATED")
    output = ann_beams([row])[0]
    assert output["Mu_kNm"] is None and output["Pu_kN"] is None
    assert output["section_pass"] is None
    assert binary_status("PASS") == 1 and binary_status("FAIL") == 0
    assert binary_status("REVIEW") is None


def test_sampled_d_is_superimposed_by_default_and_etabs_adds_self_weight():
    config = sample_study(ResearchRanges(count=20, seed=3, size_search=True))
    rows = list(plan_rows(config))
    assert config.dead_load_basis == "superimposed_plus_self_weight"
    # Heavier sections carry their own weight on top of D, so no size is ever invalid for self-weight.
    assert all(r["input_valid"] or "self-weight" not in r["input_validation_error"] for r in rows)
    big = next(r for r in rows if (r["b_mm"], r["h_mm"]) == (600, 1200))
    assert big["dead_total_kN_m"] == pytest.approx(big["dead_input_kN_m"] + 24*.6*1.2)
    assert big["dead_super_kN_m"] == pytest.approx(big["dead_input_kN_m"])
    total = sample_study(ResearchRanges(count=20, seed=3, size_search=True, dead_includes_self_weight=True))
    assert total.dead_load_basis == "total_including_self_weight"
    assert [s.dead_kN_m for s in total.scenarios] == [s.dead_kN_m for s in config.scenarios]


def test_default_dead_live_pairs_keep_live_over_dead_between_025_and_15():
    ranges = ResearchRanges(count=500, seed=12)
    assert (ranges.min_live_dead_ratio, ranges.max_live_dead_ratio) == (0.25, 1.5)
    config = sample_study(ranges)
    ratios = [s.live_kN_m[0]/s.dead_kN_m[0] for s in config.scenarios]
    assert 0.25 <= min(ratios) and max(ratios) <= 1.5
    assert min(ratios) < 0.4 and max(ratios) > 1.3  # the whole band is used
    assert all(10 <= s.dead_kN_m[0] <= 150 and 10 <= s.live_kN_m[0] <= 150 for s in config.scenarios)


def test_dead_is_even_and_live_is_even_within_its_band():
    from collections import Counter
    ranges = ResearchRanges(count=1000, seed=2, whole_unit_parameters=True, dead_min=10, dead_max=13, live_min=10, live_max=13,
                            min_live_dead_ratio=0.9, max_live_dead_ratio=1.1, candidate_count=1)
    bands = {10: {10, 11}, 11: {10, 11, 12}, 12: {11, 12, 13}, 13: {12, 13}}  # 0.9D ≤ L ≤ 1.1D on whole numbers
    lines = [(s.dead_kN_m[0], s.live_kN_m[0]) for s in sample_study(ranges).scenarios]
    dead = Counter(d for d, _ in lines)
    assert set(dead) == set(bands) and all(0.8*250 < n < 1.2*250 for n in dead.values())
    for d, band in bands.items():
        live = Counter(l for dd, l in lines if dd == d)
        assert set(live) == band
        assert all(0.6*dead[d]/len(band) < n < 1.4*dead[d]/len(band) for n in live.values())


def test_light_medium_and_heavy_dead_loads_are_equally_common():
    config = sample_study(ResearchRanges(count=900, seed=5))
    dead = [s.dead_kN_m[0] for s in config.scenarios]
    shares = [sum(lo <= d < hi for d in dead)/len(dead) for lo, hi in ((10, 50), (50, 100), (100, 150.001))]
    assert all(abs(share - expected) < 0.06 for share, expected in zip(shares, (40/140, 50/140, 50/140)))


def test_dead_live_pair_count_is_exact():
    ranges = ResearchRanges(whole_unit_parameters=True)
    brute = sum(1 for d in range(10, 151) for l in range(10, 151) if 0.25*d <= l <= 1.5*d)
    counts = factorial_counts(ranges)
    assert counts["dead_live_pairs_allowed"] == brute and counts["dead_live_pairs"] == 141*141
    assert counts["base_scenarios"] == counts["span_arrangements"]*brute*counts["fc"]*counts["fy"]*counts["fyt"]


@pytest.mark.parametrize("low,high,match", [(1.5, 0.25, "exceeds"), (-0.1, 1.5, "outside"), (True, 1.5, "finite"),
                                            (0.25, 25, "outside")])
def test_invalid_live_dead_limits_are_rejected(low, high, match):
    with pytest.raises(ValueError, match=match):
        replace(ResearchRanges(), min_live_dead_ratio=low, max_live_dead_ratio=high).validate()


def test_live_dead_limits_that_leave_no_pair_are_rejected():
    with pytest.raises(ValueError, match="No D and L pair"):
        ResearchRanges(dead_min=100, dead_max=150, live_min=10, live_max=20, min_live_dead_ratio=0.5).validate()


def test_materials_cover_the_full_ranges_and_stirrups_stay_within_the_aci_shear_limit():
    ranges = ResearchRanges(count=1000, seed=21)
    assert (ranges.fc_min, ranges.fc_max, ranges.fy_min, ranges.fy_max, ranges.fyt_min, ranges.fyt_max) == (20, 50, 200, 600, 200, 420)
    assert not ranges.stirrup_matches_fy
    lines = sample_study(ranges).scenarios
    for name, low, high in (("fc_MPa", 20, 50), ("fy_MPa", 200, 600), ("fyt_MPa", 200, 420)):
        values = [getattr(s, name) for s in lines]
        assert low <= min(values) < low+0.05*(high-low) and high-0.05*(high-low) < max(values) <= high
    # The stirrup strength is its own draw, not a copy of fy.
    assert sum(s.fyt_MPa == s.fy_MPa for s in lines) == 0


def test_stirrups_can_copy_fy_but_never_above_420():
    lines = sample_study(ResearchRanges(count=300, seed=4, stirrup_matches_fy=True)).scenarios
    assert all(s.fyt_MPa == min(s.fy_MPa, 420) for s in lines)
    assert any(s.fy_MPa > 420 for s in lines) and max(s.fyt_MPa for s in lines) == 420


@pytest.mark.parametrize("field,value", [("fyt_max", 450), ("fyt_min", 150), ("fc_max", 55), ("fy_max", 650)])
def test_material_ranges_outside_the_study_limits_are_rejected(field, value):
    with pytest.raises(ValueError):
        replace(ResearchRanges(), **{field: value}).validate()


def test_research_studies_accept_fc_50_and_fy_600():
    data = research_data()
    data["scenarios"][0].update(fc_MPa=50, fy_MPa=600, fyt_MPa=420)
    Config.parse(data)


def test_legacy_load_config_keeps_its_original_interpretation():
    data = research_data()
    data.pop("loading_protocol")
    data.pop("dead_load_basis")
    data.pop("enforce_research_ranges")
    s = data["scenarios"][0]
    s["dead_super_kN_m"] = s.pop("dead_kN_m")
    config = Config.parse(data)
    assert config.dead_load_basis == "superimposed_plus_self_weight"
    assert list(plan_rows(config))[0]["dead_total_kN_m"] == pytest.approx(24.32)
