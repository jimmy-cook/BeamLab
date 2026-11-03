# ETABS Beam Lab: handoff summary

Paste this file into a new Claude chat to continue the work.

## About me
- I'm a structural engineer, not an expert programmer.
- Please explain things simply, step by step, with pictures.

## The project
- ETABS Beam Lab (version 0.7) is a Python and Tkinter app. It builds continuous RC beam models in ETABS 22 through COM, runs ACI 318-19 design, and exports CSV datasets for ANN training.
- Folder: `ETABS-Beam-Lab\ETABS Beam Lab`
- Main files:
  - `beam_lab/sampling.py`: random study generator
  - `beam_lab/config.py`: input rules and checks
  - `beam_lab/pipeline.py`: runs each model in ETABS
  - `beam_lab/gui.py`: the app window
  - `beam_lab/ann_export.py`: ANN CSV files
- Tests: `py -3.13 -m pytest -q` (153 pass). Use Python 3.13, because the Python 3.11 install is broken.
- The exe is `ETABS Beam Lab 0.7.exe`. It is built from a clean venv with only comtypes and pyinstaller.

## Goal
Train an ANN that **predicts the beam size** (b × h) for a continuous beam line from its spans, loads and materials.

## Sampling rules already built (keep these)
1. **Span length:** 3–12 m in 250 mm steps (37 lengths). There is an optional limit on the ratio between neighbouring spans (0 = off).
2. **Beam size:** width 300–900 mm and depth 350–1200 mm, in 25 mm steps, with **0.3h ≤ b ≤ h**. That leaves 607 allowed sizes.
3. **No deep beams:** the span must be more than 4h (ACI 318-19 §9.9.1.1). Failing models are skipped before ETABS.
4. **Loads and materials:** D 10–150 kN/m (superimposed; ETABS adds self-weight), L 10–150 kN/m with **0.25 ≤ L/D ≤ 1.5** (D drawn evenly first, then L evenly within its allowed band), f'c 20–50 MPa, fy 200–600 MPa, stirrup fyt 200–420 MPa (each continuous and drawn separately; fyt ≤ 420 MPa per ACI 318-19 Table 20.2.2.4(a), shear: stirrups). One D and one L value apply to every span in a line.
5. **Limits:** at most 10,000 ETABS models per study and at most 1,000 beam lines per study.

## Decisions so far (Nov 2026)
- **Span variation plan:** Phase 1 = equal spans. Phase 2 = unequal spans with neighbouring span ratio limit 1.5 (main study), plus optionally a small 2.0 study as a margin. Combine all CSVs for training. Use a different seed per study.
- **Sizing method: smart size search (built).** Tick "Smart size search" in the sampling dialog. For each beam line and each search width (default 300, 400, 500, 600 mm) it runs the middle depth in ETABS, then halves: PASS → try shallower, FAIL → try deeper, until the smallest passing depth is found (at most 21 ETABS runs per line, about 476 lines per 10,000-model study). `ann_size.csv` keeps the smallest-area passing size over the widths. `size_search.csv` logs every probe.
  - Code: `smallest_passing` in `beam_lab/pipeline.py`; `size_search` flag in `Config`; `search_sections` in `beam_lab/sampling.py`; tests in `tests/test_size_search.py`.
  - It assumes a deeper beam (same width) never fails where a shallower one passes.
- **Self-weight (decided Nov 2026):** sampled D is **superimposed** dead load (slab, finishes, walls) and ETABS adds the beam self-weight (multiplier 1, 24 kN/m³). A heavier beam therefore carries its own weight, and the ANN input D does not depend on the size. Optional old behaviour: tick "D already includes beam self-weight" in the sampling dialog.
- **Load pairing (decided Nov 2026):** L/D limits 0.25–1.5 (light use with walls up to assembly and light storage), from typical floor loads: engineering judgement, to be checked against the local loading code. Pattern live loading (ACI 318-19 §6.4) was discussed and **not** added for now; the research preset still uses full 1.2D + 1.6L on all spans.
- **Materials (decided Nov 2026):** continuous sampling, as in Hong (2023, Ch. 7: fy 500–600, f'c 30–50 MPa, flexure only; that chapter leaves shear design to a future book). This study also designs shear, so stirrups get their own range capped at 420 MPa. Old studies (including the Excel pilot, whose fyt reaches 514 MPa) still load unchanged.
- **Loads per span (decided Nov 2026):** keep the **same D and L on all 3 spans for now**. Later (optional Phase 3): let D differ between spans with neighbouring ratio ≤ 1.5 (a real check: 10 ft vs 4 ft brick walls on a 4 m slab strip give 37.9 vs 28.3 kN/m, ratio 1.34; ±20% around the average was too tight), keep L the same on all spans, keep 0.25 ≤ L/D ≤ 1.5 per span. Same-load data stays reusable (D1 = D2 = D3). Needs: sampler option, relaxing the equal-load check for the research preset (config.py), and per-span D/L columns in ann_size.csv.

- **Web screen (built Nov 2026):** `ETABS Beam Lab Web.exe` opens a browser page (127.0.0.1 only, page token): left sidebar with ready-made runs (Smoke, VerifyA, VerifyB, Phase1, Phase2), main area with a live elevation/section view, depth search, last moment diagram and results. Code: `beam_lab/web.py` (stdlib server), `beam_lab/web/index.html`, `beam_lab/research.py` (presets, run-folder names, run summaries). The pipeline sends a `live` block with its progress (not written to files). The desktop app stays for advanced settings.

## Next steps
1. Time one real ETABS model on this PC.
2. Verify the search assumption with `examples/verify_size_search_study.json` (10 beam lines, widths 300/400): run it with "Smart size search" ticked, then unticked, and compare with `py -3.13 -m beam_lab.cli verify-search --search A --full B --out verify_report.csv`. Step-by-step guide: `VERIFY_SIZE_SEARCH.md`.
3. Run the Phase 1 and Phase 2 studies with the smart search.
