# Check the smart size search on real ETABS results

The smart size search assumes that if a depth passes, every deeper beam of the same width also passes. This check proves it on 10 real beam lines by running the same study twice and comparing.

**Study file:** `examples/verify_size_search_study.json`

- 10 beam lines, 3 unequal spans each (neighbouring span ratio ≤ 1.5), sampled with seed 2032
- Search widths 300 and 400 mm, every allowed depth on the 25 mm grid (60 sizes)
- Deflection checks on: DL L/120, Total L/240 (immediate)
- D is superimposed dead load; ETABS adds each size's self-weight. D ranges 15.8–131.0 kN/m, L/D 0.26–1.45; f′c 24.9–45.8 MPa, fy 216–598 MPa, stirrup fyt 237–407 MPa

| Run | Smart size search | ETABS models |
|---|---|---|
| A | ticked | at most 110 |
| B | unticked | 562 (38 deep-beam sizes are skipped) |

Time one model in run A first. Run B takes about 5–6 times as long as run A.

## Steps

1. Open **ETABS Beam Lab 0.7.exe**. On **1 Beam scenarios**, click **Load study…** and choose `examples/verify_size_search_study.json`.
2. On **2 Candidate sizes**, check that **Smart size search** is ticked. Check the ETABS 22 path.
3. On **4 Generate & export**, type **VerifyA** in **Run name** and click **Generate CSV dataset**. When it finishes, the status line shows the run folder, e.g. `VerifyA UnequalSpan UniformLoad 04Nov 0930` inside the `runs` folder next to the exe (unless you chose another). This is **run A**.
4. Go back to **2 Candidate sizes** and **untick Smart size search**. Change nothing else.
5. On **4 Generate & export**, change **Run name** to **VerifyB** and click **Generate CSV dataset** again. Note the new run folder. This is **run B**.
6. Compare the two runs. Either send both folder paths to Claude, or open PowerShell in this folder and run:

```
py -3.13 -m beam_lab.cli verify-search --search "RUN A FOLDER" --full "RUN B FOLDER" --out verify_report.csv
```

## Reading the result

The command prints a summary. The last line is the verdict:

- **PASS**: for every beam line and width, the search found the same smallest passing depth as testing every size, and no deeper beam failed after a shallower one passed. The search can be used for the main studies.
- **CHECK**: open `verify_report.csv` and look at rows where `same_answer` = 0 or `deeper_always_passes` = 0. `full_pass_pattern` lists every depth of run B as P (pass) or F (fail); `violations` names the depths where a deeper beam failed.

Other columns: `search_smallest_h_mm` (run A), `full_smallest_h_mm` (run B), `search_target` / `full_target` (the final b × h for the line in each run), `search_etabs_runs` (models the search needed for that width).

A beam line where neither 300 nor 400 mm passes at any depth shows `none_passed` in both runs; that still counts as a match.
