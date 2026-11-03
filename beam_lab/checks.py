"""Automatic ETABS-required reinforcement and section-feasibility evidence.
No independent section-capacity calculation is performed in this exporter.
"""
from __future__ import annotations


PASS, FAIL, REVIEW, NE, NR = "PASS", "FAIL", "REVIEW", "NOT_EVALUATED", "NOT_REQUIRED"


def overall(statuses):
    states = list(statuses)
    if FAIL in states:
        return FAIL
    if REVIEW in states:
        return REVIEW
    if NE in states or not states:
        return NE
    return PASS


def message(value):
    value = str(value or "").strip()
    return "" if value.lower() in {"no message", "none", "ok", "no error", "no warning"} else value


def feasibility(forces, design, flexure, shear):
    errors = sorted({message(d["error"]) for d in design if message(d["error"])})
    warnings = sorted({message(d["warning"]) for d in design if message(d["warning"])})
    m_evidence = " | ".join(sorted({message(r.get("Status")) for r in flexure if message(r.get("Status"))}))
    st_evidence = " | ".join(sorted({message(r.get("Status")) for r in shear if message(r.get("Status"))}))
    m = PASS if flexure and design and forces else NE
    s = PASS if shear and design and forces else NE
    t = s
    if m_evidence:
        m = FAIL if any(token in m_evidence.upper() for token in ("O/S", "FAIL", "ERROR", "OVERSTRESS")) else REVIEW
    if st_evidence:
        upper = st_evidence.upper()
        # Shared shear/torsion status without an unambiguous action is REVIEW,
        # never two manufactured independent PASS/FAIL decisions.
        if "INTERACTION" in upper or ("SHEAR" in upper and "TORSION" in upper):
            s = t = FAIL
        elif "SHEAR" in upper or "O/S V" in upper:
            s, t = FAIL, REVIEW
        elif "TORSION" in upper:
            s, t = REVIEW, FAIL
        else:
            s = t = REVIEW
    # ETABS can omit the O/S marker from the 3-location flexure envelope while
    # retaining this exact reinforcement-limit error at an API design station.
    # Attribute it only in the supported zero-torsion gravity scope with an
    # available, clear shear envelope; otherwise keep unresolved errors REVIEW.
    pure_major = bool(forces) and all(abs(f[k]) <= 1e-5 for f in forces for k in ("Pu_kN", "Mu2_kNm", "Vu3_kN", "Tu_kNm"))
    if errors and all(e == "Reinforcing required exceeds maximum allowed" for e in errors) and pure_major and shear and not st_evidence:
        m = FAIL
    # An experimentally verified ETABS flexure-overstress plus this exact error
    # can be attributed to flexure. Unknown/global errors must remain unresolved.
    known_flexure = m == FAIL and all(e == "Reinforcing required exceeds maximum allowed" for e in errors)
    if errors and not known_flexure:
        m = REVIEW if m == PASS else m
        s = REVIEW if s == PASS else s
        t = REVIEW if t == PASS else t
    if warnings:
        m = REVIEW if m == PASS else m
        s = REVIEW if s == PASS else s
        t = REVIEW if t == PASS else t
    if any(d.get("As_top_mm2") is None or d.get("As_bottom_mm2") is None for d in design) and m == PASS:
        m = REVIEW
    if any(d.get("Av_over_s_mm2_per_m") is None for d in design) and s == PASS:
        s = REVIEW
    if any(d.get("Al_torsion_mm2") is None or d.get("At_over_s_mm2_per_m") is None for d in design) and t == PASS:
        t = REVIEW
    if forces and design and not st_evidence and not warnings and (not errors or known_flexure):
        if max(abs(f["Tu_kNm"]) for f in forces) <= 1e-8 and all(d["Al_torsion_mm2"] is not None and d["At_over_s_mm2_per_m"] is not None and d["Al_torsion_mm2"] <= 1e-6 and d["At_over_s_mm2_per_m"] <= 1e-6 for d in design):
            t = NR
    external = any(abs(f[k]) > 1e-5 for f in forces for k in ("Pu_kN", "Mu2_kNm", "Vu3_kN"))
    state = FAIL if errors else overall((m, s, t))
    if external and state == PASS:
        state = REVIEW
    return dict(section_moment_status=m, section_shear_status=s, section_torsion_status=t,
                section_overall_status=state, section_error=" | ".join(errors), section_warning=" | ".join(warnings),
                flexure_table_status=m_evidence, shear_torsion_table_status=st_evidence,
                external_axial_minor_action=int(external))



def station_record(base, force, requirement):
    external = any(abs(force[k]) > 1e-5 for k in ("Pu_kN", "Mu2_kNm", "Vu3_kN"))
    status = NE if requirement is None else FAIL if message(requirement["error"]) else REVIEW if message(requirement["warning"]) or external else PASS
    if status == PASS and any(requirement[k] is None for k in ("As_top_mm2", "As_bottom_mm2", "Av_over_s_mm2_per_m")):
        status = REVIEW
    return dict(scenario_id=base["scenario_id"], candidate_id=base["candidate_id"], beam_id=base["beam_id"],
                combination=force["combination"], force_row_id=force["force_row_id"], station_mm=force["station_mm"],
                Pu_kN=force["Pu_kN"], Mu3_kNm=force["Mu3_kNm"], Vu2_kN=force["Vu2_kN"], Tu_kNm=force["Tu_kNm"],
                As_top_mm2=requirement["As_top_mm2"] if requirement else None,
                As_bottom_mm2=requirement["As_bottom_mm2"] if requirement else None,
                Av_over_s_mm2_per_m=requirement["Av_over_s_mm2_per_m"] if requirement else None,
                section_station_status=status, section_station_pass=1 if status == PASS else 0 if status == FAIL else None,
                section_error=requirement["error"] if requirement else "", section_warning=requirement["warning"] if requirement else "",
                reinforcement_source="ETABS_required", full_aci_verified=0)


def case_rows(base, forces, design, tables):
    combo = forces[0]["combination"] if forces else tables.get("combination", "")
    flexure = [r for r in tables.get("flexure", []) if r.get("UniqueName") == base["beam_id"]]
    shear = [r for r in tables.get("shear", []) if r.get("UniqueName") == base["beam_id"]]
    section = feasibility(forces, design, flexure, shear)
    identity = dict(scenario_id=base["scenario_id"], candidate_id=base["candidate_id"], beam_id=base["beam_id"], combination=combo)
    stations = []
    for force in forces:
        matching = [d for d in design if abs(d["station_mm"]-force["station_mm"]) <= 1e-3]
        stations.append(station_record(base, force, matching[0] if len(matching) == 1 else None))
    result = dict(identity, scenario_name=base["scenario_name"], b_mm=base["b_mm"], h_mm=base["h_mm"],
                  feasibility_basis="ETABS redesign with this single selected strength combination",
                  reinforcement_source="ETABS_required", full_aci_verified=0, **section)
    for source, target in (("As_top_mm2", "required_top_max_mm2"), ("As_bottom_mm2", "required_bottom_max_mm2"),
                           ("Av_over_s_mm2_per_m", "required_shear_max_mm2_per_m")):
        result[target] = max((d[source] for d in design if d[source] is not None), default=None)
    return result, stations
