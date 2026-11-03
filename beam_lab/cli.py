from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Config, plan_rows
from .dataset import write_csv
from .sampling import ResearchRanges, sample_study


def main():
    parser = argparse.ArgumentParser(description="ETABS 22 continuous beam CSV dataset tool")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "generate"):
        sub = commands.add_parser(command)
        sub.add_argument("--config", required=True)
        sub.add_argument("--out", required=True)
    sample = commands.add_parser("sample-config", help="Create reproducible scenario inputs; no ETABS execution")
    sample.add_argument("--count", type=int, default=100)
    sample.add_argument("--seed", type=int, default=42)
    sample.add_argument("--out", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    verify = commands.add_parser("verify-search", help="Compare a smart size search run with an every-size run of the same study")
    verify.add_argument("--search", required=True, help="Run folder made with Smart size search ticked")
    verify.add_argument("--full", required=True, help="Run folder of the same study with Smart size search unticked")
    verify.add_argument("--out", required=True, help="Report CSV to write")
    args = parser.parse_args()
    if args.command == "verify-search":
        from .verify_search import main as verify_main
        if Path(args.out).exists():
            parser.error("Refusing to overwrite an existing report")
        try:
            summary = verify_main(args.search, args.full, args.out)
        except (ValueError, OSError, KeyError) as exc:
            parser.error(str(exc))
        for key, value in summary.items():
            print(f"{key}: {value}")
        print(f"Report: {Path(args.out).resolve()}")
        return
    if args.command == "serve":
        import uvicorn
        uvicorn.run("beam_lab.api:app", host="127.0.0.1", port=args.port, workers=1)
        return
    if args.command == "sample-config":
        if not 1 <= args.count <= 1000:
            parser.error("count must be 1..1000")
        try:
            data = sample_study(ResearchRanges(count=args.count, seed=args.seed)).to_dict()
        except ValueError as exc:
            parser.error(str(exc))
        path = Path(args.out)
        if path.exists():
            parser.error("Refusing to overwrite an existing configuration")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Saved {args.count} scenarios, {args.count*6} candidate models: {path.resolve()}")
        return
    try:
        config = Config.load(args.config)
        if args.command == "plan":
            output = Path(args.out)
            if output.exists():
                parser.error("Refusing to overwrite an existing CSV")
            write_csv(output, plan_rows(config))
            print(f"Saved candidate plan (no analysis results): {output.resolve()}")
        else:
            from .pipeline import run
            manifest = run(config, args.out,
                           lambda m: print(f"{m['state']}: {m['completed_candidates']}/{m['total_candidates']}", flush=True))
            print(json.dumps(manifest, indent=2))
            if manifest["state"] != "completed":
                raise SystemExit(2)
    except (ValueError, TypeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
