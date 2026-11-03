"""Local web screen: pick a ready-made research run, start ETABS and watch each beam being designed live.

Standard library only. Listens on 127.0.0.1; every /api request must carry the page's random token, so other
websites open in the same browser cannot start runs.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

from .pipeline import run
from .research import (DEFAULT_ETABS, DEFAULT_RANGES, PRESETS, application_dir, build_study, installed_etabs, list_runs,
                       new_run_folder, run_folder_name, run_summary, settings_summary)


class Lab:
    """State behind the page. One ETABS run at a time, like the desktop app."""

    def __init__(self, out_dir=None, settings_file=None, runner=run, adapter_factory=None, root=None):
        self.root = Path(root or application_dir())
        self.settings_file = Path(settings_file or self.root/"web_settings.json")
        saved = {}
        try:
            saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        self.etabs = saved.get("etabs_executable", DEFAULT_ETABS)
        self.out_dir = Path(out_dir or saved.get("out_dir") or self.root/"runs")
        self.runner, self.adapter_factory = runner, adapter_factory
        self.lock, self.stop_event = threading.Lock(), threading.Event()
        self.job = None
        self.last_seen = time.monotonic()  # the open page polls; used to close a forgotten server

    def _study(self, body):
        key = body.get("preset")
        if key not in PRESETS:
            raise ValueError("Choose what to run")
        return build_study(key, body.get("lines"), body.get("seed"), bool(body.get("whole_numbers", True)),
                           self.etabs, self.root, body.get("equal_spans"), body.get("ratio"), body.get("ranges"))

    def presets(self):
        return [dict(key=k, saved="study" in p, ranges=DEFAULT_RANGES,
                     **{f: p.get(f) for f in ("title", "note", "lines", "seed", "name", "equal_spans", "ratio")})
                for k, p in PRESETS.items()]

    def preview(self, body):
        config = self._study(body)
        beams = [dict(index=i+1, name=s.name, spans_m=list(s.spans_m), D=list(s.dead_kN_m), L=list(s.live_kN_m),
                      fc=s.fc_MPa, fy=s.fy_MPa, fyt=s.fyt_MPa, gamma=s.concrete_weight_kN_m3,
                      dead_load_basis=config.dead_load_basis) for i, s in enumerate(config.scenarios)]
        return dict(summary=settings_summary(config), models=config.planned_models(), beams=beams,
                    folder=run_folder_name(config, body.get("run_name", "")))

    def save_settings(self, body):
        if self.busy():
            raise ValueError("Settings cannot change while ETABS is running")
        etabs, out_dir = str(body.get("etabs_executable", self.etabs)).strip(), str(body.get("out_dir", self.out_dir)).strip()
        if not etabs.lower().endswith("etabs.exe"):
            raise ValueError("The ETABS path must end with ETABS.exe")
        if not out_dir:
            raise ValueError("Choose an output folder")
        self.etabs, self.out_dir = etabs, Path(out_dir).expanduser()
        try:
            self.settings_file.write_text(json.dumps(dict(etabs_executable=self.etabs, out_dir=str(self.out_dir)), indent=2), encoding="utf-8")
        except OSError:
            pass  # remembering the paths is a convenience only
        return self.state()

    def busy(self):
        return bool(self.job and self.job["state"] in ("starting", "running"))

    def start(self, body):
        config = self._study(body)
        if self.adapter_factory is None and not Path(self.etabs).is_file():
            raise ValueError(f"ETABS not found: {self.etabs}. Set the ETABS 22 or 23 path first.")
        with self.lock:
            if self.busy():
                raise ValueError("A run is already in progress")
            folder = new_run_folder(self.out_dir, config, body.get("run_name", ""))
            self.stop_event.clear()
            self.job = dict(state="starting", folder=str(folder), name=folder.name, preset=PRESETS[body["preset"]]["title"],
                            progress=None, live=None, summary=None, error="")
        threading.Thread(target=self._work, args=(config, folder), daemon=False, name="BeamLab-COM").start()
        return self.state()

    def _work(self, config, folder):
        def progress(manifest):
            with self.lock:
                self.job["state"] = "running"
                self.job["progress"] = {k: v for k, v in manifest.items() if k != "live"}
                self.job["live"] = manifest.get("live")
        extra = {"adapter_factory": self.adapter_factory} if self.adapter_factory else {}
        try:
            manifest = self.runner(config, folder, progress, stop_requested=self.stop_event.is_set, **extra)
            state, error = manifest["state"], "; ".join(manifest.get("issues", []))
        except Exception as exc:
            state, error = "failed", f"{type(exc).__name__}: {exc}"
        summary = run_summary(folder)
        with self.lock:
            self.job.update(state=state, error=error, summary=summary)

    def stop(self):
        self.stop_event.set()
        return self.state()

    def state(self):
        with self.lock:
            job = json.loads(json.dumps(self.job)) if self.job else None
        return dict(job=job, busy=self.busy(), etabs_executable=self.etabs, etabs_found=Path(self.etabs).is_file(),
                    out_dir=str(self.out_dir), installed=installed_etabs())

    def open_folder(self, body):
        folder = Path(body.get("folder", "")).resolve()
        if self.out_dir.resolve() not in (folder, *folder.parents) or not folder.is_dir():
            raise ValueError("Only run folders inside the output folder can be opened")
        os.startfile(folder)  # Windows Explorer
        return dict(opened=str(folder))


def make_handler(lab, token, server_holder):
    page = files("beam_lab").joinpath("web/index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, body, kind="application/json"):
            data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind+"; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _allowed(self):
            host = self.headers.get("Host", "").rsplit(":", 1)[0]
            lab.last_seen = time.monotonic()
            return host in ("127.0.0.1", "localhost")

        def do_GET(self):
            path = urlparse(self.path).path
            if not self._allowed():
                return self._send(403, {"error": "Local use only"})
            if path == "/":
                return self._send(200, page, "text/html")
            if self.headers.get("X-Lab-Token") != token:
                return self._send(403, {"error": "Missing page token"})
            routes = {"/api/presets": lab.presets, "/api/state": lab.state, "/api/runs": lambda: list_runs(lab.out_dir)}
            if path in routes:
                return self._send(200, routes[path]())
            self._send(404, {"error": "Not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._allowed() or self.headers.get("X-Lab-Token") != token:
                return self._send(403, {"error": "Local page only"})
            length = int(self.headers.get("Content-Length", "0"))
            if length > 100_000:
                return self._send(413, {"error": "Request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                routes = {"/api/preview": lab.preview, "/api/start": lab.start, "/api/settings": lab.save_settings,
                          "/api/open": lab.open_folder, "/api/stop": lambda body: lab.stop()}
                if path == "/api/quit":
                    if lab.busy():
                        raise ValueError("Stop the run first, then close")
                    self._send(200, {"closing": True})
                    threading.Thread(target=server_holder[0].shutdown, daemon=True).start()
                    return
                if path not in routes:
                    return self._send(404, {"error": "Not found"})
                self._send(200, routes[path](body))
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self._send(400, {"error": str(exc)})

    return Handler


def serve(lab=None, port=8765, open_browser=True):
    """Start the page server; returns (server, url). Tries a few ports if the first is busy."""
    lab = lab or Lab()
    token, holder = secrets.token_urlsafe(24), []
    handler = make_handler(lab, token, holder)
    for candidate in [*range(port, port+20), 0]:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError:
            continue
    holder.append(server)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if open_browser:
        webbrowser.open(url)
    return server, url


def close_when_forgotten(server, lab, idle_seconds=600, check_seconds=30):
    """There is no window: if no page has polled for a while and ETABS is idle, stop the server."""
    def watch():
        while True:
            time.sleep(check_seconds)
            if not lab.busy() and time.monotonic()-lab.last_seen > idle_seconds:
                server.shutdown()
                return
    threading.Thread(target=watch, daemon=True).start()


def main(argv=None):
    import sys
    lab = Lab()
    server, url = serve(lab, open_browser="--no-browser" not in (sys.argv[1:] if argv is None else argv))
    close_when_forgotten(server, lab)
    print(f"ETABS Beam Lab web screen: {url}  (close it from the page)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
