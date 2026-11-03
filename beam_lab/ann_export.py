"""Explicit ANN columns; unknown/invalid results are not converted to binary failures.

A quantity has the same column name here as in beams.csv / design_stations.csv (dead_total_kN_m, live_kN_m,
As_bottom_mm2_mid, Av_over_s_mm2_per_m_left, ...). In ann_size_wide.csv an output is target_ + that name."""
from .checks import PASS, FAIL, REVIEW, NE
from .dataset import size_labels


def binary_status(status):
    return 1 if status == PASS else 0 if status == FAIL else None


def line_spans(b):
    spans = [b.get(f"span_{i}_mm") or 0 for i in range(1, 7)]
    return [s for s in spans if s > 0]


def arrangement(b):
    """Whether all spans of the beam line are equal. (span_type is the span's position: end_left, interior, end_right.)"""
    equal = len(set(line_spans(b))) == 1
    return dict(span_arrangement="equal" if equal else "unequal", equal_spans=int(equal))


def ann_beams(beams):
    output = []
    for beam in beams:
        status = beam.get("section_overall_status", NE)
        row = dict(sample_id=f"{beam['candidate_id']}_{beam['beam_id']}",
                   scenario_id=beam["scenario_id"], candidate_id=beam["candidate_id"], beam_id=beam["beam_id"],
                   split=beam["split"], span_length_mm=beam["span_length_mm"], span_type=beam["span_type"],
                   **arrangement(beam), number_spans=beam["number_spans"], left_span_ratio=beam["left_span_ratio"], right_span_ratio=beam["right_span_ratio"],
                   dead_input_kN_m=beam["dead_input_kN_m"], self_weight_kN_m=beam["self_weight_kN_m"],
                   dead_total_kN_m=beam["dead_total_kN_m"], live_kN_m=beam["live_kN_m"], Wu_kN_m=beam["Wu_kN_m"],
                   dead_load_basis=beam["dead_load_basis"], loading_protocol=beam["loading_protocol"],
                   fc_MPa=beam["fc_MPa"], fy_MPa=beam["fy_MPa"], fyt_MPa=beam["fyt_MPa"],
                   b_mm=beam["b_mm"], h_mm=beam["h_mm"], centroid_cover_mm=beam["centroid_cover_mm"],
                   input_valid=beam["input_valid"], input_validation_error=beam["input_validation_error"],
                   section_overall_status=status, section_pass=binary_status(status),
                   section_moment_status=beam.get("section_moment_status", NE), section_shear_status=beam.get("section_shear_status", NE),
                   section_torsion_status=beam.get("section_torsion_status", NE),
                   reinforcement_source="ETABS_required", full_aci_verified=0,
                   classification_eligible=int(bool(beam["input_valid"]) and status in (PASS, FAIL)))
        # Signed governing components may be at different stations: source vectors
        # remain in forces.csv / ann_stations.csv. Do not treat these as concurrent.
        for alias, source in (("Mu_kNm", "Mu3_kNm"), ("Vu_kN", "Vu2_kN"), ("Pu_kN", "Pu_kN")):
            row[alias] = beam.get(f"at_max_abs_{source}_{source}")
            row[alias+"_abs"] = beam.get("max_abs_"+source)
            row[alias+"_station_mm"] = beam.get(f"at_max_abs_{source}_station_mm")
            row[alias+"_combination"] = beam.get(f"at_max_abs_{source}_combination")
        rebar = [field.format(zone) for _, field in REBAR_FIELDS for zone in ("left", "mid", "right")]
        row.update({key: beam.get(key) for key in rebar})
        row["reinforcement_regression_eligible"] = int(status == PASS and bool(beam["input_valid"]) and
                                                       all(row[key] is not None for key in rebar))
        row.update({key: val for key, val in beam.items() if key.startswith("sls_")})
        row.update(span_index=beam.get("span_index"), flexural_modifier=beam.get("flexural_modifier"),
                   concrete_weight_kN_m3=beam.get("concrete_weight_kN_m3"))
        row.update({f"span_{i}_mm": beam.get(f"span_{i}_mm") for i in range(1,7)})
        if beam.get("sls_enabled") and beam.get("sls_settings_id"):
            row["sample_id"] += "_"+beam["sls_settings_id"]
        row["strength_and_sls_status"] = beam.get("strength_and_sls_status", NE)
        row["serviceability_classification_eligible"] = int(bool(beam["input_valid"]) and beam.get("sls_overall_status") in (PASS, FAIL))
        output.append(row)
    return output


def ann_deflection(beams):
    """Compact input/target table for the selected user-defined deflection task."""
    output = []
    structural = ("sample_id", "scenario_id", "candidate_id", "beam_id", "split", "span_index", "number_spans",
                  "span_length_mm", "span_type", "left_span_ratio", "right_span_ratio",
                  "dead_input_kN_m", "self_weight_kN_m", "dead_total_kN_m", "live_kN_m",
                  "b_mm", "h_mm", "fc_MPa", "fy_MPa", "fyt_MPa", "centroid_cover_mm", "flexural_modifier",
                  "concrete_weight_kN_m3", "loading_protocol", "dead_load_basis", "input_valid")
    for beam in ann_beams(beams):
        row = {key: beam.get(key) for key in structural}
        row.update({f"span_{i}_mm": beam.get(f"span_{i}_mm") for i in range(1,7)})
        row.update(settings_id=beam.get("sls_settings_id"),
                   limit_mode=beam.get("sls_limit_mode"), limit_source=beam.get("sls_limit_source"),
                   dl_limit_divisor=beam.get("sls_dead_limit_divisor"), total_limit_divisor=beam.get("sls_total_limit_divisor"),
                   total_deflection_basis=beam.get("sls_total_basis"),
                   sustained_live_fraction=beam.get("sls_sustained_live_fraction"), duration_months=beam.get("sls_duration_months"),
                   aci_category=beam.get("sls_category"), attachment_basis=beam.get("sls_attachment_basis"),
                   attachment_tolerance_mm=beam.get("sls_attachment_tolerance_mm"),
                   dl_deflection_mm=beam.get("sls_dead_checked_mm"), total_deflection_mm=beam.get("sls_total_checked_mm"),
                   dl_governing_station_mm=beam.get("sls_dead_checked_station_mm"), total_governing_station_mm=beam.get("sls_total_checked_station_mm"),
                   dl_limit_mm=beam.get("sls_dead_limit_mm"), total_limit_mm=beam.get("sls_total_limit_mm"),
                   dl_utilization=beam.get("sls_dead_ratio"), total_utilization=beam.get("sls_total_ratio"),
                   dl_status=beam.get("sls_dead_status", NE), total_status=beam.get("sls_total_status", NE),
                   dl_pass=beam.get("sls_dead_pass"), total_pass=beam.get("sls_total_pass"),
                   deflection_status=beam.get("sls_overall_status", NE), deflection_pass=beam.get("sls_pass"),
                   aci_status=beam.get("sls_aci_status", NE), section_overall_status=beam.get("section_overall_status", NE),
                   classification_eligible=beam.get("serviceability_classification_eligible", 0),
                   calculation_method=beam.get("sls_method"), reinforcement_basis=beam.get("sls_reinforcement_basis"),
                   full_aci_verified=0, reason=beam.get("sls_reason", ""))
        output.append(row)
    return output


REBAR_FIELDS = (("As_top_mm2", "As_top_mm2_{}"), ("As_bottom_mm2", "As_bottom_mm2_{}"),
                ("Av_over_s_mm2_per_m", "Av_over_s_mm2_per_m_{}"))  # (quantity, beams.csv column per zone)


def ratio(a, b):
    return round(a/b, 4) if a and b else None


def size_features(b):
    """Inputs derived from the beam line only (spans and entered loads), known before any size is chosen.

    Nothing here may depend on the target b × h: self-weight and Wu including self-weight would leak the answer."""
    spans = line_spans(b)
    D, L = b["dead_input_kN_m"], b["live_kN_m"]
    row = arrangement(b)
    row.update({f"span_ratio_{i+1}_{i}": ratio(spans[i], spans[i-1]) for i in range(1, len(spans))})
    row.update(max_adjacent_span_ratio=max((round(max(a, c)/min(a, c), 4) for a, c in zip(spans, spans[1:])), default=1.0),
               max_span_mm=max(spans), min_span_mm=min(spans), total_length_mm=sum(spans),
               max_min_span_ratio=ratio(max(spans), min(spans)), end_to_interior_span_ratio=
               ratio(max(spans[0], spans[-1]), max(spans[1:-1])) if len(spans) > 2 else None,
               live_dead_ratio=ratio(L, D), wu_entered_kN_m=round(1.2*D+1.6*L, 4))
    return row


def size_rebar(spans):
    """ETABS-required reinforcement of the selected section: outputs (targets), never inputs."""
    row = {}
    for b in spans:
        n = b["span_index"]+1
        for _, field in REBAR_FIELDS:
            for place in ("left", "mid", "right"):
                row[f"target_span{n}_{field.format(place)}"] = b.get(field.format(place))
    for name, field in REBAR_FIELDS:
        values = [b.get(field.format(place)) for b in spans for place in ("left", "mid", "right")]
        values = [v for v in values if v is not None]
        row[f"target_max_{name}"] = max(values) if values else None
    return row


def ann_size(beams, lines=None):
    """ann_size.csv: the selected (smallest all-pass) size of each finished beam line, one row per span.

    Same columns, in the same order, as ann_beams.csv, followed by the size-independent line features and
    candidates_tested. All spans of a line show the same b_mm × h_mm. ann_size_wide.csv holds the same answers
    as one row per beam line."""
    by_candidate = {}
    for b in beams:
        by_candidate.setdefault(b["candidate_id"], []).append(b)
    output = []
    for line in ann_size_wide(beams, lines):
        spans = sorted(by_candidate[line["selected_candidate_id"]], key=lambda s: s["span_index"])
        extra = {k: v for k, v in size_features(spans[0]).items() if k not in ("span_arrangement", "equal_spans")}
        extra["candidates_tested"] = line["candidates_tested"]
        output.extend(dict(row, **extra) for row in ann_beams(spans))
    return output


TRAIN_ID = ("scenario_id", "beam_id", "split", "span_index", "span_type", "span_length_mm")
TRAIN_INPUTS = ("span_1_mm", "span_2_mm", "span_3_mm", "span_arrangement", "equal_spans", "dead_input_kN_m", "live_kN_m",
                "fc_MPa", "fy_MPa", "fyt_MPa", "span_ratio_2_1", "span_ratio_3_2", "max_adjacent_span_ratio", "max_span_mm",
                "min_span_mm", "total_length_mm", "max_min_span_ratio", "end_to_interior_span_ratio", "live_dead_ratio",
                "wu_entered_kN_m")
TRAIN_OUTPUTS = ("b_mm", "h_mm")


def ann_size_train(size_rows):
    """ann_size_train.csv: the ann_size.csv rows cut to what a size ANN uses. Identification, size-independent inputs
    (known before b × h is chosen), then the outputs b_mm, h_mm. Nothing derived from the chosen size is an input."""
    return [{key: row.get(key) for key in (*TRAIN_ID, *TRAIN_INPUTS, *TRAIN_OUTPUTS)} for row in size_rows]


def ann_size_wide(beams, lines=None):
    """ann_size_wide.csv: one positive sizing target per uniformly loaded research beam line, spans side by side.

    lines, when given, is the set of scenario_ids whose size search/run finished; other lines get no row."""
    output, seen = [], set()
    lookup={(b["scenario_id"],b["candidate_id"],b["beam_id"]):b for b in beams}
    by_candidate = {}
    for b in beams:
        by_candidate.setdefault((b["scenario_id"], b["candidate_id"]), []).append(b)
    for label in size_labels(beams,require_serviceability=True) if beams else []:
        if not label["selected_candidate_id"] or (lines is not None and label["scenario_id"] not in lines):
            continue
        identity=(label["scenario_id"],label.get("sls_settings_id"))
        if identity in seen:
            continue
        b=lookup[(label["scenario_id"],label["selected_candidate_id"],label["beam_id"])]
        # dead_input_kN_m is the entered D: total including self-weight, or superimposed with ETABS adding self-weight (see dead_load_basis).
        if b["loading_protocol"]!="research_full_udl":
            continue
        seen.add(identity)
        row={key:b.get(key) for key in ("scenario_id","scenario_name","split","number_spans","fc_MPa","fy_MPa","fyt_MPa",
                                        "centroid_cover_mm","flexural_modifier","concrete_weight_kN_m3","loading_protocol","dead_load_basis")}
        row.update({f"span_{i}_mm":b.get(f"span_{i}_mm") for i in range(1,7)})
        row.update({key:b.get(key) for key in ("sls_settings_id","sls_limit_mode","sls_total_basis","sls_dead_limit_divisor",
                   "sls_total_limit_divisor","sls_sustained_live_fraction","sls_duration_months","sls_category",
                   "sls_attachment_basis","sls_attachment_tolerance_mm")})
        row.update(sample_id=b["scenario_id"]+"_"+str(b.get("sls_settings_id") or ""),
                   dead_input_kN_m=b["dead_input_kN_m"],live_kN_m=b["live_kN_m"])
        row.update(size_features(b))
        row.update(target_b_mm=label["preliminary_b_mm"],target_h_mm=label["preliminary_h_mm"])
        # Loads of the selected section: outputs, they depend on b × h.
        row.update(target_self_weight_kN_m=b.get("self_weight_kN_m"),target_dead_total_kN_m=b.get("dead_total_kN_m"),
                   target_Wu_kN_m=b.get("Wu_kN_m"))
        row.update(size_rebar(sorted(by_candidate[(b["scenario_id"], label["selected_candidate_id"])], key=lambda s: s["span_index"])))
        row.update(selected_candidate_id=label["selected_candidate_id"],candidates_tested=label["candidates_tested"],
                   target_rule="minimum_tested_volume_all_spans_analysis_design_flexure_shear_deflection_PASS",
                   full_aci_verified=0)
        output.append(row)
    return output


def ann_stations(beams, forces, designs):
    bases = {(b["candidate_id"], b["beam_id"]): b for b in beams}
    requirements = {}
    for design in designs:
        key = (design["candidate_id"], design["beam_id"], design["combination"], round(design["station_mm"], 3))
        requirements.setdefault(key, []).append(design)
    output = []
    for force in forces:
        base = bases.get((force["candidate_id"], force["beam_id"]))
        if base is None:
            continue
        key = (force["candidate_id"], force["beam_id"], force["combination"], round(force["station_mm"], 3))
        ds = requirements.get(key, [])
        d = ds[0] if len(ds) == 1 else None
        external = any(abs(force[k]) > 1e-5 for k in ("Pu_kN", "Mu2_kNm", "Vu3_kN"))
        status = NE if d is None else FAIL if d["error"] else REVIEW if d["warning"] or external else PASS
        if status == PASS and any(d[k] is None for k in ("As_top_mm2", "As_bottom_mm2", "Av_over_s_mm2_per_m")):
            status = REVIEW
        output.append(dict(scenario_id=force["scenario_id"], candidate_id=force["candidate_id"], beam_id=force["beam_id"],
                           force_row_id=force["force_row_id"], split=base["split"], station_mm=force["station_mm"],
                           combination=force["combination"], dead_total_kN_m=base["dead_total_kN_m"], live_kN_m=base["live_kN_m"],
                           Wu_kN_m=base["Wu_kN_m"], b_mm=base["b_mm"], h_mm=base["h_mm"], fc_MPa=base["fc_MPa"], fy_MPa=base["fy_MPa"],
                           Mu_kNm=force["Mu3_kNm"], Vu_kN=force["Vu2_kN"], Pu_kN=force["Pu_kN"], Tu_kNm=force["Tu_kNm"],
                           **{key: d[key] if d else None for key in ("As_top_mm2", "As_bottom_mm2", "Av_over_s_mm2_per_m")},
                           section_station_status=status, section_station_pass=binary_status(status),
                           beam_section_status=base.get("section_overall_status", NE),
                           section_error=d["error"] if d else "", section_warning=d["warning"] if d else "",
                           reinforcement_source="ETABS_required", full_aci_verified=0))
    return output
