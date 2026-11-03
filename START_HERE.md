# Start ETABS Beam Lab

## Easiest: the web screen

Double-click **ETABS Beam Lab Web.exe**. Your browser opens the research screen (only on this PC):

1. **Left sidebar:** choose the run (Smoke test, Verification A or B, Phase 1, Phase 2), check the number of beam lines, seed and run name. "Load and material ranges" sets the min and max of D, L, L/D, f′c, fy, fyt and span length (defaults: the research ranges; changed boxes are highlighted, "Reset to defaults" restores them). "Study settings" lists the loads, materials, sizes and checks; "ETABS and output folder" shows whether ETABS 22 was found.
2. Click **Start data generation**. The main area shows the beam line ETABS is working on, live: elevation with span dimensions, supports and loads; the tested section to true scale with b and h; the depth search; the moment diagram and PASS/FAIL of the last model; and the winning widths so far.
3. **Stop after current model** keeps everything finished so far. **Previous runs** opens any run folder. **Close program** (top right) ends it; if you only close the browser tab, it closes itself after 10 minutes unless ETABS is still running.

Run folders are named like `Phase1 EqualSpan UniformLoad 04Nov 0930`.

## Full control: the desktop window

Open **ETABS Beam Lab 0.7.exe**. Python is bundled; licensed ETABS 22 is required. CSV files are the final outputs.

1. **Beam scenarios:** start with the three equal 6 m spans, or enter your own geometry, D/L and materials. The same D/L act on every span. Sampled studies treat D as superimposed dead load and ETABS adds the beam self-weight; the Candidate sizes tab shows which dead-load basis a study uses.
2. **Candidate sizes:** add sections. **Sample many scenarios…** defaults to three equal spans, values sampled within your ranges, a 25 mm section grid limited to 0.3h ≤ b ≤ h, and a 250 mm span grid. Candidates whose span is 4h or less (ACI deep beams) are shown as "Deep beam" in the preview and are not analysed. Untick equal spans for unequal lines; set a maximum neighbouring span ratio (e.g. 1.5) to exclude unrealistic arrangements. The live/dead ratio row (default 0.25–1.5) keeps D and L in realistic pairs. For an ANN that predicts beam size, tick **Smart size search**: for each search width it finds the smallest passing depth in about 5–6 ETABS runs instead of testing random sizes. The same box on Candidate sizes switches a loaded study between the search and running every listed size.
3. **Design checks:** enter **DL Deflection Limit, L/** and **Total Deflection Limit, L/**. Starting values are 120 and 240. Choose immediate D+L or long-term total. These are user-defined limits, not universal ACI limits.
4. For long-term total, Advanced settings opens automatically; enter sustained live-load % and duration. The immediate default needs no advanced settings.
5. **Generate & export:** optionally type a **Run name** (e.g. Phase1), review counts and generate. Each run gets its own folder, e.g. `Phase1 EqualSpan UniformLoad 04Nov 0930` (without a name the seed is used; `-2` is added if the name already exists). Open **Size ANN CSV**, **ANN beam CSV** or **Deflection ANN CSV**. Select a result to inspect individual checks.

ETABS-required top/bottom steel and shear reinforcement are exported automatically. No manual/provided bars are entered. The deflection calculation uses those idealized required areas and the recorded centroid cover.

The three main training files are ann_size.csv, ann_beams.csv and ann_deflection.csv. The size file contains the selected section of each beam line, one row per span with the ann_beams.csv columns (ann_size_wide.csv: one row per beam line), only when every span passes analysis, design, flexure, shear and deflection. It chooses minimum concrete volume among the tested sections. Failed cases stay in the audit files.

In the sampling dialog, **Variation count / study size** counts available section pairs and your requested study without starting ETABS. Range sampling does not impose 1 kN/m or 1 MPa increments. Values are stored to three decimals. The default section grid has 875 pairs, of which 607 satisfy 0.3h ≤ b ≤ h; entering 607 candidate sizes tests every allowed pair for each sampled scenario. Each study is capped at 10000 candidate models.

Load examples/simple_deflection_sample.json for the small three-span example. Real results are in sample_simple_3span. The long-term example uses explicit illustrative assumptions, which you should replace with the intended research assumptions.

Read PARAMETER_VARIATIONS.md for both ANN models and dataset counts, and DEFLECTION_GUIDE.md for the calculation basis and limitations.

## Load the 2,000-model Excel pilot

1. On **Beam scenarios**, click **Load Excel pilot: 100 × 20**. This replaces the current inputs and opens a review of 2,000 candidate models / 6,000 beam rows.
2. Review the 100 scenarios, 20 candidate sizes and simple deflection limits in the numbered tabs. On another PC, check the ETABS application path in Candidate sizes.
3. In **Generate & export**, choose an output folder and click **Generate CSV dataset** when ready. Loading the preset starts no analysis.
4. Open the resulting ANN CSVs using the buttons. The 38 invalid self-weight inputs are retained and excluded; 1,962 models are eligible for analysis. Final passing counts depend on actual results.

The matching Excel workbook is in study_plan. It is a planning reference, not a calculated dataset. Use the GUI to change inputs.
