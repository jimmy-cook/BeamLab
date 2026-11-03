"""Normalize API data, preserve simultaneous demands and build research labels."""
from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path


def cell(value):
    """One number format in every CSV: whole numbers without '.0' (10750, not 10750.0)."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def write_csv(path, rows, fields=None):
    rows = list(rows)
    fields = list(fields or dict.fromkeys(key for row in rows for key in row))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows({key: cell(value) for key, value in row.items()} for row in rows)


def split_for(scenario_id):
    # Every span and candidate from a scenario shares a split.
    n = int(hashlib.sha256(scenario_id.encode()).hexdigest()[:8], 16) % 100
    return "train" if n < 70 else "validation" if n < 85 else "test"


def normalized_forces(base, rows):
    result = []
    for index, row in enumerate(rows):
        if not all(math.isfinite(row[key]) for key in ("station_m", "P_kN", "V2_kN", "V3_kN", "T_kNm", "M2_kNm", "M3_kNm")):
            raise ValueError("Non-finite force result")
        result.append(dict(scenario_id=base["scenario_id"], candidate_id=base["candidate_id"],
                           beam_id=base["beam_id"], force_row_id=f"{base['candidate_id']}_{base['beam_id']}_{index}",
                           object=row["object"], element=row["element"], station_mm=1000*row["station_m"],
                           element_station_mm=1000*row["element_station_m"], combination=row["combination"],
                           step_type=row["step_type"], step_number=row["step_number"],
                           Pu_kN=row["P_kN"], Vu2_kN=row["V2_kN"], Vu3_kN=row["V3_kN"],
                           Tu_kNm=row["T_kNm"], Mu2_kNm=row["M2_kNm"], Mu3_kNm=row["M3_kNm"]))
    return result


def normalized_design(base, rows):
    result = []
    for index, row in enumerate(rows):
        area_keys = [key for key in row if "area_m2" in key]
        invalid = [key for key in area_keys if not math.isfinite(row[key]) or row[key] < 0]
        if invalid and not str(row.get("error") or "").strip():
            raise ValueError("Non-finite or negative design reinforcement")
        def area(key):
            return None if key in invalid else 1e6*row[key]
        result.append(dict(scenario_id=base["scenario_id"], candidate_id=base["candidate_id"],
                           reinforcement_source="ETABS_required",
                           beam_id=base["beam_id"], design_row_id=f"{base['candidate_id']}_{base['beam_id']}_D{index}",
                           station_mm=1000*row["location_m"], top_combo=row["top_combo"],
                           As_top_mm2=area("top_area_m2"), bottom_combo=row["bottom_combo"],
                           As_bottom_mm2=area("bottom_area_m2"), shear_combo=row["shear_combo"],
                           Av_over_s_mm2_per_m=area("shear_area_m2_per_m"),
                           torsion_long_combo=row["torsion_long_combo"], Al_torsion_mm2=area("torsion_long_area_m2"),
                           torsion_trans_combo=row["torsion_trans_combo"], At_over_s_mm2_per_m=area("torsion_trans_area_m2_per_m"),
                           unavailable_reinforcement_fields=" | ".join(invalid),
                           error=str(row["error"] or "").strip(), warning=str(row["warning"] or "").strip()))
    return result


def summarize(base, forces, design, error=""):
    row = dict(base, split=split_for(base["scenario_id"]), design_code="ACI 318-19",
               reinforcement_source="ETABS_required",
               analysis_status="complete" if forces else "unavailable",
               design_status="unavailable", etabs_summary_clear=0, full_aci_verified=0,
               external_axial_minor_axis_check_required=1, detailing_check_required=1,
               serviceability_check_required=1, runtime_error=error,
               design_errors=" | ".join(sorted({d["error"] for d in design if d["error"]})),
               design_warnings=" | ".join(sorted({d["warning"] for d in design if d["warning"]})))
    if design:
        row["design_status"] = "errors" if row["design_errors"] else "warnings" if row["design_warnings"] else "summary_clear"
        row["etabs_summary_clear"] = int(row["design_status"] == "summary_clear" and not error and bool(forces))
    for field in ("Mu3_kNm", "Vu2_kN", "Tu_kNm", "Pu_kN", "Mu2_kNm", "Vu3_kN"):
        if not forces:
            row["max_abs_"+field] = None
            continue
        governing = max(forces, key=lambda f: abs(f[field]))
        row["max_abs_"+field] = abs(governing[field])
        prefix = "at_max_abs_"+field+"_"
        for key in ("force_row_id", "station_mm", "combination", "Pu_kN", "Vu2_kN", "Vu3_kN", "Tu_kNm", "Mu2_kNm", "Mu3_kNm"):
            row[prefix+key] = governing[key]
    # Zones cover entire span. End/mid values are zone maxima, not one sampled point.
    # 0..L/4 = left; L/4..3L/4 = mid; 3L/4..L = right.
    for zone in ("left", "mid", "right"):
        subset = []
        for d in design:
            ratio = d["station_mm"] / base["span_length_mm"]
            actual = "left" if ratio <= 0.25 else "right" if ratio >= 0.75 else "mid"
            if actual == zone:
                subset.append(d)
        for field in ("As_top_mm2", "As_bottom_mm2", "Av_over_s_mm2_per_m", "Al_torsion_mm2", "At_over_s_mm2_per_m"):
            row[f"{field}_{zone}"] = max((d[field] for d in subset if d[field] is not None), default=None)
    return row


def candidate_clear(rows, require_serviceability=False):
    """True when every span summary of one candidate model is clear (the size-target pass rule)."""
    return bool(rows) and len(rows) == rows[0]["number_spans"] and all(x["etabs_summary_clear"] and
        (not require_serviceability or (x.get("sls_overall_status") == "PASS" and x.get("section_overall_status") == "PASS" and
          x.get("section_moment_status") == "PASS" and x.get("section_shear_status") == "PASS" and
          x.get("analysis_status") == "complete" and x.get("design_status") == "summary_clear")) for x in rows)


def size_labels(rows, require_serviceability=False):
    """A whole-model candidate must have all span summaries clear to be selected.

    This is a preliminary research target, never a complete ACI acceptance label.
    """
    groups = {}
    for row in rows:
        groups.setdefault(row["scenario_id"], {}).setdefault(row["candidate_id"], []).append(row)
    result = []
    for scenario, candidates in groups.items():
        clear = [r for r in candidates.values() if candidate_clear(r, require_serviceability)]
        selected = min(clear, key=lambda r: (sum(x["b_mm"]*x["h_mm"]*x["span_length_mm"] for x in r),
                                             r[0]["h_mm"], r[0]["b_mm"])) if clear else []
        for beam in next(iter(candidates.values())):
            pick = next((x for x in selected if x["beam_id"] == beam["beam_id"]), None)
            # Only size-independent structural/architectural inputs go into the sizing table.
            fields = ("scenario_id", "scenario_name", "beam_id", "span_index", "number_spans", "span_length_mm",
                      "span_type", "left_span_ratio", "right_span_ratio", "dead_super_kN_m", "live_kN_m", "fc_MPa",
                      "fy_MPa", "fyt_MPa", "centroid_cover_mm", "flexural_modifier", "split",
                      "dead_input_kN_m", "dead_load_basis", "loading_protocol")
            inputs = {key: beam[key] for key in fields}
            if require_serviceability:
                inputs.update({key:beam.get(key) for key in ("sls_category", "sls_sustained_live_fraction", "sls_duration_months",
                    "sls_attachment_basis", "sls_attachment_tolerance_mm", "sls_dead_limit_divisor", "sls_total_limit_divisor", "sls_settings_id",
                    "sls_limit_mode", "sls_total_basis", "sls_limit_source")})
            total_basis = beam["dead_load_basis"] == "total_including_self_weight"
            if total_basis:
                # Derived superimposed load changes with candidate self-weight.
                # The research size input is the prescribed TOTAL D instead.
                inputs["dead_super_kN_m"] = None
            # The entered total D is dead_input_kN_m; wu_entered_kN_m matches ann_size.csv (Wu_kN_m elsewhere includes self-weight).
            inputs.update(wu_entered_kN_m=1.2*beam["dead_input_kN_m"]+1.6*beam["live_kN_m"] if total_basis else None)
            result.append(dict(inputs,
                               preliminary_b_mm=pick["b_mm"] if pick else None,
                               preliminary_h_mm=pick["h_mm"] if pick else None,
                               selected_candidate_id=pick["candidate_id"] if pick else "",
                               target_status=("minimum_tested_volume_strength_and_SLS_clear" if require_serviceability else "minimum_tested_volume_ETABS_summary_clear") if pick else "no_clear_candidate",
                               serviceability_included=int(require_serviceability),
                               candidates_tested=len(candidates), complete_design_verified=0))
    return result
