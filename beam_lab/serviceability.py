"""ACI 318-19 approximate deflection for this tool's prismatic gravity beams.

ETABS supplies service M3 diagrams and required steel. This module integrates
curvature with the midspan effective inertia permitted by 24.2.3.7. It is not
an ETABS concrete-design deflection result or a discrete-bar verification.
"""
from __future__ import annotations

import math
import hashlib
import json
from dataclasses import asdict
from .checks import PASS, FAIL, REVIEW, NE, NR, overall, message

METHOD = "ACI318_19_midspan_Ie_ETABS_moment_integration"
NO_LIMIT = "NO_CODE_LIMIT"
CATEGORIES = {
    "Beam supporting damage-sensitive walls / finishes": "beam_sensitive",
    "Beam supporting non-sensitive nonstructural elements": "beam_nonsensitive",
    "Floor beam with no attached nonstructural elements": "floor_unattached",
    "Flat-roof beam with no attached nonstructural elements": "roof_unattached",
}
DURATIONS = {"3 months": 3, "6 months": 6, "12 months": 12, "5 years or more": 60}
ATTACHMENTS = {"After immediate dead-load deflection": "after_immediate_dead",
               "Before service loading (count full D+L)": "before_service_loading"}
TOTAL_BASES = {"Immediate D + L": "immediate", "Long-term D + L (including creep/shrinkage)": "long_term"}
XI = {3: 1., 6: 1.2, 12: 1.4, 60: 2.}


def value(coefficients, t):
    result = 0.
    for coefficient in reversed(coefficients):
        result = result*t + coefficient
    return result


def roots_between(coefficients, lo=0., hi=1.):
    """Real polynomial roots by recursive derivative isolation, degrees <= 3."""
    c = list(coefficients)
    while len(c) > 1 and abs(c[-1]) < 1e-14:
        c.pop()
    if len(c) <= 1:
        return []
    if len(c) == 2:
        x = -c[0]/c[1]
        return [x] if lo <= x <= hi else []
    turning = roots_between([i*c[i] for i in range(1, len(c))], lo, hi)
    points = sorted({lo, hi, *turning})
    result = [x for x in points if abs(value(c, x)) <= 1e-12*max(1., sum(abs(a) for a in c))]
    for a, b in zip(points, points[1:]):
        fa, fb = value(c, a), value(c, b)
        if fa*fb >= 0:
            continue
        for _ in range(60):
            mid = (a+b)/2
            fm = value(c, mid)
            if fa*fm <= 0:
                b = mid
            else:
                a, fa = mid, fm
        result.append((a+b)/2)
    return sorted(set(round(x, 13) for x in result))


def peak(coefficients, length):
    candidates = [0., 1., *roots_between([i*coefficients[i] for i in range(1, len(coefficients))])]
    t = max(candidates, key=lambda x: abs(value(coefficients, x)))
    return abs(value(coefficients, t)), t*length, value(coefficients, t)


def combine(a, b, factor=1.):
    return [x+factor*y for x, y in zip(a, b)]


def cracked_section(b, h, cover, top, bottom, ec, positive=True):
    """Cracked transformed section; concrete tension ignored, Es=200000 MPa.

    Compression steel replaces concrete: n-1 above the neutral axis, n below.
    Required ETABS areas are idealized at the configured reinforcement centroid.
    """
    n = 200000./ec
    tension, opposite = (bottom, top) if positive else (top, bottom)
    if tension <= 0:
        raise ValueError("No positive required tension-steel area at midspan")
    bars = [(tension, h-cover), (opposite, cover)]
    def first_moment(c):
        return b*c*c/2 + sum((n-1 if y < c else n)*area*(c-y) for area, y in bars)
    lo, hi = 0., h
    for _ in range(90):
        c = (lo+hi)/2
        if first_moment(c) < 0:
            lo = c
        else:
            hi = c
    c = (lo+hi)/2
    inertia = b*c**3/3 + sum((n-1 if y < c else n)*area*(y-c)**2 for area, y in bars)
    rho_compression = opposite/(b*(h-cover)) if cover < c else 0.
    return inertia, c, rho_compression


def effective_inertia(ig, icr, mcr, ma):
    if ma <= 2*mcr/3:
        return ig
    return min(ig, icr/(1-(2*mcr/(3*ma))**2*(1-icr/ig)))


def diagram(base, rows, name):
    fs = sorted([r for r in rows if r["combination"] == name], key=lambda r: r["station_mm"])
    length = base["span_length_mm"]
    if len(fs) < 3 or abs(fs[0]["station_mm"]) > 1e-3 or abs(fs[-1]["station_mm"]-length) > 1e-3:
        raise ValueError(f"Missing full-span service force diagram: {name}")
    if any(abs(r[k]) > 1e-5 for r in fs for k in ("Pu_kN", "Mu2_kNm", "Vu3_kN", "Tu_kNm")):
        raise ValueError("Serviceability scope requires zero axial, minor-axis and torsion actions")
    q = base["dead_total_kN_m"] if name == "SLS_D" else base["dead_total_kN_m"]+base["live_kN_m"]*base["sls_sustained_live_fraction"] if name == "SLS_SUSTAINED" else base["dead_total_kN_m"]+base["live_kN_m"]
    m0, ml = fs[0]["Mu3_kNm"]*1e6, fs[-1]["Mu3_kNm"]*1e6
    # q in kN/m equals N/mm; CSI M3 is sagging-positive for the generated line.
    def moment(x):
        return m0+(ml-m0)*x/length+q*x*(length-x)/2
    scale = max(1., *(abs(r["Mu3_kNm"]*1e6) for r in fs))
    if max(abs(moment(r["station_mm"])-r["Mu3_kNm"]*1e6) for r in fs) > 1e-5*scale:
        raise ValueError("Service M3 diagram does not match the supported uniform-UDL model")
    if abs(abs(fs[-1]["Vu2_kN"]-fs[0]["Vu2_kN"])-q*length/1000) > 1e-5*max(1., q*length/1000):
        raise ValueError("Service shear equilibrium does not match entered UDL")
    return m0, ml, q, moment(length/2)


def deflection_curve(length, m0, ml, q, ec, ie, area):
    """Downward-positive relative-to-end-chord curve, t=x/L.

    Exact integration of the quadratic UDL moment diagram. Add rectangular
    Timoshenko shear displacement with k=5/6, nu=.2 (not a flexural EI reduction).
    """
    a1 = (ml-m0)/length+q*length/2
    a2 = -q/2
    ei = ec*ie
    kga = (5/6)*(ec/(2*1.2))*area
    c2, c3, c4 = -m0*length**2/(2*ei), -a1*length**3/(6*ei), -a2*length**4/(12*ei)
    shear = q*length**2/(2*kga)
    return [0., -c2-c3-c4+shear, c2-shear, c3, c4]


def empty_summary(reason, status=NE):
    return dict(sls_enabled=0, sls_method=METHOD, sls_aci_status=status, sls_project_status=NE,
                sls_overall_status=status, sls_aci_pass=None, sls_pass=None,
                sls_reason=reason, sls_reinforcement_basis="ETABS_required_idealized_centroid",
                sls_full_aci_verified=0)


def evaluate(base, service_forces, design, settings, stations=21):
    identity = {k: base[k] for k in ("scenario_id", "candidate_id", "beam_id")}
    metadata = dict(sls_enabled=1, sls_category=settings.category, sls_sustained_live_fraction=settings.sustained_live_fraction,
                    sls_limit_mode=settings.limit_mode, sls_total_basis=settings.total_basis,
                    sls_limit_source="USER_DEFINED" if settings.limit_mode == "user_defined" else "ACI_CATEGORY_AND_OPTIONAL_PROJECT",
                    sls_settings_id=hashlib.sha256(json.dumps(asdict(settings),sort_keys=True).encode()).hexdigest()[:16],
                    sls_duration_months=settings.duration_months,
                    sls_attachment_basis=settings.attachment_timing, sls_pre_attachment_creep_credit_mm=0.,
                    sls_attachment_tolerance_mm=settings.attachment_tolerance_mm,
                    sls_cracking_history="retain_full_D_plus_L_midspan_cracking_for_all_service_cases",
                    sls_dead_limit_divisor=settings.dead_limit_divisor, sls_total_limit_divisor=settings.total_limit_divisor)
    if not settings.enabled:
        result = dict(empty_summary("Serviceability disabled"), **metadata)
        result["sls_enabled"] = 0
        return result, [], []
    try:
        settings.require_ready()
        if not base["input_valid"]:
            raise ValueError("Input-invalid candidate")
        if base["span_length_mm"] <= 4*base["h_mm"]:
            raise ValueError("Deep/short beam (span <= 4h) requires a separate serviceability model")
        if not 22 <= base.get("concrete_weight_kN_m3", 24) <= 26:
            raise ValueError("This deflection implementation assumes normal-weight concrete")
        if not design or any(message(d["error"]) or message(d["warning"]) for d in design):
            raise ValueError("ETABS required reinforcement is missing or its design has errors/warnings")
        length, b, h, cover = (base[k] for k in ("span_length_mm", "b_mm", "h_mm", "centroid_cover_mm"))
        midpoint = [d for d in design if abs(d["station_mm"]-length/2) < 1e-3]
        if len(midpoint) != 1:
            raise ValueError("Unique ETABS midspan reinforcement result required")
        d = midpoint[0]
        top, bottom = d["As_top_mm2"], d["As_bottom_mm2"]
        if any(x is None or not math.isfinite(x) or x < 0 for x in (top, bottom)):
            raise ValueError("Invalid/missing ETABS midspan required reinforcement")
        ec = 4700*math.sqrt(base["fc_MPa"])
        ig = b*h**3/12
        mcr = .62*math.sqrt(base["fc_MPa"])*ig/(h/2)
        workbase = dict(base, sls_sustained_live_fraction=settings.sustained_live_fraction or 0.)
        long_term = settings.duration_months is not None and settings.sustained_live_fraction is not None
        names = ("SLS_D", "SLS_DL", "SLS_SUSTAINED") if long_term else ("SLS_D", "SLS_DL")
        diagrams = {name: diagram(workbase, service_forces, name) for name in names}
        ma = abs(diagrams["SLS_DL"][3])
        icr, neutral, rho = cracked_section(b, h, cover, top, bottom, ec, diagrams["SLS_DL"][3] >= 0)
        ie = effective_inertia(ig, icr, mcr, ma)
        if not 0 < icr <= ig or not icr <= ie <= ig:
            raise ValueError("Cracked/effective inertia outside supported transformed-section range")
        curves = {name: deflection_curve(length, *data[:3], ec, ie, b*h) for name, data in diagrams.items()}
        curves["LIVE_INCREMENT"] = combine(curves["SLS_DL"], curves["SLS_D"], -1)
        lam = XI[settings.duration_months]/(1+50*rho) if long_term else None
        if long_term:
            curves["LONG_TERM_ADDITIONAL"] = [lam*x for x in curves["SLS_SUSTAINED"]]
            immediate = curves["SLS_DL"] if settings.attachment_timing == "before_service_loading" else curves["LIVE_INCREMENT"]
            curves["AFTER_ATTACHMENT"] = combine(immediate, curves["LONG_TERM_ADDITIONAL"])
            curves["TOTAL_LONG_TERM"] = combine(curves["SLS_DL"], curves["LONG_TERM_ADDITIONAL"])
            curves["DEAD_LONG_TERM"] = [(1+lam)*x for x in curves["SLS_D"]]
        summary = dict(empty_summary(""), **metadata, sls_Ec_MPa=ec, sls_Ig_mm4=ig, sls_Icr_mm4=icr,
                       sls_Ie_mm4=ie, sls_Ie_over_Ig=ie/ig, sls_Mcr_kNm=mcr/1e6,
                       sls_midspan_service_M_kNm=diagrams["SLS_DL"][3]/1e6,
                       sls_neutral_axis_mm=neutral, sls_compression_ratio=rho, sls_lambda=lam,
                       sls_xi=XI[settings.duration_months] if long_term else None,
                       sls_midspan_required_top_mm2=top, sls_midspan_required_bottom_mm2=bottom,
                       sls_deflection_reference="relative_to_support_chord_downward_positive")
        for name, curve in curves.items():
            magnitude, station, signed = peak(curve, length)
            alias = {"SLS_D":"dead_immediate", "SLS_DL":"combined_immediate", "SLS_SUSTAINED":"sustained_immediate"}.get(name, name.lower())
            summary[f"sls_{alias}_mm"] = magnitude
            summary[f"sls_{alias}_station_mm"] = station
            summary[f"sls_{alias}_signed_mm"] = signed
        checks = []
        def check(name, curve_name, divisor, basis, attachment_tolerance=None):
            amount, station, _ = peak(curves[curve_name], length)
            limit = length/divisor if divisor else None
            if attachment_tolerance is not None:
                limit = min(limit, attachment_tolerance)
            ratio = amount/limit if limit else None
            status = (PASS if ratio <= 1+1e-9 else FAIL) if ratio is not None else NO_LIMIT
            checks.append(dict(identity, check=name, basis=basis, curve=curve_name, deflection_mm=amount,
                               governing_station_mm=station, limit_divisor=divisor, limit_mm=limit,
                               attachment_tolerance_mm=attachment_tolerance,
                               utilization=ratio, status=status, passed=1 if status == PASS else 0 if status == FAIL else None))
            summary[f"sls_{name}_status"] = status
            summary[f"sls_{name}_ratio"] = ratio
            summary[f"sls_{name}_limit_mm"] = limit
        check("dead_immediate", "SLS_D", settings.dead_limit_divisor, "PROJECT_optional_not_an_ACI_dead_only_limit")
        total_curve = "SLS_DL" if settings.total_basis == "immediate" else "TOTAL_LONG_TERM"
        total_name = "combined_immediate" if settings.total_basis == "immediate" else "total_long_term"
        check(total_name, total_curve, settings.total_limit_divisor, "PROJECT_user_defined_total_limit")
        if settings.limit_mode == "aci":
            if settings.category in {"floor_unattached", "roof_unattached"}:
                check("aci_live", "LIVE_INCREMENT", 360 if settings.category == "floor_unattached" else 180, "ACI318_19_Table24.2.2")
            else:
                check("aci_after_attachment", "AFTER_ATTACHMENT", 480 if settings.category == "beam_sensitive" else 240,
                      "ACI318_19_Table24.2.2_and24.2.4.1", settings.attachment_tolerance_mm if settings.category == "beam_nonsensitive" else None)
        else:
            for row in checks:
                row["basis"] = "USER_DEFINED_LIMIT"
        for alias, row in (("dead", checks[0]), ("total", checks[1])):
            for key, source in (("checked_mm", "deflection_mm"), ("checked_station_mm", "governing_station_mm"),
                                ("status", "status"), ("pass", "passed"), ("ratio", "utilization"), ("limit_mm", "limit_mm")):
                summary[f"sls_{alias}_{key}"] = row[source]
        aci = overall(r["status"] for r in checks if r["basis"].startswith("ACI"))
        project_states = [r["status"] for r in checks if r["basis"].startswith("PROJECT") and r["status"] != NO_LIMIT]
        if settings.limit_mode == "user_defined":
            project_states = [r["status"] for r in checks]
        project = overall(project_states) if project_states else NR
        state = overall((aci, project)) if settings.limit_mode == "aci" else project
        summary.update(sls_aci_status=aci, sls_project_status=project, sls_overall_status=state,
                       sls_aci_pass=1 if aci == PASS else 0 if aci == FAIL else None,
                       sls_pass=1 if state == PASS else 0 if state == FAIL else None)
        samples = []
        for i in range(stations):
            t = i/(stations-1)
            record = dict(identity, station_mm=t*length, sls_method=METHOD, reinforcement_source="ETABS_required")
            record.update({name.lower()+"_signed_mm": value(curve, t) for name, curve in curves.items()})
            samples.append(record)
        return summary, checks, samples
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        # Unavailable/unsupported evidence never becomes a manufactured failure.
        result = dict(empty_summary(str(exc), REVIEW), **metadata)
        if settings.limit_mode == "user_defined":
            result.update(sls_aci_status=NE, sls_project_status=REVIEW)
        return result, [], []
