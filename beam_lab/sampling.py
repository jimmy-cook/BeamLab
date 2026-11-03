"""Seeded research inputs. One sampled D and one L apply to every span in a line."""
from dataclasses import asdict, dataclass
import itertools
import random
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from fractions import Fraction

from .config import (Config, number, width_depth_allowed, check_width_depth_limits, check_span_depth_limit,
                     planned_model_count)


@dataclass
class ResearchRanges:
    count: int = 10
    seed: int = 42
    spans_min: int = 3
    spans_max: int = 3
    span_min_mm: int = 3000
    span_max_mm: int = 12000
    dead_min: float = 10
    dead_max: float = 150
    live_min: float = 10
    live_max: float = 150
    fc_min: float = 20
    fc_max: float = 50
    fy_min: float = 200
    fy_max: float = 600
    # Stirrups get their own strength. ACI 318-19 Table 20.2.2.4(a) caps fyt for shear at 420 MPa (60,000 psi).
    fyt_min: float = 200
    fyt_max: float = 420
    width_min_mm: int = 300
    width_max_mm: int = 900
    depth_min_mm: int = 350
    depth_max_mm: int = 1200
    section_step_mm: int = 25
    span_step_mm: int = 250
    max_adjacent_span_ratio: float = 0.  # longer/shorter neighbouring span; 0 = no limit
    # Realistic load pairs: D is drawn first, then L within the band where L/D lies within these limits; 0 = no limit.
    min_live_dead_ratio: float = 0.25
    max_live_dead_ratio: float = 1.5
    min_width_depth_ratio: float = 0.3  # b ≥ 0.3h; 0 = no minimum
    max_width_depth_ratio: float = 1.   # b ≤ h; 0 = no maximum
    min_span_depth_ratio: float = 4.    # span > 4h excludes ACI deep beams before ETABS; 0 = off
    candidate_count: int = 6
    stirrup_matches_fy: bool = False  # True: fyt = min(fy, 420) instead of its own range
    equal_spans: bool = True
    whole_unit_parameters: bool = False
    # Smart size search: every grid depth of these widths becomes the search space; no random sizes are drawn.
    size_search: bool = False
    search_widths_mm: tuple = (300, 400, 500, 600)
    # False: sampled D is superimposed dead load and ETABS adds beam self-weight (realistic for sizing).
    # True: sampled D is the total including self-weight; ETABS self-weight is off.
    dead_includes_self_weight: bool = False

    def validate(self):
        for key, low, high in (("count", 1, 1000), ("seed", 0, 2147483647), ("spans_min", 1, 6), ("spans_max", 1, 6),
                               ("span_min_mm", 3000, 12000), ("span_max_mm", 3000, 12000),
                               ("width_min_mm", 300, 900), ("width_max_mm", 300, 900),
                               ("depth_min_mm", 350, 1200), ("depth_max_mm", 350, 1200),
                               ("section_step_mm", 1, 850), ("span_step_mm", 1, 9000), ("candidate_count", 1, 10000)):
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key} must be an integer in {low}–{high}")
        for prefix, low, high in (("dead", 10, 150), ("live", 10, 150), ("fc", 20, 50), ("fy", 200, 600),
                                  ("fyt", 200, ACI_SHEAR_FYT_MAX)):
            number(getattr(self, prefix+"_min"), prefix, low, high, inclusive=True)
            number(getattr(self, prefix+"_max"), prefix, low, high, inclusive=True)
        ratio = self.max_adjacent_span_ratio
        if ratio != 0:
            number(ratio, "max_adjacent_span_ratio", 1, 4, inclusive=True)
        check_width_depth_limits(self.min_width_depth_ratio, self.max_width_depth_ratio)
        check_span_depth_limit(self.min_span_depth_ratio)
        for name in ("min_live_dead_ratio", "max_live_dead_ratio"):
            if getattr(self, name) != 0:
                number(getattr(self, name), name, 0, 20)
        if self.min_live_dead_ratio and self.max_live_dead_ratio and self.min_live_dead_ratio > self.max_live_dead_ratio:
            raise ValueError("Minimum live/dead ratio exceeds the maximum")
        for prefix in ("spans", "dead", "live", "fc", "fy", "fyt"):
            if getattr(self, prefix+"_min") > getattr(self, prefix+"_max"):
                raise ValueError(f"Minimum {prefix} exceeds its maximum")
        for prefix in ("span", "width", "depth"):
            if getattr(self, prefix+"_min_mm") > getattr(self, prefix+"_max_mm"):
                raise ValueError(f"Minimum {prefix} exceeds its maximum")
        for key in ("stirrup_matches_fy", "equal_spans", "whole_unit_parameters", "size_search", "dead_includes_self_weight"):
            if type(getattr(self,key)) is not bool:
                raise ValueError(f"{key} must be Boolean")
        if self.whole_unit_parameters:
            for prefix in ("dead", "live", "fc", "fy", "fyt"):
                if math.ceil(getattr(self,prefix+"_min")) > math.floor(getattr(self,prefix+"_max")):
                    raise ValueError(f"{prefix} range contains no whole-unit level")
        if not load_pairs(self)[0]:
            raise ValueError("No D and L pair in the load ranges satisfies the live/dead ratio limits")
        if self.size_search:
            models = planned_model_count(self.count, search_sections(self), True)
            if models > 10000:
                raise ValueError(f"Study exceeds 10,000 candidate models: the size search may need up to {models:,}")
        elif self.count*self.candidate_count > 10000:
            raise ValueError("Study exceeds 10,000 candidate models")
        return self


ACI_SHEAR_FYT_MAX = 420  # MPa; ACI 318-19 Table 20.2.2.4(a), shear: stirrups, ties, hoops of deformed bars


def load_scale(ranges):
    """Sampling resolution: D, L, fc and fy are drawn as integers of 1/scale."""
    return 1 if ranges.whole_unit_parameters else 1000


def load_levels(ranges, prefix):
    scale = load_scale(ranges)
    low = int((Decimal(str(getattr(ranges, prefix+"_min")))*scale).to_integral_value(rounding=ROUND_CEILING))
    high = int((Decimal(str(getattr(ranges, prefix+"_max")))*scale).to_integral_value(rounding=ROUND_FLOOR))
    return low, high


def live_dead_bounds(ranges):
    """Function giving the scaled live-load levels allowed for one scaled dead level (exact rational comparison)."""
    low, high = load_levels(ranges, "live")
    a, b = Fraction(Decimal(str(ranges.min_live_dead_ratio))), Fraction(Decimal(str(ranges.max_live_dead_ratio)))
    def bounds(dead):
        return (max(low, -(-a.numerator*dead//a.denominator)) if a else low,
                min(high, b.numerator*dead//b.denominator) if b else high)
    return bounds


def load_pairs(ranges):
    """(allowed, total) D–L level pairs at the sampling resolution."""
    d_low, d_high = load_levels(ranges, "dead")
    l_low, l_high = load_levels(ranges, "live")
    bounds = live_dead_bounds(ranges)
    allowed = sum(max(0, high-low+1) for low, high in map(bounds, range(d_low, d_high+1)))
    return allowed, max(0, d_high-d_low+1)*max(0, l_high-l_low+1)


def span_grid(ranges):
    """Span lengths in mm on the chosen step, endpoints included (like the section grid)."""
    return sorted(set([*range(ranges.span_min_mm, ranges.span_max_mm+1, ranges.span_step_mm), ranges.span_max_mm]))


def neighbour_windows(grid, ratio):
    """For each grid index, the contiguous index window of spans allowed beside it.

    longer/shorter <= ratio; ratio 0 means no limit. Decimal keeps the comparison exact.
    """
    if not ratio:
        return [(0, len(grid))]*len(grid)
    r = Decimal(str(ratio))
    windows, lo, hi = [], 0, 0
    for v in grid:
        while grid[lo]*r < v:
            lo += 1
        while hi < len(grid) and grid[hi] <= v*r:
            hi += 1
        windows.append((lo, hi))
    return windows


def completions(windows, spans):
    """counts[k][i] = number of valid k-span lines starting at grid index i."""
    counts = [None, [1]*len(windows)]
    for _ in range(2, spans+1):
        prefix = [0, *itertools.accumulate(counts[-1])]
        counts.append([prefix[hi]-prefix[lo] for lo, hi in windows])
    return counts


def weighted_index(rng, indices, weights):
    pick = rng.randrange(sum(weights))
    for index, weight in zip(indices, weights):
        if pick < weight:
            return index
        pick -= weight


def sample_spans(rng, ranges, grid, windows, counts, n):
    if ranges.equal_spans:
        return [grid[rng.randrange(len(grid))]/1000]*n
    if not ranges.max_adjacent_span_ratio:
        return [grid[rng.randrange(len(grid))]/1000 for _ in range(n)]
    # Uniform over every allowed arrangement: weight each choice by its valid completions.
    index = weighted_index(rng, range(len(grid)), counts[n])
    chosen = [index]
    for k in range(n-1, 0, -1):
        lo, hi = windows[index]
        index = weighted_index(rng, range(lo, hi), counts[k][lo:hi])
        chosen.append(index)
    return [grid[i]/1000 for i in chosen]


def section_pairs(ranges):
    """Every b × h on the section grid (endpoints included) that satisfies the b/h limits."""
    def grid(low, high):
        return sorted(set([*range(low, high+1, ranges.section_step_mm), high]))
    return [(b, h) for b, h in itertools.product(grid(ranges.width_min_mm, ranges.width_max_mm),
                                                 grid(ranges.depth_min_mm, ranges.depth_max_mm))
            if width_depth_allowed(b, h, ranges.min_width_depth_ratio, ranges.max_width_depth_ratio)]


def search_sections(ranges):
    """Every allowed depth of each search width: the space the smart size search bisects."""
    widths = ranges.search_widths_mm
    if (not isinstance(widths, (list, tuple)) or not widths or len(set(widths)) != len(widths)
            or any(type(w) is not int for w in widths)):
        raise ValueError("Search widths must be distinct whole millimetres")
    pairs = section_pairs(ranges)
    for width in widths:
        if not any(b == width for b, _ in pairs):
            raise ValueError(f"Search width {width} mm is not on the section grid or has no depth allowed by the b/h limits")
    return [(b, h) for b, h in pairs if b in widths]


def sample_study(ranges, template=None):
    ranges.validate()
    template = template or {}
    rng = random.Random(ranges.seed)
    available = section_pairs(ranges)
    if not available:
        raise ValueError("No section pair in the grid satisfies the width/depth ratio limits")
    if ranges.size_search:
        chosen = search_sections(ranges)
    else:
        if ranges.candidate_count > len(available):
            raise ValueError(f"Candidate count exceeds the {len(available)} section pairs allowed by the grid and b/h limits")
        chosen = [available[0]]
        if ranges.candidate_count > 1 and available[-1] != available[0]:
            chosen.append(available[-1])
        remaining = [s for s in available if s not in chosen]
        chosen.extend(rng.sample(remaining, ranges.candidate_count-len(chosen)))
    scale = load_scale(ranges)
    def sample_level(prefix):
        return rng.randint(*load_levels(ranges, prefix))
    def sample_value(prefix):
        return sample_level(prefix)/scale
    bounds = live_dead_bounds(ranges)
    def sample_loads():
        # D evenly over its range, then L evenly within the band the L/D limits allow for that D,
        # so light and heavy dead loads are equally represented. Without limits this is the 0.7.0 draw order.
        for _ in range(1000000):
            d = sample_level("dead")
            low, high = bounds(d)
            if low <= high:
                return d/scale, rng.randint(low, high)/scale
        raise ValueError("The live/dead ratio limits leave too few D and L pairs to sample")
    spans = span_grid(ranges)
    windows = neighbour_windows(spans, ranges.max_adjacent_span_ratio)
    counts = completions(windows, ranges.spans_max)
    scenarios = []
    for i in range(ranges.count):
        n = rng.randint(ranges.spans_min, ranges.spans_max)
        d,live=sample_loads()
        fy=sample_value("fy")
        lengths = sample_spans(rng, ranges, spans, windows, counts, n)
        fc = sample_value("fc")
        fyt = min(fy, ACI_SHEAR_FYT_MAX) if ranges.stirrup_matches_fy else sample_value("fyt")
        scenarios.append(dict(name=f"Research {ranges.seed}-{i+1:04d}",
                              spans_m=lengths,
                              dead_kN_m=[d]*n, live_kN_m=[live]*n,
                              fc_MPa=fc, fy_MPa=fy, fyt_MPa=fyt,
                              centroid_cover_mm=template.get("centroid_cover_mm", 60),
                              concrete_weight_kN_m3=template.get("concrete_weight_kN_m3", 24),
                              flexural_modifier=template.get("flexural_modifier", 1.)))
    sections = [list(s) for s in chosen]
    return Config.parse(dict(scenarios=scenarios, sections_mm=sections,
                             max_models=planned_model_count(ranges.count, sections, ranges.size_search),
                             loading_protocol="research_full_udl",
                             dead_load_basis="total_including_self_weight" if ranges.dead_includes_self_weight else "superimposed_plus_self_weight",
                             min_width_depth_ratio=ranges.min_width_depth_ratio, max_width_depth_ratio=ranges.max_width_depth_ratio,
                             min_span_depth_ratio=ranges.min_span_depth_ratio, size_search=ranges.size_search,
                             enforce_research_ranges=True, sampling_settings=asdict(ranges)))


def factorial_counts(ranges):
    """Count the exhaustive quantized population; never enumerate its models."""
    ranges.validate()
    def levels(low, high, step):
        a,b,q=Decimal(str(low)),Decimal(str(high)),Decimal(str(step))
        return max(0,int((b/q).to_integral_value(rounding=ROUND_FLOOR))-
                   int((a/q).to_integral_value(rounding=ROUND_CEILING))+1)
    q=1 if ranges.whole_unit_parameters else .001
    counts={prefix:levels(getattr(ranges,prefix+"_min"),getattr(ranges,prefix+"_max"),q) for prefix in ("dead","live","fc","fy","fyt")}
    if ranges.stirrup_matches_fy:
        counts["fyt"]=1  # follows fy
    spans=span_grid(ranges)
    span_levels=len(spans)
    arrangements=completions(neighbour_windows(spans,ranges.max_adjacent_span_ratio),ranges.spans_max)
    def section_levels(low, high):
        return (high-low)//ranges.section_step_mm+1+int((high-low)%ranges.section_step_mm!=0)
    widths=section_levels(ranges.width_min_mm,ranges.width_max_mm)
    depths=section_levels(ranges.depth_min_mm,ranges.depth_max_mm)
    pairs=len(section_pairs(ranges))
    allowed_pairs,all_pairs=load_pairs(ranges)
    load_material=allowed_pairs*counts["fc"]*counts["fy"]*counts["fyt"]
    patterns=lambda n:span_levels if ranges.equal_spans else sum(arrangements[n])
    span_arrangements=sum(patterns(n) for n in range(ranges.spans_min,ranges.spans_max+1))
    scenarios=span_arrangements*load_material
    beamrows=sum(n*patterns(n) for n in range(ranges.spans_min,ranges.spans_max+1))*load_material*pairs
    return dict(span_length_levels=span_levels,span_arrangements=span_arrangements,**counts,width_levels=widths,depth_levels=depths,
                grid_section_pairs=widths*depths,section_pairs=pairs,base_scenarios=scenarios,candidate_models=scenarios*pairs,
                beam_candidate_rows=beamrows,load_material_step=q,dead_live_pairs=all_pairs,dead_live_pairs_allowed=allowed_pairs,
                passing_rows="Unknown until analysis, design and deflection checks complete")
