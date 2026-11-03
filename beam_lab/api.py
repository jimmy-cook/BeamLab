"""Local REST interface. One COM worker owns one ETABS instance at a time."""
from __future__ import annotations

import io
import csv
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .config import Config, plan_rows
from .pipeline import run

app = FastAPI(title="ETABS Beam Lab", version="0.7.0", description="Automatic ETABS-required reinforcement, research UDL datasets and section checks.")
ROOT = Path("runs").resolve()
WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ETABS-COM")
JOBS = {}
LOCK = threading.Lock()
FILES = {"beams.csv", "forces.csv", "design_stations.csv", "size_targets.csv", "runs.csv",
         "candidate_plan.csv", "table_catalog.csv", "manifest.json", "config.json",
         "combination_checks.csv", "station_checks.csv", "combination_design_stations.csv"}
FILES.update({"ann_beams.csv", "ann_stations.csv"})
FILES.update({"service_forces.csv", "deflection_checks.csv", "deflection_stations.csv", "size_targets_sls.csv"})
FILES.add("ann_deflection.csv")
FILES.update({"ann_size.csv", "ann_size_train.csv", "ann_size_wide.csv"})


@app.middleware("http")
async def local_requests(request: Request, call_next):
    host = request.headers.get("host", "").split(":")[0]
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        return Response("Local interface only", status_code=403)
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://{request.headers.get('host')}", f"https://{request.headers.get('host')}"}:
            return Response("Cross-origin job creation rejected", status_code=403)
        if int(request.headers.get("content-length", "0")) > 2_000_000:
            return Response("Configuration too large", status_code=413)
    return await call_next(request)


def parse_config(data):
    # Network callers cannot override the server's executable path.
    if "etabs_executable" in data:
        raise HTTPException(422, "Set the ETABS executable via the CLI configuration, not the REST request")
    try:
        return Config.parse(data)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/health")
def health():
    return {"status": "ready", "etabs_connectivity": "not_checked_until_job_runs", "csv_outputs": sorted(FILES)}


@app.get("/example")
def example():
    return {"scenarios": [{"name": "three_spans", "spans_m": [6, 6, 6], "dead_kN_m": [20, 20, 20],
                           "live_kN_m": [10, 10, 10], "fc_MPa": 30, "fy_MPa": 420}],
            "sections_mm": [[300, 600]], "max_models": 1, "stations_per_span": 21,
            "loading_protocol": "research_full_udl", "dead_load_basis": "total_including_self_weight", "enforce_research_ranges": True,
            "serviceability": {"enabled": True, "limit_mode": "user_defined", "total_basis": "immediate",
                               "dead_limit_divisor": 120, "total_limit_divisor": 240}}


@app.post("/plan")
def plan(data: dict = Body(...)):
    config = parse_config(data)
    rows = list(plan_rows(config))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return Response("\ufeff"+output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="candidate_plan.csv"'})


def perform(job_id, config):
    def progress(value):
        with LOCK:
            JOBS[job_id] = dict(job_id=job_id, **value)
    try:
        manifest = run(config, ROOT/job_id, progress)
        progress(manifest)
    except Exception as exc:
        progress({"state": "failed", "issues": [f"{type(exc).__name__}: {exc}"]})


@app.post("/jobs", status_code=202)
def create_job(data: dict = Body(...)):
    config = parse_config(data)
    job_id = uuid.uuid4().hex
    with LOCK:
        active = sum(j["state"] in {"queued", "running"} for j in JOBS.values())
        if active >= 10:
            raise HTTPException(429, "Job queue is full")
        JOBS[job_id] = {"job_id": job_id, "state": "queued"}
    WORKER.submit(perform, job_id, config)
    return {"job_id": job_id, "status_url": f"/jobs/{job_id}"}


@app.get("/jobs/{job_id}")
def status(job_id: str):
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, "Unknown job; status registry is local to this server process")
        result = dict(JOBS[job_id])
    result["files"] = [f"/jobs/{job_id}/files/{name}" for name in sorted(FILES) if (ROOT/job_id/name).is_file()]
    return result


@app.get("/jobs/{job_id}/files/{filename}")
def download(job_id: str, filename: str):
    with LOCK:
        if job_id not in JOBS:
            raise HTTPException(404, "Unknown job")
        state = JOBS[job_id]["state"]
    if state in {"queued", "running"}:
        raise HTTPException(409, "Wait for the job to finish before downloading its CSV files")
    if filename not in FILES:
        raise HTTPException(404, "Unknown output")
    path = ROOT/job_id/filename
    if not path.is_file():
        raise HTTPException(404, "File not yet available")
    return FileResponse(path, filename=filename, media_type="text/csv" if filename.endswith(".csv") else "application/json")
