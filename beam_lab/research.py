"""Ready-made research runs and run-folder helpers shared by the desktop and web screens (no UI code here)."""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import Config, planned_model_count
from .sampling import ResearchRanges, sample_study

DEFAULT_ETABS = r"C:\Program Files\Computers and Structures\ETABS 22\ETABS.exe"


def installed_etabs():
    """{version: ETABS.exe path} for the supported versions found in the standard install folder."""
    paths = {v: Path(DEFAULT_ETABS.replace("ETABS 22", f"ETABS {v}")) for v in ("22", "23")}
    return {v: str(p) for v, p in paths.items() if p.is_file()}
# Deflection checks of the desktop form's defaults: user-defined DL L/120 and total L/240, immediate.
DEFAULT_SERVICEABILITY = dict(enabled=True, limit_mode="user_defined", total_basis="immediate",
                              dead_limit_divisor=120., total_limit_divisor=240.)
# Gravity-only, first-order analysis of a uniform continuous beam: a common EI factor does not change moments or design,
# and deflection uses its own cracked-section Ie, so no stiffness modifier is applied (1.0).
TEMPLATE = dict(fyt_MPa=420, centroid_cover_mm=60, concrete_weight_kN_m3=24, flexural_modifier=1.0)
VERIFY_STUDY = "examples/verify_size_search_study.json"

PRESETS = {
    "smoke": dict(title="Smoke test", note="2 beam lines, about 40 ETABS models. Checks that ETABS runs and how long one model takes.",
                  lines=2, seed=1, equal_spans=True, ratio=0., name="Smoke"),
    "verify_a": dict(title="Verification A: smart search", note="The saved 10-line test study, searched (at most 110 models).",
                     study=VERIFY_STUDY, size_search=True, name="VerifyA"),
    "verify_b": dict(title="Verification B: every size", note="The same 10 lines, every listed size (562 models). Compare with A.",
                     study=VERIFY_STUDY, size_search=False, name="VerifyB"),
    "phase1": dict(title="Phase 1: equal spans", note="Three equal spans per line, smart search, seed 42.",
                   lines=476, seed=42, equal_spans=True, ratio=0., name="Phase1"),
    "phase2": dict(title="Phase 2: unequal spans", note="Neighbouring span ratio ≤ 1.5, smart search, seed 43.",
                   lines=476, seed=43, equal_spans=False, ratio=1.5, name="Phase2"),
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def application_dir():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def build_study(key, lines=None, seed=None, whole_numbers=True, etabs_executable=DEFAULT_ETABS, root=None,
                equal_spans=None, ratio=None):
    """The Config a preset runs: sampled with the agreed defaults, or the saved verification study.

    equal_spans and ratio (longer/shorter neighbouring span, 0 = no limit) override the preset's span type."""
    preset = PRESETS[key]
    if "study" in preset:
        data = json.loads((Path(root or application_dir())/preset["study"]).read_text(encoding="utf-8"))
        data.update(size_search=preset["size_search"],
                    max_models=planned_model_count(len(data["scenarios"]), data["sections_mm"], preset["size_search"]))
    else:
        if type(lines) is not int or type(seed) is not int:
            raise ValueError("Number of beam lines and seed must be whole numbers")
        equal = preset["equal_spans"] if equal_spans is None else bool(equal_spans)
        limit = 0. if equal else float(preset["ratio"] if ratio is None else ratio)
        ranges = ResearchRanges(count=lines, seed=seed, size_search=True, equal_spans=equal,
                                max_adjacent_span_ratio=limit, whole_unit_parameters=whole_numbers)
        data = sample_study(ranges, TEMPLATE).to_dict()
        data["serviceability"] = DEFAULT_SERVICEABILITY
    data["etabs_executable"] = etabs_executable
    return Config.parse(data)


def settings_summary(config):
    """Plain-language lines describing what a study will run."""
    s = config.sampling_settings or {}
    step = "1 kN/m and 1 MPa steps" if s.get("whole_unit_parameters") else "continuous values"
    equal = all(len(set(x.spans_m)) == 1 for x in config.scenarios)
    widths = ", ".join(f"{b:g}" for b, _ in config.search_groups())
    sls = config.serviceability
    basis = "superimposed, ETABS adds self-weight" if config.dead_load_basis == "superimposed_plus_self_weight" else "total incl. self-weight"
    lines = [("Beam lines", f"{len(config.scenarios)} · 3 spans · " + ("equal spans" if equal else
              f"unequal spans (neighbour ratio ≤ {s.get('max_adjacent_span_ratio') or '–'})") +
              (f" · seed {s['seed']}" if "seed" in s else ""))]
    if s:
        lines += [("Loads", f"D {s['dead_min']:g}–{s['dead_max']:g} kN/m ({basis}); L {s['live_min']:g}–{s['live_max']:g} kN/m with "
                            f"{s['min_live_dead_ratio']:g} ≤ L/D ≤ {s['max_live_dead_ratio']:g}; same on all spans; {step}"),
                  ("Materials", f"f′c {s['fc_min']:g}–{s['fc_max']:g} MPa · fy {s['fy_min']:g}–{s['fy_max']:g} MPa · "
                                f"stirrups fyt {s.get('fyt_min', 200):g}–{s.get('fyt_max', 420):g} MPa")]
    lines += [("Sizes", ("smart search, widths " + widths if config.size_search else f"every listed size ({len(config.sections_mm)})") +
               " · 0.3h ≤ b ≤ h · span > 4h"),
              ("Checks", "ACI 318-19 strength (1.2D + 1.6L, all spans loaded)" +
               (f" + deflection L/{sls.dead_limit_divisor:g} (dead), L/{sls.total_limit_divisor:g} (total)" if sls.enabled else ", deflection off")),
              ("ETABS models", f"at most {config.planned_models():,}")]
    return lines


def run_folder_name(config, run_name="", now=None):
    """Short readable run folder: name (or seed), span type, load type, date and time, e.g. 'Phase1 EqualSpan UniformLoad 04Nov 0930'."""
    now = now or datetime.now()
    label = " ".join("".join(c for c in run_name if c not in '\\/:*?"<>|').split())[:40].rstrip(". ")
    if not label:
        seed = (config.sampling_settings or {}).get("seed")
        label = f"seed{seed}" if seed is not None else "study"
    equal = [len(set(s.spans_m)) == 1 for s in config.scenarios]
    sampled_equal = (config.sampling_settings or {}).get("equal_spans")
    if sampled_equal is not None:  # a sampled study says what it is; unequal sampling can still draw some equal lines
        spans = "EqualSpan" if sampled_equal else "UnequalSpan"
    else:
        spans = "EqualSpan" if all(equal) else "UnequalSpan" if not any(equal) else "MixedSpan"
    uniform = all(len(set(s.dead_kN_m)) == 1 and len(set(s.live_kN_m)) == 1 for s in config.scenarios)
    return f"{label} {spans} {'UniformLoad' if uniform else 'VariedLoad'} {now.day:02d}{MONTHS[now.month-1]} {now:%H%M}"


def new_run_folder(parent, config, run_name=""):
    """A run folder path that does not exist yet ('-2', '-3' … added when needed)."""
    parent, name = Path(parent), run_folder_name(config, run_name)
    output, copy = parent/name, 2
    while output.exists():
        output, copy = parent/f"{name}-{copy}", copy+1
    return output


def read_csv(path):
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    except OSError:
        return []


def run_summary(folder):
    """What a finished (or stopped) run produced, for a quick look without opening the CSV files."""
    folder = Path(folder)
    try:
        manifest = json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # One row per beam line: ann_size_wide.csv (runs before it existed had that layout in ann_size.csv).
    wide = folder/"ann_size_wide.csv"
    size = read_csv(wide if wide.exists() else folder/"ann_size.csv")
    runs = read_csv(folder/"runs.csv")
    searches = read_csv(folder/"size_search.csv")
    seconds = None
    try:
        span = datetime.fromisoformat(manifest["finished_utc"]) - datetime.fromisoformat(manifest["started_utc"])
        ran = sum(r["status"] in ("complete", "failed") for r in runs)
        seconds = round(span.total_seconds()/ran, 1) if ran else None
    except (KeyError, ValueError):
        pass
    return dict(folder=str(folder), name=folder.name, state=manifest.get("state"), started=manifest.get("started_utc"),
                models=manifest.get("completed_candidates"), total=manifest.get("total_candidates"),
                sizing_rows=len(size), seconds_per_model=seconds,
                failed_models=sum(r["status"] == "failed" for r in runs),
                width_wins=dict(sorted(Counter(f"{float(r['target_b_mm']):g}" for r in size if r.get("target_b_mm")).items())),
                search_status=dict(Counter(r["search_status"] for r in searches)), issues=manifest.get("issues", []))


def list_runs(parent):
    parent = Path(parent)
    found = [run_summary(p) for p in parent.iterdir() if (p/"manifest.json").is_file()] if parent.is_dir() else []
    return sorted((r for r in found if r), key=lambda r: r["started"] or "", reverse=True)
