import csv
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from beam_lab import web
from beam_lab.pipeline import run
from beam_lab.research import PRESETS, build_study, settings_summary

ROOT = Path(__file__).parent.parent


def fake_etabs(needs=(300, 650)):
    """Three-span fake: a size passes strength when h ≥ needed depth for width 300 (wider sizes pass from 525)."""
    class Fake:
        version = "fake"
        close_error = None
        def __init__(self, *args): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def build(self, scenario, b, h, *args, **kwargs):
            self.spans = scenario.spans_m
            self.passing = h >= (needs[1] if b == needs[0] else 525)
            weight = 24*b*h/1e6 if kwargs["dead_load_basis"] == "superimposed_plus_self_weight" else 0
            dead, live = scenario.dead_kN_m[0]+weight, scenario.live_kN_m[0]
            self.q = {"SLS_D": dead, "SLS_DL": dead+live, "SLS_SUSTAINED": dead, "U_1p2D_1p6L_FULL": 1.2*dead+1.6*live}
            return [f"B{i+1}" for i in range(len(self.spans))], ["U_1p2D_1p6L_FULL"], ["LAB_DEAD", "LAB_LIVE"]
        def analyze(self, cases): return [dict(case=c, status=4) for c in cases]
        def forces(self, frame, combos):
            length = self.spans[int(frame[1:])-1]
            return [dict(object=frame, element=frame, station_m=length*i/20, element_station_m=length*i/20, combination=c,
                         step_type="", step_number=0, P_kN=0, V2_kN=self.q[c]*(length*i/20-length/2), V3_kN=0, T_kNm=0,
                         M2_kNm=0, M3_kNm=self.q[c]*(length*i/20)*(length-length*i/20)/2) for c in combos for i in range(21)]
        def start_design(self): pass
        def design(self, frame):
            length = self.spans[int(frame[1:])-1]
            return [dict(frame=frame, location_m=length*i/20, top_combo="U", top_area_m2=.0008, bottom_combo="U", bottom_area_m2=.0025,
                         shear_combo="U", shear_area_m2_per_m=.0005, torsion_long_combo="U", torsion_long_area_m2=0,
                         torsion_trans_combo="U", torsion_trans_area_m2_per_m=0, error="", warning="") for i in range(21)]
        def combination_designs(self, frames, combos, progress):
            status = None if self.passing else "O/S #3"
            return {combos[0]: dict(designs={f: self.design(f) for f in frames}, flexure=[dict(UniqueName=f, Status=status) for f in frames],
                                    shear=[dict(UniqueName=f, Status=None) for f in frames])}
        def table_catalog(self): return []
    return Fake


@pytest.mark.parametrize("key", ["smoke", "phase1", "phase2"])
def test_sampled_presets_use_the_agreed_settings(key):
    config = build_study(key, lines=3, seed=PRESETS[key]["seed"], root=ROOT)
    assert config.size_search and len(config.scenarios) == 3 and config.serviceability.enabled
    assert config.dead_load_basis == "superimposed_plus_self_weight"
    assert all(s.dead_kN_m[0] == int(s.dead_kN_m[0]) for s in config.scenarios)  # whole numbers by default
    equal = [len(set(s.spans_m)) == 1 for s in config.scenarios]
    assert all(equal) if PRESETS[key]["equal_spans"] else not all(equal)
    text = dict(settings_summary(config))
    assert "0.25 ≤ L/D ≤ 1.5" in text["Loads"] and "fyt 200–420" in text["Materials"] and "L/120" in text["Checks"]


@pytest.mark.parametrize("key,search", [("verify_a", True), ("verify_b", False)])
def test_verification_presets_load_the_saved_study(key, search):
    config = build_study(key, root=ROOT)
    assert config.size_search is search and len(config.scenarios) == 10
    assert config.planned_models() == (110 if search else 600)


def test_live_view_data_reaches_the_screen_but_not_the_run_files(tmp_path):
    seen = []
    config = build_study("smoke", lines=1, seed=1, root=ROOT)
    run(config, tmp_path, seen.append, adapter_factory=fake_etabs())
    live = [m["live"] for m in seen if m.get("live", {}).get("last")]
    assert live and live[-1]["lines_done"] == 1 and live[-1]["line"]["spans_m"] == config.scenarios[0].spans_m
    last = live[-1]["last"]
    assert len(last["diagram"]) == 3 and len(last["diagram"][0]["M_kNm"]) == 21
    assert live[-1]["wins"] and live[-1]["line_answer"]["b"] in (300, 400, 500, 600)
    probes = live[-1]["search"]["probes"]
    assert probes and all(isinstance(ok, bool) for _, ok in probes)
    assert "live" not in json.loads((tmp_path/"manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def server(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(web.os, "startfile", opened.append, raising=False)
    lab = web.Lab(out_dir=tmp_path/"runs", settings_file=tmp_path/"settings.json", adapter_factory=fake_etabs(), root=ROOT)
    srv, url = web.serve(lab, port=0, open_browser=False)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    page = urllib.request.urlopen(url).read().decode("utf-8")
    token = re.search(r'const TOKEN = "([^"]+)"', page).group(1)
    def call(path, body=None, token=token, host=None):
        request = urllib.request.Request(url.rstrip("/")+path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"X-Lab-Token": token, "Content-Type": "application/json", **({"Host": host} if host else {})})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    yield call, lab, opened
    srv.shutdown()


def test_page_requires_its_token_and_local_host(server):
    call, lab, _ = server
    assert call("/api/state", token="wrong")[0] == 403
    assert call("/api/state", host="evil.example")[0] == 403
    status, presets = call("/api/presets")
    assert status == 200 and [p["key"] for p in presets] == list(PRESETS)


def test_a_run_from_the_page_reports_live_progress_and_results(server, tmp_path):
    call, lab, opened = server
    status, preview = call("/api/preview", {"preset": "smoke", "lines": 1, "seed": 1, "run_name": "Smoke"})
    assert status == 200 and preview["folder"].startswith("Smoke EqualSpan UniformLoad ")
    status, state = call("/api/start", {"preset": "smoke", "lines": 1, "seed": 1, "run_name": "Smoke"})
    assert status == 200 and state["job"]["name"].startswith("Smoke EqualSpan")
    assert call("/api/start", {"preset": "smoke", "lines": 1, "seed": 1})[0] in (200, 400)  # second start refused while busy
    for _ in range(200):
        state = call("/api/state")[1]
        if not state["busy"]:
            break
        time.sleep(0.05)
    job = state["job"]
    assert job["state"] == "completed" and job["live"]["lines_done"] == 1
    assert job["summary"]["sizing_rows"] == 1 and sum(job["summary"]["width_wins"].values()) == 1
    status, runs = call("/api/runs")
    assert [r["name"] for r in runs] == [job["name"]]
    assert call("/api/open", {"folder": job["folder"]})[0] == 200 and opened
    assert call("/api/open", {"folder": str(tmp_path)})[0] == 400


def test_a_forgotten_server_closes_itself_only_when_idle(tmp_path):
    import threading
    lab = web.Lab(out_dir=tmp_path, settings_file=tmp_path/"s.json", root=ROOT)
    srv, _ = web.serve(lab, port=0, open_browser=False)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    lab.job = dict(state="running")
    lab.last_seen -= 100
    web.close_when_forgotten(srv, lab, idle_seconds=1, check_seconds=0.05)
    time.sleep(0.3)
    assert thread.is_alive()  # ETABS busy: keep serving
    lab.job["state"] = "completed"
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_settings_reject_a_wrong_etabs_path(server):
    call, lab, _ = server
    assert call("/api/settings", {"etabs_executable": r"C:\\nothing\\notepad.exe", "out_dir": "runs"})[0] == 400
    status, state = call("/api/settings", {"etabs_executable": r"D:\\CSI\\ETABS.exe", "out_dir": str(lab.out_dir)})
    assert status == 200 and state["etabs_executable"].endswith("ETABS.exe") and not state["etabs_found"]


@pytest.mark.parametrize("key", ["smoke", "phase2"])
def test_span_type_can_be_chosen_on_the_page(key):
    unequal = build_study(key, lines=5, seed=7, root=ROOT, equal_spans=False, ratio=1.2)
    assert not all(len(set(s.spans_m)) == 1 for s in unequal.scenarios)
    assert unequal.sampling_settings["max_adjacent_span_ratio"] == 1.2
    for s in unequal.scenarios:
        assert all(max(a, b)/min(a, b) <= 1.2 + 1e-9 for a, b in zip(s.spans_m, s.spans_m[1:]))
    equal = build_study(key, lines=5, seed=7, root=ROOT, equal_spans=True, ratio=1.2)
    assert all(len(set(s.spans_m)) == 1 for s in equal.scenarios)
    with pytest.raises(ValueError):
        build_study(key, lines=5, seed=7, root=ROOT, equal_spans=False, ratio=9)


def closable_etabs(close_on_build=None, fail_on_build=None):
    """fake_etabs whose process 'closes' (stops answering) at the given build number, counted over all instances."""
    base, started, builds = fake_etabs(), [], [0]
    class Closable(base):
        def __init__(self, *args):
            started.append(self)
            self.alive = True
        def responding(self):
            return self.alive
        def build(self, *args, **kwargs):
            builds[0] += 1
            if builds[0] == close_on_build:
                self.alive = False
            if not self.alive or builds[0] == fail_on_build:
                raise OSError("Object reference not set to an instance of an object.")
            return super().build(*args, **kwargs)
    return Closable, started


def test_a_closed_etabs_is_restarted_and_the_model_repeated(tmp_path):
    config = build_study("smoke", lines=2, seed=1, root=ROOT)
    factory, started = closable_etabs(close_on_build=5)
    manifest = run(config, tmp_path, adapter_factory=factory)
    assert manifest["state"] == "completed" and manifest["etabs_restarts"] == 1 and len(started) == 2
    assert "ETABS not answering); restarted ETABS and repeated the model" in manifest["issues"][0]
    runs = list(csv.DictReader((tmp_path/"runs.csv").open(encoding="utf-8-sig")))
    assert all(r["status"] == "complete" for r in runs) and len({r["candidate_id"] for r in runs}) == len(runs)
    forces = list(csv.DictReader((tmp_path/"forces.csv").open(encoding="utf-8-sig")))
    assert len({r["force_row_id"] for r in forces}) == len(forces)  # the repeated model left no duplicate rows
    assert len(list(csv.DictReader((tmp_path/"ann_size_wide.csv").open(encoding="utf-8-sig")))) == 2


def test_a_model_error_while_etabs_still_answers_is_repeated_in_a_fresh_etabs(tmp_path):
    # ETABS can answer GetVersion yet hold a broken model, so any failed model gets one retry in a new ETABS.
    config = build_study("smoke", lines=1, seed=1, root=ROOT)
    factory, started = closable_etabs(fail_on_build=2)
    manifest = run(config, tmp_path, adapter_factory=factory)
    assert manifest["state"] == "completed" and manifest["etabs_restarts"] == 1 and len(started) == 2
    assert "ETABS answering" in manifest["issues"][0]


def test_restarts_stop_after_three_in_a_row_without_a_completed_model(tmp_path):
    config = build_study("smoke", lines=1, seed=1, root=ROOT)
    base, started = closable_etabs()
    class Broken(base):
        def build(self, *args, **kwargs):
            raise OSError("Object reference not set to an instance of an object.")
    manifest = run(config, tmp_path, adapter_factory=Broken)
    assert manifest["state"] == "completed_with_failures" and manifest["etabs_restarts"] == 3 and len(started) == 4


@pytest.mark.parametrize("closed_before_end", [True, False])
def test_an_output_file_open_in_excel_does_not_stop_the_run(tmp_path, monkeypatch, closed_before_end):
    import beam_lab.pipeline as pipeline
    monkeypatch.setattr(pipeline, "LOCKED_FILE_WAIT_SECONDS", 0)
    real, calls = pipeline.write_csv, [0]
    def excel_holds_ann_beams(path, rows, *args):
        if path.name == "ann_beams.csv":
            calls[0] += 1
            if calls[0] > 3 and not (closed_before_end and calls[0] > 6):
                raise PermissionError(13, "Permission denied", str(path))
        return real(path, rows, *args)
    monkeypatch.setattr(pipeline, "write_csv", excel_holds_ann_beams)
    config = build_study("smoke", lines=1, seed=1, root=ROOT)
    manifest = run(config, tmp_path, adapter_factory=fake_etabs())
    assert manifest["state"] == "completed" and manifest["current_stage"] == "Finished"
    assert any("ann_beams.csv was open in another program" in issue for issue in manifest["issues"])
    final = tmp_path/("ann_beams.csv" if closed_before_end else "ann_beams (saved while locked).csv")
    rows = list(csv.DictReader(final.open(encoding="utf-8-sig")))
    assert len(rows) == 3*manifest["completed_candidates"]  # every span of every model is in the final file


def test_ann_size_has_span_features_and_the_selected_sections_rebar(tmp_path):
    config = build_study("smoke", lines=3, seed=7, root=ROOT, equal_spans=False, ratio=1.5)
    run(config, tmp_path, adapter_factory=fake_etabs())
    size = list(csv.DictReader((tmp_path/"ann_size_wide.csv").open(encoding="utf-8-sig")))
    beams = list(csv.DictReader((tmp_path/"beams.csv").open(encoding="utf-8-sig")))
    assert size
    for row in size:
        spans = [float(row[f"span_{i}_mm"]) for i in (1, 2, 3)]
        assert row["span_arrangement"] == ("equal" if len(set(spans)) == 1 else "unequal") and row["equal_spans"] == str(int(len(set(spans)) == 1))
        assert float(row["span_ratio_2_1"]) == pytest.approx(spans[1]/spans[0], abs=1e-4)
        assert float(row["span_ratio_3_2"]) == pytest.approx(spans[2]/spans[1], abs=1e-4)
        assert float(row["max_adjacent_span_ratio"]) == pytest.approx(max(max(a, b)/min(a, b) for a, b in zip(spans, spans[1:])), abs=1e-4)
        assert float(row["max_span_mm"]) == max(spans) and float(row["total_length_mm"]) == sum(spans)
        D, L = float(row["dead_input_kN_m"]), float(row["live_kN_m"])
        assert float(row["wu_entered_kN_m"]) == pytest.approx(1.2*D + 1.6*L) and float(row["live_dead_ratio"]) == pytest.approx(L/D, abs=1e-4)
        chosen = {int(b["span_index"])+1: b for b in beams if b["candidate_id"] == row["selected_candidate_id"]}
        assert len(chosen) == 3
        assert row["dead_input_kN_m"] == chosen[1]["dead_input_kN_m"] and row["live_kN_m"] == chosen[1]["live_kN_m"]
        assert row["target_self_weight_kN_m"] == chosen[1]["self_weight_kN_m"]
        assert row["target_dead_total_kN_m"] == chosen[1]["dead_total_kN_m"] and row["target_Wu_kN_m"] == chosen[1]["Wu_kN_m"]
        for n, b in chosen.items():
            assert row[f"target_span{n}_As_bottom_mm2_mid"] == b["As_bottom_mm2_mid"]
            assert row[f"target_span{n}_Av_over_s_mm2_per_m_left"] == b["Av_over_s_mm2_per_m_left"]
        assert float(row["target_max_As_bottom_mm2"]) == max(float(b[f"As_bottom_mm2_{p}"]) for b in chosen.values() for p in ("left", "mid", "right"))
    inputs = [c for c in size[0] if not c.startswith("target_")]
    assert not any("self_weight" in c or c in ("Wu_kN_m", "b_mm", "h_mm") for c in inputs)  # nothing that depends on the answer


def test_ann_beams_records_equal_or_unequal_spans_beside_the_span_position(tmp_path):
    config = build_study("smoke", lines=3, seed=7, root=ROOT, equal_spans=False, ratio=1.5)
    run(config, tmp_path, adapter_factory=fake_etabs())
    rows = list(csv.DictReader((tmp_path/"ann_beams.csv").open(encoding="utf-8-sig")))
    for row in rows:
        equal = len({row[f"span_{i}_mm"] for i in (1, 2, 3)}) == 1
        assert row["span_arrangement"] == ("equal" if equal else "unequal") and row["equal_spans"] == str(int(equal))
        assert row["span_type"] in ("end_left", "interior", "end_right")


def test_a_beam_line_cut_short_gets_no_ann_size_row(tmp_path):
    # Stopping (or a crash) part-way through a line must not export a size that may not be its smallest yet.
    config = build_study("smoke", lines=2, seed=1, root=ROOT)
    models = []
    def stop_after_line_one_plus_two_models():
        return len(models) >= 23
    class Counting(fake_etabs()):
        def build(self, *args, **kwargs):
            models.append(1)
            return super().build(*args, **kwargs)
    manifest = run(config, tmp_path, adapter_factory=Counting, stop_requested=stop_after_line_one_plus_two_models)
    assert manifest["state"] == "stopped"
    size = list(csv.DictReader((tmp_path/"ann_size_wide.csv").open(encoding="utf-8-sig")))
    searched = {r["scenario_id"] for r in csv.DictReader((tmp_path/"beams.csv").open(encoding="utf-8-sig"))}
    assert len(searched) == 2 and [r["scenario_id"] for r in size] == [config.scenarios[0].scenario_id]


def test_every_size_mode_exports_only_lines_whose_sizes_all_ran(tmp_path):
    from beam_lab.config import Config
    data = json.loads((ROOT/"examples/verify_size_search_study.json").read_text(encoding="utf-8"))
    data.update(scenarios=data["scenarios"][:2], sections_mm=[[300, 650], [300, 900], [400, 800]], size_search=False, max_models=6)
    config = Config.parse(data)
    factory, _ = closable_etabs()
    class SmallestSizeOfLineTwoFails(factory):
        def build(self, scenario, b, h, *args, **kwargs):
            if scenario.scenario_id == config.scenarios[1].scenario_id and (b, h) == (300, 650):
                raise OSError("ETABS error")
            return super().build(scenario, b, h, *args, **kwargs)
    run(config, tmp_path, adapter_factory=SmallestSizeOfLineTwoFails)
    size = {r["scenario_id"] for r in csv.DictReader((tmp_path/"ann_size_wide.csv").open(encoding="utf-8-sig"))}
    # Line two's larger sizes pass, but its smallest size never ran, so its true minimum is unknown.
    assert size == {config.scenarios[0].scenario_id}


def test_ann_files_use_beams_csv_names_and_one_number_format(tmp_path):
    config = build_study("smoke", lines=2, seed=7, root=ROOT, equal_spans=False, ratio=1.5)
    run(config, tmp_path, adapter_factory=fake_etabs())
    read = lambda name: list(csv.DictReader((tmp_path/name).open(encoding="utf-8-sig")))
    beams = {(r["candidate_id"], r["beam_id"]): r for r in read("beams.csv")}
    shared_names = set(next(iter(beams.values())))
    for name in ("ann_beams.csv", "ann_deflection.csv"):
        rows = read(name)
        common = shared_names & set(rows[0])
        assert {"dead_input_kN_m", "dead_total_kN_m", "live_kN_m", "self_weight_kN_m", "b_mm", "span_1_mm"} <= common
        for row in rows:  # a column that exists in beams.csv means exactly the same there
            source = beams[(row["candidate_id"], row["beam_id"])]
            assert all(row[c] == source[c] for c in common if c != "sample_id"), name
    for row in read("ann_size_wide.csv"):
        chosen = {r["beam_id"]: r for (cid, _), r in beams.items() if cid == row["selected_candidate_id"]}
        for c in row:  # an output is target_ + the beams.csv name, with the same value
            base = c.removeprefix("target_")
            if c.startswith("target_span"):
                span, base = base.split("_", 1)
                assert row[c] == chosen["B" + span.removeprefix("span")][base]
            elif base in shared_names and c not in ("target_b_mm", "target_h_mm"):
                assert row[c] == chosen["B1"][base], c
        assert row["target_b_mm"] == chosen["B1"]["b_mm"] and row["target_h_mm"] == chosen["B1"]["h_mm"]
    for name in ("beams.csv", "ann_beams.csv", "ann_size.csv", "ann_size_wide.csv", "ann_deflection.csv", "ann_stations.csv"):
        text = (tmp_path/name).read_text(encoding="utf-8-sig")
        assert not re.search(r"(^|,)-?\d+\.0(,|\r?$)", text, re.M), f"{name} writes a whole number with .0"
    old = {"D_kN_m", "L_kN_m", "physical_self_weight_kN_m", "section_status", "As_bot_mm2", "Asv_mm2_per_m"}
    for name in ("ann_beams.csv", "ann_size.csv", "ann_size_wide.csv", "ann_deflection.csv", "ann_stations.csv"):
        header = set(read(name)[0])
        assert not header & old and not any(c.startswith(("As_bot_", "Asv_")) or "_Av_s_" in c for c in header), name


def test_ann_size_is_ann_beams_rows_of_the_selected_size_one_span_per_row(tmp_path):
    config = build_study("smoke", lines=2, seed=7, root=ROOT, equal_spans=False, ratio=1.5)
    run(config, tmp_path, adapter_factory=fake_etabs())
    read = lambda name: list(csv.DictReader((tmp_path/name).open(encoding="utf-8-sig")))
    ann_beams, size, wide = read("ann_beams.csv"), read("ann_size.csv"), read("ann_size_wide.csv")
    extra = ["span_ratio_2_1", "span_ratio_3_2", "max_adjacent_span_ratio", "max_span_mm", "min_span_mm", "total_length_mm",
             "max_min_span_ratio", "end_to_interior_span_ratio", "live_dead_ratio", "wu_entered_kN_m", "candidates_tested"]
    assert wide and list(size[0]) == list(ann_beams[0]) + extra  # every ann_beams column, same order, then the extras
    assert len(size) == 3*len(wide)
    by_sample = {r["sample_id"]: r for r in ann_beams}
    for line in wide:
        rows = [r for r in size if r["scenario_id"] == line["scenario_id"]]
        assert [r["beam_id"] for r in rows] == ["B1", "B2", "B3"]
        for r in rows:  # the selected size of that line, identical to its ann_beams.csv row
            assert r["candidate_id"] == line["selected_candidate_id"] and r["strength_and_sls_status"] == "PASS"
            assert (r["b_mm"], r["h_mm"]) == (line["target_b_mm"], line["target_h_mm"])
            assert all(r[c] == by_sample[r["sample_id"]][c] for c in ann_beams[0])
            assert all(r[c] == line[c] for c in extra if c in line)


def test_ann_size_train_has_only_size_independent_inputs_and_b_h(tmp_path):
    config = build_study("smoke", lines=2, seed=7, root=ROOT, equal_spans=False, ratio=1.5)
    run(config, tmp_path, adapter_factory=fake_etabs())
    read = lambda name: list(csv.DictReader((tmp_path/name).open(encoding="utf-8-sig")))
    size, train = read("ann_size.csv"), read("ann_size_train.csv")
    assert train and len(train) == len(size)
    assert list(train[0])[-2:] == ["b_mm", "h_mm"]
    leaks = ("sls_", "self_weight", "dead_total", "Wu_kN_m", "Mu_", "Vu_", "Pu_", "As_", "Av_", "section_", "candidate")
    assert not [c for c in train[0] if c.startswith(leaks) or any(x in c for x in leaks[1:4])]
    for t, s in zip(train, size):
        assert all(t[c] == s[c] for c in t)
