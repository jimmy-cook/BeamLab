"""Compare a smart-size-search run with an every-size run of the same study.

Reads only the CSV/JSON files each run already wrote; ETABS is not needed.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .dataset import candidate_clear, write_csv


def read(folder, name):
    with (Path(folder)/name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def number(text):
    return float(text) if text not in ("", None) else None


def compare(search_dir, full_dir):
    """One report row per beam line and width, plus a summary.

    The full run must test every depth the search could choose: run the same saved study
    with Smart size search unticked.
    """
    search_dir, full_dir = Path(search_dir), Path(full_dir)
    manifest = json.loads((search_dir/"manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("size_search"):
        raise ValueError(f"{search_dir} is not a smart size search run (no size_search.csv)")
    configs = [json.loads((d/"config.json").read_text(encoding="utf-8")) for d in (search_dir, full_dir)]
    if configs[1].get("size_search"):
        raise ValueError(f"{full_dir} is also a smart size search run; untick Smart size search and run every size")
    for key in ("scenarios", "sections_mm", "serviceability", "min_span_depth_ratio", "loading_protocol", "dead_load_basis"):
        if configs[0].get(key) != configs[1].get(key):
            raise ValueError(f"The two runs used different studies ({key} differs); load the same saved study for both")
    require_sls = manifest["size_search_pass_rule"] == "strength_and_deflection"
    status = {r["candidate_id"]: r["status"] for r in read(full_dir, "runs.csv")}
    models = {}
    for row in read(full_dir, "beams.csv"):
        row = dict(row, etabs_summary_clear=int(row["etabs_summary_clear"] or 0), number_spans=int(row["number_spans"]))
        models.setdefault((row["scenario_id"], number(row["b_mm"])), {}).setdefault(number(row["h_mm"]), []).append(row)
    report = []
    for row in read(search_dir, "size_search.csv"):
        key = (row["scenario_id"], number(row["b_mm"]))
        outcomes = []  # (h, True/False) for every depth the full run analysed
        errors = 0
        for h, spans in sorted(models.get(key, {}).items()):
            state = status.get(spans[0]["candidate_id"])
            if state == "complete":
                outcomes.append((h, candidate_clear(spans, require_sls)))
            elif state == "failed":
                errors += 1
        violations = [f"{h:g} PASS but {deeper:g} FAIL" for i, (h, ok) in enumerate(outcomes) if ok
                      for deeper, later in outcomes[i+1:] if not later]
        full_answer = next((h for h, ok in outcomes if ok), None)
        search_answer = number(row["smallest_passing_h_mm"])
        judged = row["search_status"] in ("found", "none_passed", "no_valid_depth") and not errors
        report.append(dict(scenario_id=row["scenario_id"], scenario_name=row["scenario_name"], b_mm=key[1],
                           search_status=row["search_status"], search_smallest_h_mm=search_answer,
                           full_smallest_h_mm=full_answer, full_depths_analysed=len(outcomes), full_run_errors=errors,
                           search_etabs_runs=int(row["etabs_runs"]),
                           same_answer=int(search_answer == full_answer) if judged else None,
                           deeper_always_passes=int(not violations), violations="; ".join(violations[:5]),
                           full_pass_pattern=" ".join(f"{h:g}:{'P' if ok else 'F'}" for h, ok in outcomes)))
    target_file = "size_targets_sls.csv" if require_sls else "size_targets.csv"
    targets = [{r["scenario_id"]: (number(r["preliminary_b_mm"]), number(r["preliminary_h_mm"])) for r in read(d, target_file)}
               for d in (search_dir, full_dir)]
    for row in report:
        a, b = targets[0].get(row["scenario_id"]), targets[1].get(row["scenario_id"])
        row["search_target"] = "" if not a or a[0] is None else f"{a[0]:g} x {a[1]:g}"
        row["full_target"] = "" if not b or b[0] is None else f"{b[0]:g} x {b[1]:g}"
    judged = [r for r in report if r["same_answer"] is not None]
    lines = {r["scenario_id"] for r in report}
    summary = dict(beam_lines=len(lines), widths_compared=len(report), widths_judged=len(judged),
                   same_smallest_depth=sum(r["same_answer"] for r in judged),
                   widths_where_deeper_failed=sum(1 for r in report if not r["deeper_always_passes"]),
                   lines_with_same_target=sum(1 for s in lines if targets[0].get(s) == targets[1].get(s)),
                   search_etabs_runs=sum(r["search_etabs_runs"] for r in report),
                   full_etabs_runs=sum(1 for s in status.values() if s in ("complete", "failed")))
    summary["verdict"] = ("PASS: the search found the same smallest depth as testing every size, and deeper beams always passed"
                          if judged and summary["same_smallest_depth"] == len(judged) and not summary["widths_where_deeper_failed"]
                          else "CHECK: see the rows with same_answer = 0 or deeper_always_passes = 0")
    return report, summary


def main(search_dir, full_dir, out):
    report, summary = compare(search_dir, full_dir)
    write_csv(out, report)
    return summary
