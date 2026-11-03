"""Independent demand checks for examples/validation.json (no ETABS required)."""
import argparse
import csv
from pathlib import Path

from .dataset import write_csv


def validate(data):
    data = Path(data)
    with (data/"beams.csv").open(encoding="utf-8-sig") as stream:
        beams = list(csv.DictReader(stream))
    with (data/"forces.csv").open(encoding="utf-8-sig") as stream:
        forces = list(csv.DictReader(stream))
    checks = []
    for beam in beams:
        if beam["scenario_name"] not in {"simply_supported_6m", "two_equal_spans_6m"}:
            continue
        rows = [f for f in forces if f["candidate_id"] == beam["candidate_id"] and f["beam_id"] == beam["beam_id"]]
        length = float(beam["span_length_mm"])/1000
        w = 1.2*float(beam["dead_total_kN_m"]) + 1.6*float(beam["live_kN_m"])
        if beam["scenario_name"] == "simply_supported_6m":
            target_combo = "U_1p2D_1p6L_1"
            matching = [r for r in rows if r["combination"] == target_combo]
            measured_m = max(abs(float(r["Mu3_kNm"])) for r in matching)
            measured_v = max(abs(float(r["Vu2_kN"])) for r in matching)
            items = [("simple_midspan_Mu", w*length**2/8, measured_m, 1e-6),
                     ("simple_support_Vu", w*length/2, measured_v, 1e-6)]
        else:
            matching = [r for r in rows if r["combination"] == "U_1p2D_1p6L_11"]
            measured = max(abs(float(r["Mu3_kNm"])) for r in matching)
            items = [("two_equal_spans_support_Mu", w*length**2/8, measured, 0.01)]
        for name, expected, measured, tolerance in items:
            relative = abs(measured-expected)/expected
            checks.append(dict(check=name, beam_id=beam["beam_id"], expected=expected, measured=measured,
                               relative_error=relative, tolerance=tolerance, passed=int(relative <= tolerance)))
    if len(checks) != 4:
        raise ValueError("Expected exactly four checks from the two validation scenarios")
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = validate(args.data)
    write_csv(args.out, rows)
    for row in rows:
        print(f"{row['check']} {row['beam_id']}: {'PASS' if row['passed'] else 'FAIL'} ({row['relative_error']:.3%})")
    if not all(row["passed"] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
