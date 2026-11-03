"""The built-in plan must keep matching the supplied Excel inputs."""
import hashlib
import json

from beam_lab.config import plan_rows
from beam_lab.dataset import split_for
from beam_lab.pilot import load_excel_pilot


def test_frozen_pilot_matches_original_excel_plan_and_exclusions():
    config = load_excel_pilot()
    rows = list(plan_rows(config))
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == "f5eba34cd28b4389c1764be80f8abbdd6f4568f8d68a737be2ff3535816d6b54"
    assert len(config.scenarios) == 100 and len(config.sections_mm) == 20
    assert len(rows) == 6000 and len({r["candidate_id"] for r in rows}) == 2000
    assert len({r["candidate_id"] for r in rows if not r["input_valid"]}) == 38
    assert sum(not r["input_valid"] for r in rows) == 114
    assert all(len(s.spans_m) == 3 and len(set(s.spans_m)) == 1 for s in config.scenarios)
    assert {split: sum(split_for(s.scenario_id) == split for s in config.scenarios)
            for split in ("train", "validation", "test")} == {"train": 71, "validation": 19, "test": 10}


def test_pilot_has_ready_simple_service_checks_and_automatic_reinforcement_only():
    config = load_excel_pilot()
    config.serviceability.require_ready()
    settings = config.serviceability
    assert settings.enabled and settings.limit_mode == "user_defined" and settings.total_basis == "immediate"
    assert settings.dead_limit_divisor == 120 and settings.total_limit_divisor == 240
    assert config.check_combinations and config.loading_protocol == "research_full_udl"
    assert config.dead_load_basis == "total_including_self_weight"
    assert "provided_reinforcement" not in config.to_dict()


def test_editing_loaded_study_does_not_mutate_packaged_pilot():
    edited = load_excel_pilot()
    edited.sections_mm[0][0] = 325
    edited.scenarios.clear()
    edited.sampling_settings["seed"] = 0
    original = load_excel_pilot()
    assert original.sections_mm[0] == [300, 350]
    assert len(original.scenarios) == 100 and original.sampling_settings["seed"] == 42


def test_reviewing_pilot_form_keeps_every_original_scenario_identifier():
    # Exercise the form conversion without starting Tk or an ETABS session.
    from types import SimpleNamespace
    from beam_lab.config import Scenario
    from beam_lab.gui import BeamLab

    class Value:
        def __init__(self, value): self.value = str(value)
        def get(self): return self.value

    config = load_excel_pilot()
    scenarios = config.to_dict()["scenarios"]
    for index, saved in enumerate(scenarios):
        form = SimpleNamespace(scenarios=scenarios, selected_scenario=index,
            editor={key: Value(saved[key]) for key in ("name", "fc_MPa", "fy_MPa", "fyt_MPa",
                                                       "centroid_cover_mm", "flexural_modifier")},
            span_vars=[[Value(f"{saved[key][i]:g}") for key in ("spans_m", "dead_kN_m", "live_kN_m")]
                       for i in range(3)] + [[Value("") for _ in range(3)] for _ in range(3)])
        reviewed = Scenario.parse(BeamLab.editor_scenario(form))
        assert reviewed.scenario_id == config.scenarios[index].scenario_id
