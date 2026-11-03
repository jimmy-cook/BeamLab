"""Run each candidate in a fresh model, audit failures, export real CSI results only."""
from __future__ import annotations

import copy
import hashlib
import json
import platform
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import candidate_rows, plan_rows
from .dataset import candidate_clear, normalized_design, normalized_forces, size_labels, summarize, write_csv
from .etabs import Etabs
from .checks import case_rows, overall, NR
from .ann_export import ann_beams, ann_stations, ann_deflection, ann_size, ann_size_train, ann_size_wide
from .serviceability import evaluate as evaluate_serviceability

MAX_ETABS_RESTARTS = 3  # in a row without a completed model; a setup that keeps failing then records failures instead of looping
SEARCH_FINISHED = {"found", "none_passed", "no_valid_depth"}  # width searches that ended with a definite answer
LOCKED_FILE_WAIT_SECONDS = 120  # at the end of a run, how long to wait for an output file open in Excel to be closed


def smallest_passing(depths, passes):
    """Bisect ascending depths for the smallest one where passes(h) is True.

    Assumes a deeper beam of the same width never fails where a shallower one passes, so each
    PASS rules out every deeper depth and each FAIL every shallower one: at most ⌈log2(n+1)⌉ calls.
    passes(h) returns None to abandon the search (run error or stop request).
    Returns (answer or None, [(h, outcome), …], finished).
    """
    lo, hi, probes = -1, len(depths), []
    while hi-lo > 1:
        mid = (lo+hi)//2
        outcome = passes(depths[mid])
        probes.append((depths[mid], outcome))
        if outcome is None:
            return None, probes, False
        if outcome:
            hi = mid
        else:
            lo = mid
    return (depths[hi] if hi < len(depths) else None), probes, True


def run(config, output, progress=None, adapter_factory=Etabs, stop_requested=None):
    config.serviceability.require_ready()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "manifest.json").exists():
        raise ValueError("Output already contains a run; choose a new directory")
    models = output / "models"
    models.mkdir(exist_ok=True)
    (output / "config.json").write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    search = config.size_search
    # The search plans each model as it is chosen; listing every grid model up front could be huge.
    plan = [] if search else list(plan_rows(config))
    if not search:
        write_csv(output / "candidate_plan.csv", plan)
    bases_by_candidate = {}
    for row in plan:
        bases_by_candidate.setdefault(row["candidate_id"], []).append(row)
    manifest = dict(tool_version=__version__, started_utc=datetime.now(timezone.utc).isoformat(),
                    source="ETABS_COM_live", state="running", python=platform.python_version(),
                    design_code="ACI 318-19", api_units="kN_m_C", completed_candidates=0,
                    total_candidates=config.planned_models(), issues=[],
                    loading_protocol=config.loading_protocol, dead_load_basis=config.dead_load_basis,
                    reinforcement_source="ETABS_required", reinforcement_api="GetSummaryResultsBeam",
                    serviceability_settings=config.to_dict()["serviceability"],
                    limitations=["ETABS beam design does not include axial force or minor-axis bending",
                                 "Anchorage, laps, confinement and full detailing require separate verification",
                                 "Serviceability uses ACI approximate midspan effective inertia and idealized ETABS-required steel; attachment and load-history assumptions are recorded",
                                 "Gravity-only planar models produce zero torsion and axial demands",
                                 "Loads are gravity research combinations, not a complete building load set"])
    if search:
        manifest.update(size_search=True, size_search_pass_rule="strength_and_deflection" if config.serviceability.enabled else "strength_only")
        manifest["limitations"].append("Smart size search assumes a deeper beam of the same width never fails where a shallower one "
                                       "passes; depths it skips are inferred, not analysed")
    beam_rows, force_rows, design_rows, runs, catalogs = [], [], [], [], []
    combination_rows, station_check_rows, combination_design_rows = [], [], []
    service_forces, deflection_checks, deflection_stations = [], [], []
    search_rows = []
    # Live view for user interfaces only (current line, size, search and last diagram); never written to the run files.
    live = dict(lines=len(config.scenarios), lines_done=0, wins={})
    line_index = {s.scenario_id: i for i, s in enumerate(config.scenarios)}
    def report():
        if progress:
            progress(dict(manifest, live=copy.deepcopy(live)))
    finished_lines = set()  # search mode: lines whose every width search ended with a definite answer
    def final_lines():
        """Beam lines whose size target is final. A line cut short (stop, crash, ETABS error) may not have reached its
        smallest size yet, so it gets no ann_size.csv row; its tested sizes stay in the audit files."""
        if search:
            return finished_lines
        done = {r["candidate_id"] for r in runs if r["status"] in ("complete", "input_invalid")}
        planned = {}
        for row in plan:
            planned.setdefault(row["scenario_id"], set()).add(row["candidate_id"])
        return {line for line, ids in planned.items() if ids <= done}
    reported_locks = set()
    def write(name, content, final=False):
        """Write one output file. A file open in Excel is locked against writing: mid-run it is skipped (the next
        save rewrites it); at the end the run waits for it to be closed, then saves a copy beside it instead."""
        target, waited = output / name, 0
        while True:
            try:
                if name.endswith(".csv"):
                    write_csv(target, content)
                else:
                    target.write_text(content, encoding="utf-8")
                return
            except PermissionError:
                if name not in reported_locks:
                    reported_locks.add(name)
                    manifest["issues"].append(f"{name} was open in another program (such as Excel) while the run was saving it")
                if not final:
                    return
                if waited >= LOCKED_FILE_WAIT_SECONDS:
                    target = output / f"{Path(name).stem} (saved while locked){Path(name).suffix}"
                    manifest["issues"].append(f"{name} stayed open, so the final version was saved as {target.name}")
                    name = target.name
                    continue
                manifest["current_stage"] = f"Close {name} in Excel so the run can save it"
                report()
                time.sleep(2)
                waited += 2
    def save(final=False):
        size = ann_size(beam_rows, final_lines())
        for name, rows in (("beams.csv", beam_rows), ("forces.csv", force_rows),
                           ("design_stations.csv", design_rows), ("runs.csv", runs),
                           ("size_targets.csv", size_labels(beam_rows) if beam_rows else []),
                           ("size_targets_sls.csv", size_labels(beam_rows, require_serviceability=True) if beam_rows else []),
                           ("table_catalog.csv", catalogs), ("combination_checks.csv", combination_rows),
                           ("station_checks.csv", station_check_rows),
                           ("combination_design_stations.csv", combination_design_rows),
                           ("ann_beams.csv", ann_beams(beam_rows)),
                           ("ann_deflection.csv", ann_deflection(beam_rows)),
                           ("ann_size.csv", size), ("ann_size_train.csv", ann_size_train(size)),
                           ("ann_size_wide.csv", ann_size_wide(beam_rows, final_lines())),
                           ("ann_stations.csv", ann_stations(beam_rows, force_rows, combination_design_rows))):
            write(name, rows, final)
        for name, rows in (("service_forces.csv", service_forces), ("deflection_checks.csv", deflection_checks),
                           ("deflection_stations.csv", deflection_stations)):
            write(name, rows, final)
        if search:
            write("candidate_plan.csv", plan, final)
            write("size_search.csv", search_rows, final)
        write("manifest.json", json.dumps(manifest, indent=2), final)
        report()
    save()
    client = None
    def open_etabs():
        nonlocal client
        client = adapter_factory(config.etabs_executable, output / ".com_cache").__enter__()
        manifest["etabs_version"] = client.version
    restarts_in_a_row = 0
    def may_restart():
        """A failed model is repeated once in a fresh ETABS. ETABS can be closed, crashed, hung or left with a broken
        model (it may still answer GetVersion then), so any failure qualifies. Adapters without `responding` never restart."""
        return hasattr(client, "responding") and restarts_in_a_row < MAX_ETABS_RESTARTS
    def restart_etabs(error):
        nonlocal client, restarts_in_a_row
        restarts_in_a_row += 1
        manifest["etabs_restarts"] = manifest.get("etabs_restarts", 0)+1
        state = "answering" if client.responding() else "not answering"
        manifest["issues"].append(f"Model failed ({type(error).__name__}: {error}; ETABS {state}); restarted ETABS and repeated the model")
        manifest["current_stage"] = "Model failed; restarting ETABS and repeating it"
        report()
        old, client = client, None
        old.__exit__(None, None, None)
        open_etabs()
    # Rows a failed model may already have added; removed again before the model is repeated in a fresh ETABS.
    collected = (force_rows, service_forces, design_rows, combination_design_rows, combination_rows, station_check_rows)
    try:
        with ExitStack() as stack:
            open_etabs()
            stack.callback(lambda: client and client.__exit__(None, None, None))
            def execute(scenario, b, h, candidate, bases, retry=True):
                """Analyse and design one candidate model; returns its run entry and span summaries."""
                nonlocal restarts_in_a_row
                entry = dict(scenario_id=scenario.scenario_id, candidate_id=candidate, b_mm=b, h_mm=h,
                             status="running", stage="build", error="", model_path=f"models/{candidate}.edb")
                this_forces, this_design, this_service = {}, {}, {}
                candidate_checks = []
                summaries = []
                live.update(line=dict(index=line_index[scenario.scenario_id]+1, name=scenario.name, spans_m=list(scenario.spans_m),
                                      D=list(scenario.dead_kN_m), L=list(scenario.live_kN_m), fc=scenario.fc_MPa,
                                      fy=scenario.fy_MPa, fyt=scenario.fyt_MPa, gamma=scenario.concrete_weight_kN_m3,
                                      dead_load_basis=config.dead_load_basis),
                            size=dict(b=b, h=h))
                if any(not row["input_valid"] for row in bases):
                    reasons = "; ".join(dict.fromkeys(r["input_validation_error"] for r in bases if r["input_validation_error"]))
                    entry.update(status="input_invalid", stage="input_validation",
                                 error=f"{reasons}; no model was analysed")
                    for base in bases:
                        summary = summarize(base, [], [])
                        summary.update(analysis_status="not_run_input_invalid", design_status="input_invalid", combinations_checked=0)
                        summary.update(evaluate_serviceability(base, [], [], config.serviceability)[0])
                        for prefix, actions in (("section", ("moment", "shear", "torsion", "overall")),):
                            for action in actions:
                                summary[f"{prefix}_{action}_status"] = "NOT_EVALUATED"
                        beam_rows.append(summary)
                        summaries.append(summary)
                    runs.append(entry)
                    manifest["completed_candidates"] += 1
                    manifest["invalid_input_candidates"] = manifest.get("invalid_input_candidates", 0)+1
                    if "Deep beam" in reasons:
                        manifest["deep_beam_candidates"] = manifest.get("deep_beam_candidates", 0)+1
                    live["last"] = dict(b=b, h=h, status=entry["status"], passed=None, diagram=[], deflection=[])
                    save()
                    return entry, summaries
                marks = [len(rows) for rows in collected]
                try:
                    manifest["current_candidate"] = f"{scenario.name} — {b:g} × {h:g} mm"
                    manifest["current_stage"] = "Building model"
                    report()
                    extra = {"serviceability": config.serviceability} if config.serviceability.enabled else {}
                    frames, combos, cases = client.build(scenario, b, h, config.stations_per_span, models/f"{candidate}.edb",
                                                        loading_protocol=config.loading_protocol, dead_load_basis=config.dead_load_basis, **extra)
                    entry["stage"] = "analysis"
                    manifest["current_stage"] = "Running analysis"
                    report()
                    states = client.analyze(cases)
                    entry["analysis_cases_finished"] = len([s for s in states if s["case"] in cases and s["status"] == 4])
                    for base, frame in zip(bases, frames):
                        rows = normalized_forces(base, client.forces(frame, combos))
                        expected = set(combos)
                        if not rows or {r["combination"] for r in rows} != expected:
                            raise RuntimeError("Missing force rows or requested combinations")
                        for name in expected:
                            stations = [r["station_mm"] for r in rows if r["combination"] == name]
                            if min(stations) > 1e-3 or max(stations) < base["span_length_mm"] - 1e-3:
                                raise RuntimeError("Force station coverage does not span full beam")
                        this_forces[base["beam_id"]] = rows
                        force_rows.extend(rows)
                        if config.serviceability.enabled:
                            sls = normalized_forces(base, client.forces(frame, ["SLS_D", "SLS_DL", "SLS_SUSTAINED"]))
                            for row in sls:
                                row["force_row_id"] += "_SLS"
                            this_service[base["beam_id"]] = sls
                            service_forces.extend(sls)
                    entry["stage"] = "design"
                    manifest["current_stage"] = "Running ACI 318-19 design"
                    report()
                    client.start_design()
                    for base, frame in zip(bases, frames):
                        rows = normalized_design(base, client.design(frame))
                        if not rows:
                            raise RuntimeError(f"No beam design rows for {frame}")
                        stations = [r["station_mm"] for r in rows]
                        if min(stations) > 1e-3 or max(stations) < base["span_length_mm"] - 1e-3:
                            raise RuntimeError("Design station coverage does not span full beam")
                        this_design[base["beam_id"]] = rows
                        design_rows.extend(rows)
                    if config.check_combinations:
                        def design_progress(index, total, combination):
                            manifest["current_stage"] = f"Checking combination {index}/{total}: {combination}"
                            report()
                        entry["stage"] = "combination_checks"
                        cases_data = client.combination_designs(frames, combos, design_progress)
                        for combo, evidence in cases_data.items():
                            for base, frame in zip(bases, frames):
                                ds = normalized_design(base, evidence["designs"][frame])
                                for row in ds:
                                    row["combination"] = combo
                                    row["design_row_id"] += "_"+combo
                                combination_design_rows.extend(ds)
                                fs = [f for f in this_forces[base["beam_id"]] if f["combination"] == combo]
                                checked_case, station_checks = case_rows(base, fs, ds, evidence)
                                candidate_checks.append(checked_case)
                                combination_rows.append(checked_case)
                                station_check_rows.extend(station_checks)
                    entry["status"] = "complete"
                    entry["stage"] = "complete"
                    restarts_in_a_row = 0
                    # Discover available tables once. API summaries remain the canonical data.
                    if not catalogs:
                        try:
                            catalogs.extend(client.table_catalog())
                        except Exception as exc:
                            manifest["issues"].append(f"Optional table catalog unavailable: {exc}")
                except Exception as exc:
                    if retry and may_restart():
                        for rows, count in zip(collected, marks):
                            del rows[count:]
                        restart_etabs(exc)
                        return execute(scenario, b, h, candidate, bases, retry=False)
                    entry["status"] = "failed"
                    entry["error"] = f"{type(exc).__name__}: {exc}"
                for base in bases:
                    summary = summarize(base, this_forces.get(base["beam_id"], []),
                                        this_design.get(base["beam_id"], []), entry["error"])
                    checks = [r for r in candidate_checks if r["beam_id"] == base["beam_id"]]
                    for prefix, actions in (("section", ("moment", "shear", "torsion", "overall")),):
                        for action in actions:
                            key = f"{prefix}_{action}_status"
                            states = [r[key] for r in checks]
                            summary[key] = NR if states and all(s == NR for s in states) else overall(states)
                    summary["combinations_checked"] = len(checks)
                    summary["envelope_design_status"] = summary["design_status"]
                    sls, sls_checks, sls_stations = evaluate_serviceability(
                        base, this_service.get(base["beam_id"], []), this_design.get(base["beam_id"], []),
                        config.serviceability, config.stations_per_span)
                    summary.update(sls)
                    summary["strength_and_sls_status"] = overall((summary["section_overall_status"], sls["sls_overall_status"]))
                    deflection_checks.extend(sls_checks)
                    deflection_stations.extend(sls_stations)
                    if config.check_combinations and summary["section_overall_status"] != "PASS":
                        summary["etabs_summary_clear"] = 0
                        if summary["design_status"] == "summary_clear":
                            summary["design_status"] = "combination_"+summary["section_overall_status"].lower()
                    beam_rows.append(summary)
                    summaries.append(summary)
                diagram = []
                for base in bases:
                    rows = this_forces.get(base["beam_id"], [])
                    names = sorted({r["combination"] for r in rows}, key=lambda name: (not name.startswith("U_1p2D_1p6L"), name))
                    pick = sorted((r for r in rows if r["combination"] == names[0]), key=lambda r: r["station_mm"]) if names else []
                    diagram.append(dict(x_mm=[r["station_mm"] for r in pick], M_kNm=[r["Mu3_kNm"] for r in pick]))
                live["last"] = dict(b=b, h=h, status=entry["status"], diagram=diagram,
                                    passed=candidate_clear(summaries, config.serviceability.enabled) if entry["status"] == "complete" else None,
                                    deflection=[x.get("sls_overall_status") for x in summaries])
                runs.append(entry)
                manifest["completed_candidates"] += 1
                save()
                return entry, summaries
            if search:
                require_sls = config.serviceability.enabled
                for scenario in config.scenarios:
                    live["widths"] = {}
                    for b, depths in config.search_groups():
                        if manifest["state"] == "stopped":
                            break
                        ids = {h: config.candidate_id(scenario, [b, h]) for h in depths}
                        planned = {h: list(candidate_rows(config, scenario, b, h, ids[h])) for h in depths}
                        # Deep-beam and self-weight rules exclude the deepest sizes; only valid depths are searched.
                        valid = [h for h in depths if all(r["input_valid"] for r in planned[h])]
                        outcome = dict(status="no_valid_depth", runs=0)
                        live["search"] = dict(b=b, depths=valid, probes=[], answer=None)
                        def passes(h):
                            if stop_requested and stop_requested():
                                manifest["state"] = "stopped"
                                outcome["status"] = "stopped"
                                return None
                            plan.extend(planned[h])
                            entry, summaries = execute(scenario, b, h, ids[h], planned[h])
                            outcome["runs"] += 1
                            if entry["status"] != "complete":
                                outcome["status"] = "run_error"
                                live["search"]["probes"].append([h, None])
                                return None
                            result = candidate_clear(summaries, require_sls)
                            live["search"]["probes"].append([h, result])
                            return result
                        answer, probes, finished = smallest_passing(valid, passes)
                        live["search"]["answer"] = answer
                        live["widths"][str(b)] = answer
                        if valid and finished:
                            outcome["status"] = "found" if answer is not None else "none_passed"
                        search_rows.append(dict(scenario_id=scenario.scenario_id, scenario_name=scenario.name, b_mm=b,
                                                grid_depths=len(depths), min_grid_h_mm=depths[0], max_grid_h_mm=depths[-1],
                                                valid_depths=len(valid), max_valid_h_mm=valid[-1] if valid else None,
                                                search_status=outcome["status"], smallest_passing_h_mm=answer,
                                                etabs_runs=outcome["runs"], worst_case_runs=len(depths).bit_length(),
                                                probes="; ".join(f"{h:g}:{'PASS' if o else 'FAIL' if o is False else 'NOT_RUN' if outcome['status'] == 'stopped' else 'RUN_ERROR'}"
                                                                 for h, o in probes),
                                                pass_rule=manifest["size_search_pass_rule"]))
                        save()
                    if manifest["state"] == "stopped":
                        break
                    found = [(int(w)*h, h, int(w)) for w, h in live["widths"].items() if h is not None]
                    best = min(found) if found else None
                    live["line_answer"] = dict(name=scenario.name, b=best[2], h=best[1]) if best else dict(name=scenario.name, b=None, h=None)
                    if best:
                        live["wins"][str(best[2])] = live["wins"].get(str(best[2]), 0)+1
                    live["lines_done"] += 1
                    statuses = [r["search_status"] for r in search_rows if r["scenario_id"] == scenario.scenario_id]
                    if len(statuses) == len(config.search_groups()) and set(statuses) <= SEARCH_FINISHED:
                        finished_lines.add(scenario.scenario_id)
                    save()
                if manifest["state"] != "stopped":
                    # The worst-case bound is no longer needed once the search has finished.
                    manifest["total_candidates"] = manifest["completed_candidates"]
            else:
                for scenario, b, h, candidate in config.candidates():
                    if stop_requested and stop_requested():
                        manifest["state"] = "stopped"
                        break
                    execute(scenario, b, h, candidate, bases_by_candidate[candidate])
            if manifest["state"] != "stopped":
                manifest["state"] = "completed_with_failures" if any(r["status"] == "failed" for r in runs) else "completed_with_exclusions" if any(r["status"] == "input_invalid" for r in runs) else "completed"
        if client and client.close_error:
            manifest["issues"].append(client.close_error)
    except Exception as exc:
        manifest["state"] = "failed"
        manifest["issues"].append(f"{type(exc).__name__}: {exc}")
    manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["current_stage"] = "Finished" if manifest["state"].startswith("completed") else manifest["state"]
    save(final=True)
    manifest["current_stage"] = "Finished" if manifest["state"].startswith("completed") else manifest["state"]
    manifest["csv_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob("*.csv")}
    write("manifest.json", json.dumps(manifest, indent=2), final=True)
    return manifest
