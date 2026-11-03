"""Strict, reproducible configuration; no COM dependency during planning."""
from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path


def number(value, name, minimum=0, maximum=1e9, inclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if (value < minimum if inclusive else value <= minimum) or value > maximum:
        raise ValueError(f"{name} outside permitted range ({minimum}, {maximum}]")
    return float(value)


def width_depth_allowed(b, h, low, high):
    """b/h within [low, high]; a zero bound means that side is unlimited."""
    return (not low or b >= low*h - 1e-9) and (not high or b <= high*h + 1e-9)


def check_width_depth_limits(low, high):
    for name, value in (("min_width_depth_ratio", low), ("max_width_depth_ratio", high)):
        if value != 0:
            number(value, name, 0, 10)
    if low and high and low > high:
        raise ValueError("Minimum width/depth ratio exceeds the maximum")


def check_span_depth_limit(ratio):
    if ratio != 0:
        number(ratio, "min_span_depth_ratio", 0, 30)


def deep_beam(span_mm, h, ratio):
    """True when span ≤ ratio × h; ratio 0 disables the rule."""
    return bool(ratio) and span_mm <= ratio*h + 1e-9


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def search_groups(sections):
    """Candidate depths per width, ascending: the space a smart size search bisects."""
    groups = {}
    for b, h in sections:
        groups.setdefault(b, []).append(h)
    return [(b, sorted(groups[b])) for b in sorted(groups)]


def planned_model_count(scenario_count, sections, size_search=False):
    """Every scenario × section, or the bisection worst case: ⌈log2(n+1)⌉ runs per width."""
    if not size_search:
        return scenario_count*len(sections)
    return scenario_count*sum(len(depths).bit_length() for _, depths in search_groups(sections))


@dataclass(frozen=True)
class Scenario:
    name: str
    spans_m: list[float]
    dead_kN_m: list[float]
    live_kN_m: list[float]
    fc_MPa: float = 30
    fy_MPa: float = 420
    fyt_MPa: float = 420
    centroid_cover_mm: float = 60
    concrete_weight_kN_m3: float = 24
    flexural_modifier: float = 1.0  # EI factor in ETABS; uniform gravity beams give the same forces for any value

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Each scenario must be an object")
        data = dict(data)
        if "dead_super_kN_m" in data:
            if "dead_kN_m" in data:
                raise ValueError("Specify dead_kN_m once; do not also supply the legacy dead_super_kN_m field")
            data["dead_kN_m"] = data.pop("dead_super_kN_m")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown scenario fields: {sorted(unknown)}")
        item = cls(**data)
        if not isinstance(item.name, str) or not item.name.strip():
            raise ValueError("Scenario name is required")
        if not isinstance(item.spans_m, list) or not 1 <= len(item.spans_m) <= 6:
            raise ValueError("Use 1 to 6 spans")
        for name in ("spans_m", "dead_kN_m", "live_kN_m"):
            values = getattr(item, name)
            if not isinstance(values, list) or len(values) != len(item.spans_m):
                raise ValueError(f"{name} must have one value per span")
            for value in values:
                number(value, name, 0, 30 if name == "spans_m" else 1000,
                       inclusive=name != "spans_m")
        # fy up to 690 MPa: ACI 318-19 Table 20.2.2.4(a), flexure, other than special seismic systems.
        for name, lo, hi in (("fc_MPa", 16, 70), ("fy_MPa", 200, 690),
                             ("fyt_MPa", 200, 550), ("centroid_cover_mm", 20, 150),
                             ("concrete_weight_kN_m3", 20, 26), ("flexural_modifier", 0, 1)):
            number(getattr(item, name), name, lo, hi, inclusive=name != "flexural_modifier")
        return item

    @property
    def scenario_id(self):
        legacy = asdict(self)
        legacy["dead_super_kN_m"] = legacy.pop("dead_kN_m")
        return fingerprint(legacy)


@dataclass(frozen=True)
class Serviceability:
    enabled: bool = False
    # Missing fields in old saved studies preserve their original ACI checks.
    limit_mode: str = "aci"
    total_basis: str = "immediate"
    category: str | None = None
    sustained_live_fraction: float | None = None
    duration_months: int | None = None
    attachment_timing: str | None = None
    attachment_tolerance_mm: float | None = None
    dead_limit_divisor: float | None = None
    total_limit_divisor: float | None = None

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data)-set(cls.__dataclass_fields__):
            raise ValueError("Invalid serviceability settings")
        result = cls(**data)
        if type(result.enabled) is not bool:
            raise ValueError("Serviceability enabled must be Boolean")
        if result.limit_mode not in {"aci", "user_defined"}:
            raise ValueError("Deflection limit_mode must be aci or user_defined")
        if result.total_basis not in {"immediate", "long_term"}:
            raise ValueError("Total deflection basis must be immediate or long_term")
        if result.category not in {None, "floor_unattached", "roof_unattached", "beam_sensitive", "beam_nonsensitive"}:
            raise ValueError("Unknown ACI deflection category")
        if result.sustained_live_fraction is not None:
            number(result.sustained_live_fraction, "Sustained live fraction", 0, 1, inclusive=True)
        if result.attachment_timing not in {None, "after_immediate_dead", "before_service_loading"}:
            raise ValueError("Unknown nonstructural attachment timing")
        if result.attachment_tolerance_mm is not None:
            number(result.attachment_tolerance_mm, "Attachment tolerance (mm)", 0, 10000)
        if result.duration_months is not None and (type(result.duration_months) is not int or result.duration_months not in {3, 6, 12, 60}):
            raise ValueError("Use an ACI sustained duration: 3, 6, 12 or 60 months (60 means 5 years or more)")
        for key in ("dead_limit_divisor", "total_limit_divisor"):
            if getattr(result, key) is not None:
                number(getattr(result, key), key, 1, 10000, inclusive=True)
        return result

    def require_ready(self):
        if not self.enabled:
            return
        if self.total_basis == "long_term" and (self.sustained_live_fraction is None or self.duration_months is None):
            raise ValueError("Long-term total deflection requires a sustained live-load percentage and load duration")
        if self.limit_mode == "user_defined":
            if self.dead_limit_divisor is None or self.total_limit_divisor is None:
                raise ValueError("Enter both DL and Total deflection limits as span/divisor")
            return
        if self.category is None:
            raise ValueError("Choose what the beam supports under ACI serviceability settings")
        if self.category in {"beam_sensitive", "beam_nonsensitive"} and (self.sustained_live_fraction is None or self.duration_months is None):
            raise ValueError("Enter the sustained live-load fraction and choose an ACI load duration; ACI does not prescribe the project's sustained live fraction")
        if self.category in {"beam_sensitive", "beam_nonsensitive"} and self.attachment_timing is None:
            raise ValueError("Choose whether nonstructural elements are attached before service loading or after immediate dead deflection")
        if self.category == "beam_nonsensitive" and self.attachment_tolerance_mm is None:
            raise ValueError("Enter the nonstructural attachment tolerance in mm; ACI Table 24.2.2 does not permit the limit to exceed that tolerance")


@dataclass(frozen=True)
class Config:
    scenarios: list[Scenario]
    sections_mm: list[list[float]]
    stations_per_span: int = 21
    max_models: int = 100
    etabs_executable: str = r"C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe"
    check_combinations: bool = True
    loading_protocol: str = "patterned_gravity"
    dead_load_basis: str = "superimposed_plus_self_weight"
    enforce_research_ranges: bool = False
    # Section shape rule b/h; 0 = unlimited. Older studies and the frozen pilot keep 0.
    min_width_depth_ratio: float = 0.
    max_width_depth_ratio: float = 0.
    # A span must exceed this × h; 4 excludes ACI 318-19 §9.9.1.1 deep beams (span ≤ 4h). 0 = off.
    min_span_depth_ratio: float = 0.
    # Smart size search: per scenario and width, bisect the listed depths for the smallest passing one
    # instead of running every section. Assumes a deeper beam of the same width never fails where a shallower one passes.
    size_search: bool = False
    sampling_settings: dict | None = None
    serviceability: Serviceability = field(default_factory=Serviceability)

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Configuration must be an object")
        data = dict(data)
        # Migrate old studies: manual bar settings never enter the new workflow.
        data.pop("provided_reinforcement", None)
        if set(data) - set(cls.__dataclass_fields__):
            raise ValueError(f"Unknown config fields: {set(data) - set(cls.__dataclass_fields__)}")
        args = dict(data)
        args["scenarios"] = [Scenario.parse(s) for s in args.get("scenarios", [])]
        args["serviceability"] = Serviceability.parse(args.get("serviceability", {}))
        result = cls(**args)
        if result.loading_protocol not in {"research_full_udl", "uniform_gravity", "patterned_gravity"}:
            raise ValueError("Unknown loading_protocol")
        if result.dead_load_basis not in {"total_including_self_weight", "superimposed_plus_self_weight"}:
            raise ValueError("Unknown dead_load_basis")
        if type(result.enforce_research_ranges) is not bool:
            raise ValueError("enforce_research_ranges must be Boolean")
        if result.sampling_settings is not None and not isinstance(result.sampling_settings, dict):
            raise ValueError("sampling_settings must be an object")
        for scenario in result.scenarios:
            if result.loading_protocol != "patterned_gravity":
                if any(abs(v-scenario.dead_kN_m[0]) > 1e-9 for v in scenario.dead_kN_m) or any(abs(v-scenario.live_kN_m[0]) > 1e-9 for v in scenario.live_kN_m):
                    raise ValueError("Uniform-UDL mode requires the same D and the same L on every span")
            if result.enforce_research_ranges:
                for name, values, low, high in (("Span (m)", scenario.spans_m, 3, 12),
                                                ("D (kN/m)", scenario.dead_kN_m, 10, 150),
                                                ("L (kN/m)", scenario.live_kN_m, 10, 150),
                                                ("fc (MPa)", [scenario.fc_MPa], 20, 50),
                                                ("fy (MPa)", [scenario.fy_MPa], 200, 600)):
                    for value in values:
                        number(value, name, low, high, inclusive=True)
        if type(result.check_combinations) is not bool:
            raise ValueError("check_combinations must be Boolean")
        if type(result.size_search) is not bool:
            raise ValueError("size_search must be Boolean")
        if not result.scenarios or len({s.scenario_id for s in result.scenarios}) != len(result.scenarios):
            raise ValueError("Provide at least one scenario; duplicate scenarios are not allowed")
        low, high = result.min_width_depth_ratio, result.max_width_depth_ratio
        check_width_depth_limits(low, high)
        check_span_depth_limit(result.min_span_depth_ratio)
        if not isinstance(result.sections_mm, list) or not result.sections_mm:
            raise ValueError("sections_mm must contain [width, depth] candidate pairs")
        seen = set()
        for section in result.sections_mm:
            if not isinstance(section, list) or len(section) != 2:
                raise ValueError("Each section must be [width_mm, depth_mm]")
            b, h = [number(v, "section dimension", 100, 3000) for v in section]
            if result.enforce_research_ranges and not (300 <= b <= 900 and 350 <= h <= 1200):
                raise ValueError("Research sections require b = 300–900 mm and h = 350–1200 mm")
            if not width_depth_allowed(b, h, low, high):
                times = lambda r: "h" if r == 1 else f"{r:g}h"
                rule = " and ".join(([f"b ≥ {times(low)}"] if low else []) + ([f"b ≤ {times(high)}"] if high else []))
                raise ValueError(f"Section {b:g} × {h:g} mm has b/h = {b/h:.3f}; the study requires {rule}")
            if h <= 2 * max(s.centroid_cover_mm for s in result.scenarios):
                raise ValueError("Depth must exceed twice the reinforcement centroid cover")
            if (b, h) in seen:
                raise ValueError("Duplicate section")
            seen.add((b, h))
        for key, lo, hi in (("stations_per_span", 5, 501), ("max_models", 1, 10000)):
            value = getattr(result, key)
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{key} must be an integer between {lo} and {hi}")
        if result.stations_per_span % 2 == 0:
            raise ValueError("stations_per_span must be odd so the midspan is sampled")
        if result.planned_models() > result.max_models:
            raise ValueError("Requested candidate count exceeds max_models")
        if not isinstance(result.etabs_executable, str) or not result.etabs_executable.lower().endswith("etabs.exe"):
            raise ValueError("etabs_executable must point to ETABS.exe")
        return result

    @classmethod
    def load(cls, path):
        return cls.parse(json.loads(Path(path).read_text(encoding="utf-8-sig")))

    def to_dict(self):
        return asdict(self)

    def candidate_id(self, scenario, section):
        identity = {"scenario": scenario.scenario_id, "section": list(section)}
        if self.loading_protocol != "patterned_gravity" or self.dead_load_basis != "superimposed_plus_self_weight":
            identity.update(loading_protocol=self.loading_protocol, dead_load_basis=self.dead_load_basis)
        return fingerprint(identity)

    def candidates(self):
        for scenario, section in itertools.product(self.scenarios, self.sections_mm):
            yield scenario, section[0], section[1], self.candidate_id(scenario, section)

    def search_groups(self):
        return search_groups(self.sections_mm)

    def planned_models(self):
        """Exact model count, or the upper bound when the smart size search is on."""
        return planned_model_count(len(self.scenarios), self.sections_mm, self.size_search)


def patterns(n):
    return list(itertools.product((0, 1), repeat=n))


def strength_combinations(number_spans, protocol):
    if protocol != "patterned_gravity":
        combos = {"U_1p2D_1p6L_FULL": {"LAB_DEAD": 1.2, "LAB_LIVE": 1.6}}
        if protocol == "uniform_gravity":
            combos["U_1p4D"] = {"LAB_DEAD": 1.4}
        return combos
    combos = {"U_1p4D": {"LAB_DEAD": 1.4}}
    for pattern in patterns(number_spans):
        terms = {"LAB_DEAD": 1.2}
        terms.update({f"LL_{i+1}": 1.6 for i, on in enumerate(pattern) if on})
        combos["U_1p2D_1p6L_"+"".join(map(str, pattern))] = terms
    return combos


def plan_rows(config):
    for scenario, b, h, candidate_id in config.candidates():
        yield from candidate_rows(config, scenario, b, h, candidate_id)


def candidate_rows(config, scenario, b, h, candidate_id):
    """One planned row per span for a single scenario × section model."""
    for i, length in enumerate(scenario.spans_m):
        self_weight = scenario.concrete_weight_kN_m3 * b * h / 1e6
        dead = scenario.dead_kN_m[i] if config.dead_load_basis == "total_including_self_weight" else scenario.dead_kN_m[i]+self_weight
        errors = []
        if dead < self_weight-1e-9:
            errors.append("Entered total D is less than the beam self-weight")
        ratio = config.min_span_depth_ratio
        if deep_beam(length*1000, h, ratio):
            errors.append(f"Deep beam: span {length*1000:g} mm ≤ {ratio:g}h = {ratio*h:g} mm "
                          "(ACI 318-19 §9.9.1.1 deep beams need strut-and-tie design)")
        valid = not errors
        wu = 1.2*dead+1.6*scenario.live_kN_m[i]
        yield dict(scenario_id=scenario.scenario_id, scenario_name=scenario.name,
                   candidate_id=candidate_id, beam_id=f"B{i + 1}", span_index=i,
                   span_length_mm=length * 1000, span_type="single" if len(scenario.spans_m) == 1 else
                   ("end_left" if i == 0 else "end_right" if i == len(scenario.spans_m)-1 else "interior"),
                   left_span_ratio=scenario.spans_m[i-1]/length if i else 0,
                   right_span_ratio=scenario.spans_m[i+1]/length if i+1 < len(scenario.spans_m) else 0,
                   number_spans=len(scenario.spans_m), b_mm=b, h_mm=h,
                   **{f"span_{j+1}_mm": scenario.spans_m[j]*1000 if j < len(scenario.spans_m) else 0. for j in range(6)},
                   fc_MPa=scenario.fc_MPa, fy_MPa=scenario.fy_MPa, fyt_MPa=scenario.fyt_MPa,
                   centroid_cover_mm=scenario.centroid_cover_mm, flexural_modifier=scenario.flexural_modifier,
                   concrete_weight_kN_m3=scenario.concrete_weight_kN_m3,
                   dead_input_kN_m=scenario.dead_kN_m[i], dead_load_basis=config.dead_load_basis,
                   loading_protocol=config.loading_protocol, input_valid=int(valid),
                   input_validation_error="; ".join(errors),
                   dead_super_kN_m=dead-self_weight, self_weight_kN_m=self_weight,
                   dead_total_kN_m=dead, live_kN_m=scenario.live_kN_m[i],
                   Wu_kN_m=wu,
                   max_applied_gravity_factored_UDL_kN_m=wu if config.loading_protocol == "research_full_udl" else max(1.4*dead, wu))
