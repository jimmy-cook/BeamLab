from datetime import datetime

from beam_lab.config import Config
from beam_lab.gui import run_folder_name
from beam_lab.sampling import ResearchRanges, sample_study

NOW = datetime(2026, 11, 4, 9, 30, 15)


def study(*lines, sampled=False):
    scenarios = [dict(name=f"Line {i}", spans_m=spans, dead_kN_m=dead, live_kN_m=live) for i, (spans, dead, live) in enumerate(lines)]
    return Config.parse(dict(scenarios=scenarios, sections_mm=[[300, 600]], max_models=len(scenarios),
                             sampling_settings={"seed": 42} if sampled else None))


def test_typed_name_with_detected_span_and_load_type():
    config = study(([6, 6, 6], [30]*3, [20]*3))
    assert run_folder_name(config, "Phase1", NOW) == "Phase1 EqualSpan UniformLoad 04Nov 0930"


def test_seed_is_used_when_no_name_is_typed():
    config = sample_study(ResearchRanges(count=3, seed=43, equal_spans=False, max_adjacent_span_ratio=1.5))
    assert run_folder_name(config, "  ", NOW) == "seed43 UnequalSpan UniformLoad 04Nov 0930"


def test_unequal_sampling_is_named_unequal_even_if_some_lines_came_out_equal():
    config = sample_study(ResearchRanges(count=476, seed=43, equal_spans=False, max_adjacent_span_ratio=1.5, size_search=True))
    assert any(len(set(s.spans_m)) == 1 for s in config.scenarios)
    assert run_folder_name(config, "Phase2", NOW) == "Phase2 UnequalSpan UniformLoad 04Nov 0930"


def test_mixed_spans_varied_loads_and_unsafe_characters():
    config = study(([6, 6, 6], [30]*3, [20]*3), ([5, 7, 6], [30, 40, 30], [20]*3))
    assert run_folder_name(config, 'Verify: A/B?', NOW) == "Verify AB MixedSpan VariedLoad 04Nov 0930"
    assert run_folder_name(config, "", NOW).startswith("study ")
