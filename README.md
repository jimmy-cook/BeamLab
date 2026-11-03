# ETABS Beam Lab 0.7.0

Open **ETABS Beam Lab 0.7.exe**. Enter studies through the native high-DPI interface. Python is bundled; a licensed ETABS 22 installation is required. Results export to CSV. No large collection starts automatically.

## Built-in Excel pilot

Click **Load Excel pilot: 100 × 20** on Beam scenarios to load the exact workbook inputs and review the plan. It loads 100 three-equal-span scenarios and 20 shared sections: 2,000 candidate models / 6,000 beam rows. Simple immediate DL L/120 and total D+L L/240 checks are enabled. Click **Generate CSV dataset** separately to run ETABS.

38 candidate inputs violate total D ≥ beam self-weight and are excluded from analysis. The app retains all 6,000 planned rows, with 5,886 beam rows eligible before analysis/design filtering. It does not fabricate PASS/FAIL or reinforcement values. The source inputs are frozen so changes to the random sampler cannot change this workbook plan. See study_plan/README.md and the included Excel workbook.

## Current research setup

| Parameter | Range / setting |
|---|---|
| Beam line | Three equal spans by default |
| Span length | 3000–12000 mm, 250 mm sampling grid (3.00, 3.25 … 12.00 m) |
| Dead D: superimposed (slab, finishes, walls); ETABS adds beam self-weight | 10–150 kN/m |
| Live L | 10–150 kN/m, paired with D so that 0.25 ≤ L/D ≤ 1.5 |
| Concrete fc | 20–50 MPa |
| Longitudinal steel fy | 200–600 MPa |
| Stirrup steel fyt | 200–420 MPa, sampled separately (ACI 318-19 shear limit 420 MPa) |
| Width b | 300–900 mm, 25 mm sampling grid |
| Section shape | 0.3h ≤ b ≤ h by default (607 of 875 grid pairs) |
| Span ÷ depth | span > 4h; ACI 318-19 §9.9.1.1 deep beams (span ≤ 4h) are marked invalid before ETABS |
| Depth h | 350–1200 mm, 25 mm sampling grid |
| Strength combination | 1.2D+1.6L, every span fully loaded |
| Deflection limits | Editable user-defined DL L/120 and Total L/240 starting values |
| Total deflection basis | Immediate D+L by default; long-term total available |

The same D and L apply to every span. In sampled studies D is superimposed dead load by default: ETABS adds each candidate's self-weight (24 kN/m³ × b × h), so a deeper beam carries its own extra weight and the size-independent D is the sizing input. Ticking "D already includes beam self-weight" in the sampling dialog keeps the earlier interpretation: D is the total, ETABS self-weight is off, and candidates whose own weight exceeds D are excluded as invalid inputs, not labeled structural failures. The Excel pilot keeps that total-D interpretation. The research preset does not add 1.4D or alternate live-load patterns.

The sampler draws span/load/material combinations within the ranges. D is drawn evenly over its range, then L evenly within the band the live/dead ratio limits allow for that D (default 0.25 ≤ L/D ≤ 1.5, covering light-use floors with walls up to assembly and light storage; 0 removes a limit), so unrealistic pairs such as D 12 with L 140 do not occur and light and heavy dead loads are equally represented. D/L/fc/fy are not restricted to 1-unit increments by default; values are stored to three decimals. Span lengths lie on the span grid (default 250 mm, endpoints included; span step 1 mm with both b/h limits set to 0 reproduces 0.7.0 studies). When spans may differ, an optional maximum neighbouring span ratio (longer ÷ shorter, 0 = no limit) is applied, and every allowed arrangement is equally likely. Section pairs lie on the selected grid, with endpoints included, and must satisfy the width-to-depth rule 0.3h ≤ b ≤ h by default: wide shallow (band) beams with b > h and very thin sections with b < 0.3h are excluded. Each limit is a sampling setting (0 removes it) and is saved with the study; Config rejects hand-entered sections outside the study rule. Each span must also exceed 4h (setting "span must exceed this × h", 0 = off): a candidate with any deep-beam span is marked input-invalid before ETABS, with the reason in input_validation_error and runs.csv, and never reaches a training file. Older studies and the frozen Excel pilot record no limits and load unchanged. Seed and settings are saved. Six shared candidate sections are sampled by default; the full grid has 875 pairs, of which 607 follow the default rule. For sizing targets, the optional smart size search instead lists every allowed depth of a few widths and, per beam line, halves the depth range in ETABS until the smallest passing depth is found (at most 21 models per line by default; see PARAMETER_VARIATIONS.md). A study cannot exceed 10000 candidate models. The integer-only option is optional and off by default.

## Workflow and outputs

Follow START_HERE.md. **ETABS Beam Lab Web.exe** is the simplest way to generate data: a browser page (this PC only) with ready-made runs in a sidebar and a live elevation, section, depth-search and moment view of each model while ETABS works. **ETABS Beam Lab 0.7.exe** remains the full desktop window. The simplified Deflection Limits panel has two divisors and a total-basis selector. Long-term mode requires sustained live percentage and duration. Legacy ACI-category checks remain under Advanced settings and retain their separate status fields.

| File | Purpose |
|---|---|
| ann_size.csv | The selected size of each beam line, one row per span: every ann_beams.csv column (same order) for the smallest size that passes analysis, design, flexure, shear and deflection on all spans, plus span ratios, L/D, wu_entered_kN_m and candidates_tested |
| ann_size_train.csv | ann_size.csv cut to training columns: IDs and span position, size-independent inputs (spans, D as entered, L, f′c, fy, fyt, span ratios, L/D, wu_entered_kN_m), then the outputs b_mm, h_mm |
| ann_size_wide.csv | The same answers as one row per beam line (spans side by side; outputs prefixed target_) |
| ann_beams.csv | Beam/candidate inputs, Mu/Vu/Pu, nine required reinforcement targets, strength and deflection labels |
| ann_deflection.csv | Compact DL/Total deflection inputs, limits, results, utilization and separate/combined labels |
| ann_stations.csv | Concurrent station demands and matching ETABS-required reinforcement |
| beams.csv, forces.csv | Extended beam features and original strength force vectors |
| design_stations.csv | ETABS-required areas, stations, governing combinations and messages |
| combination_design_stations.csv | Original required reinforcement for individually selected design combinations |
| combination_checks.csv, station_checks.csv | ETABS section feasibility and station evidence |
| service_forces.csv | Original ETABS unfactored service-combination forces |
| deflection_checks.csv, deflection_stations.csv | Per-criterion checks and signed deflection curves |
| size_targets_sls.csv | Sizing audit, including unresolved scenarios with blank targets |
| size_targets.csv | Earlier strength-only sizing audit; use ann_size.csv for the requested all-pass training targets |
| candidate_plan.csv, runs.csv | Input validity, execution status, errors and model paths (smart size search: only the models it ran) |
| size_search.csv | Smart size search only: per beam line and width, the probes, ETABS runs and smallest passing depth |

Top/bottom areas are mm²; shear Av/s is mm²/m, named Asv in ANN outputs. All values come automatically from ETABS GetSummaryResultsBeam. No provided-reinforcement inputs, discrete bar schedules or arbitrary selected-bar checks are added in this release.

CSV uses UTF-8 BOM, commas, decimal points and units in column names. Missing values remain blank. Each run has its own folder, exact config.json, manifest.json with CSV hashes and ETABS models. All candidates/spans from the same structural scenario share a train/validation/test split. Service-setting fingerprints distinguish different limit assumptions without splitting related scenarios across training groups.

## Training interpretation

See PARAMETER_VARIATIONS.md. Size targets are the minimum tested concrete volume passing every implemented criterion on every span; they are not a proven global optimum. With uniform sections, tie-breaking favours lower depth then width. Multiple passing candidate sizes do not become conflicting targets for one line.

Reinforcement regression uses reinforcement_regression_eligible=1 in ann_beams.csv. Deflection regression/classification uses ann_deflection.csv with classification_eligible=1. Failures, REVIEW and missing evidence are retained for auditing. Only actual PASS/FAIL maps to 1/0; unresolved results stay blank. Do not use target-derived status, areas, utilization or calculated stiffness as predictors unless that information is also available during the intended prediction workflow.

The complete span arrangement and zero-based span_index are exported. Beam Mu/Vu/Pu maxima may occur at different stations; use station results for simultaneous vectors. Pu and Tu are normally zero in this planar vertical-load population and do not provide training diversity for those actions.

## Scope and verification

The RC deflection calculation retains the existing approximate ACI midspan effective-inertia method, using real ETABS service moments and idealized required steel. Its Python-computed deflections are not raw ETABS concrete-design deflection results. Simple mode compares immediate D and the selected total curve against your entered limits; it does not issue an ACI-category acceptance label. See DEFLECTION_GUIDE.md.

203 tests passed (including span-grid, neighbouring-ratio, width-to-depth, deep-beam, ETABS restart, locked-file, finished-line and shared-column rules). One actual three-equal-span ETABS 22.5.0 sample completed; its re-exported training files were checked against unchanged original force/reinforcement data and independent moment-area quadrature. See VALIDATION.md and sample_simple_3span/simple_validation.csv. No exhaustive range collection was run.

Straight rectangular normal-weight, nonprestressed gravity beams, ideal point supports, uniform section per line and analytical centre-to-centre spans are the supported population. Complete detailing, development/laps, staged construction, support movement, nonlinear cracking redistribution, deep beams, general axial/torsion behaviour and full project/code acceptance are outside this scope. full_aci_verified remains 0.

## Developer use

Optional Python 3.10+ setup: install .[api,test]. Run `python -m beam_lab.cli generate --config examples/simple_deflection_sample.json --out runs/new_sample`. The local REST API offers /example, /plan, /jobs, status and CSV downloads, plus /docs. Use one worker; COM jobs are serialized. Public hosting and crash resumption are not implemented. Stopping preserves completed candidates.

Old saved studies retain their explicit conventions and their ACI-category mode when no new limit_mode was recorded. New UI studies use user_defined mode. Configurations without explicit loading conventions retain legacy defaults; do not mix them with this research population without filtering.

## Folder for sharing

Send this entire ETABS Beam Lab folder or ETABS-Beam-Lab.zip. It contains one current Windows executable, Python source (beam_lab), regression tests (tests), four relevant saved-study examples, the Excel pilot (study_plan), one validated ETABS sample (sample_simple_3span), engineering guides and runtime license notices. The app needs licensed ETABS 22 on the receiving PC; Python and Excel are not needed to use the executable.

Temporary work files, obsolete executables/packages, old sample versions, generated caches and redundant examples were removed. Previous completed runs/datasets are retained separately in outputs/retained-results. The active preview collection remains in outputs/etabs-beam-lab/runs, with its runner, and is excluded from the sharing package. RELEASE_CONTENTS.sha256 records the delivered file hashes.
