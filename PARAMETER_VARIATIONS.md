# Parameter variations for the first ANN stage

## Confirmed population

| Parameter | Variation |
|---|---|
| Number of spans | Exactly 3 for the first stage |
| Span lengths | L1=L2=L3, sampled within 3000–12000 mm on a 250 mm grid (37 values) |
| Dead D | Sampled within 10–150 kN/m; superimposed, with ETABS adding beam self-weight (optional: total including self-weight) |
| Live L | Sampled within 10–150 kN/m and within 0.25D–1.5D (live/dead ratio limits; D drawn first, then L evenly in its band) |
| fc | Sampled within 20–50 MPa (continuous) |
| fy | Sampled within 200–600 MPa (continuous; ACI 318-19 Table 20.2.2.4(a) permits up to 690 MPa for flexure outside special seismic systems) |
| fyt | Sampled on its own within 200–420 MPa (continuous). 420 MPa is the ACI 318-19 Table 20.2.2.4(a) limit for shear (stirrups, ties, hoops of deformed bars). Optional: copy fy, capped at 420 MPa |
| Width b | 300, 325, 350, …, 900 mm: 25 levels |
| Depth h | 350, 375, 400, …, 1200 mm: 35 levels |
| Width ÷ depth | 0.3h ≤ b ≤ h by default: 607 of the 875 pairs |
| Span ÷ depth | span > 4h; deep-beam candidates (only spans under 4.8 m) are invalid inputs, not FAIL labels |
| Reinforcement | Automatic ETABS-required areas; outputs, not independent factorial inputs |
| D/L on spans | Same D and same L on all three spans |
| Strength combination | Fully loaded 1.2D+1.6L only |
| Default deflection criterion | User DL L/120 and immediate D+L L/240, editable |

Range sampling does not impose 1 kN/m or 1 MPa steps. The latest clarification supersedes the earlier integer-grid count. By default, D/L/fc/fy are sampled within bounds and stored to 3 decimals, with span lengths on the span grid (default 250 mm). On that grid three unequal spans give 50,653 arrangements; a neighbouring span ratio limit of 2.0, 1.5 or 1.2 leaves 31,465, 15,353 or 4,079. The optional integer-only checkbox is off. These numerical precisions are not prescribed material-grade catalogues.

Centroid cover, concrete density, flexural modifier and service criteria are fixed within a study unless explicitly changed. Current starting values are 60 mm, 24 kN/m³ and a flexural modifier of 1.0 (no EI reduction: this gravity-only, first-order analysis of a uniform continuous beam gives the same forces and design for any common factor, and deflection uses its own cracked-section Ie; studies made before this change recorded 0.35 with identical results). Long-term inputs add factors only when that total basis is selected. Derived Wu, neighbouring span ratios, forces and required areas are not independent variation factors. Three equal spans do not create three independent length factors.

## Counts

The finite section grid is 25×35=875 b/h pairs; the default width-to-depth rule 0.3h ≤ b ≤ h keeps 607 of them (b ≤ h alone keeps 622; 0.3h ≤ b ≤ 2h keeps 840). The bounds on the other parameters do not define a unique finite factorial population without explicit discrete levels. Therefore the earlier whole-unit numerical factorial total is not the total for the requested range-sampling study.

For N sampled base scenarios and K tested section pairs per scenario:

- Candidate ETABS models: N×K.
- Beam-candidate rows: 3×N×K.
- At every allowed pair, K=607: 607N models and 1821N beam-candidate rows (K=875 with both b/h limits set to 0).
- Independent sizing targets: at most N, because the chosen uniform section is one target per whole beam line.
- Smart size search: at most N × Σ⌈log2(n_w+1)⌉ models (21N for the default four widths), with no shared random sizes.
- Passing reinforcement/deflection rows: unknown until the actual checks complete.

| Base scenarios N | Sections K | Candidate models | Beam-candidate rows before filters |
|---:|---:|---:|---:|
| 100 | 6 | 600 | 1800 |
| 1000 | 6 | 6000 | 18000 |
| 10 | 607 | 6070 | 18210 |
| 1000 | 607 | 607000 | 1821000 |

The last example is a theoretical total requiring batches; one study is capped at 10000 candidate models. No such collection is launched automatically. **Variation count / study size** in the UI reports the available section grid and planned sampled study. If optional integer-only mode is deliberately selected, it also reports that explicit integer-grid factorial estimate; it is not the default population.

## ANN 1: size prediction

Two files hold the same answers. ann_size.csv has one row per span of the selected size: exactly the ann_beams.csv columns in the same order (b_mm, h_mm, As_…, sls_… of that span), followed by span_ratio_2_1, span_ratio_3_2, max_adjacent_span_ratio, max_span_mm, min_span_mm, total_length_mm, max_min_span_ratio, end_to_interior_span_ratio, live_dead_ratio, wu_entered_kN_m and candidates_tested; the spans of one line share scenario_id, split and b_mm × h_mm. ann_size_train.csv has the same rows cut to scenario_id, beam_id, split, span_index, span_type, span_length_mm, the size-independent inputs (span_1–3_mm, span_arrangement, equal_spans, dead_input_kN_m, live_kN_m, fc_MPa, fy_MPa, fyt_MPa and the extra line columns) and the outputs b_mm, h_mm; it holds no column that depends on the chosen size. ann_size_wide.csv has one row per beam line, described below. In ann_size.csv only the size-independent columns are predictors for b × h (spans, dead_input_kN_m, live_kN_m, materials, cover, limits and the extra line columns); b_mm, h_mm, self_weight_kN_m, dead_total_kN_m, Wu_kN_m, forces, reinforcement and sls_ results all depend on the selected size.

In ann_size_wide.csv, inputs are the full three-span arrangement, D/L (D as entered: superimposed by default, see dead_load_basis), materials, centroid/stiffness assumptions and selected deflection criteria. The targets are target_b_mm and target_h_mm. Candidate-specific required steel, forces and self-weight-derived superimposed load are not included as size-independent predictors.

Ready-made derived inputs, all computed from the spans and entered loads only (none depends on the chosen size): span_arrangement (equal/unequal) / equal_spans (1/0, also in ann_beams.csv, where span_type is the span position end_left/interior/end_right), span_ratio_2_1, span_ratio_3_2, max_adjacent_span_ratio, max_span_mm, min_span_mm, total_length_mm, max_min_span_ratio, end_to_interior_span_ratio, live_dead_ratio and wu_entered_kN_m (1.2D + 1.6L with D as entered, without self-weight). For an equal-span-only dataset the span ratios are constant and add nothing.

Only beam lines whose size search finished get a row: every search width must end found, none_passed or no_valid_depth (in every-size mode, every listed size must have run). A line cut short by Stop, a crash or an ETABS error that persists after a restart may not have reached its smallest size yet; its tested sizes stay in beams.csv / ann_beams.csv / size_search.csv but it has no ann_size.csv row.

Every column starting with target_ is an output, named target_ + the beams.csv column of the selected section: target_b_mm, target_h_mm, target_self_weight_kN_m, target_dead_total_kN_m, target_Wu_kN_m, and the ETABS-required reinforcement per span n (span_index n−1) at left/mid/right, target_span{n}_As_top_mm2_{left|mid|right}, target_span{n}_As_bottom_mm2_{…}, target_span{n}_Av_over_s_mm2_per_m_{…}, with the maxima over the beam line target_max_As_top_mm2, target_max_As_bottom_mm2, target_max_Av_over_s_mm2_per_m. These depend on the chosen b × h, so they must never be used as inputs for predicting b × h. Reinforcement values are required areas, not a bar schedule.

Column names are shared: a column with the same name in beams.csv, ann_beams.csv, ann_deflection.csv, ann_size.csv or ann_stations.csv means the same quantity. Loads: dead_input_kN_m is D as entered, self_weight_kN_m the beam's own weight, dead_total_kN_m = D + self-weight (superimposed basis), live_kN_m the live load, Wu_kN_m = 1.2 dead_total + 1.6 live, wu_entered_kN_m = 1.2 dead_input + 1.6 live (size-independent). Reinforcement uses the design_stations.csv names (As_top_mm2, As_bottom_mm2, Av_over_s_mm2_per_m, with _left/_mid/_right per zone). selected_candidate_id in ann_size.csv is candidate_id elsewhere. Every CSV writes whole numbers without a decimal point (10750, not 10750.0).

Every span must have completed analysis, a clear design summary, PASS flexure, PASS shear, PASS overall ETABS section feasibility and PASS deflection. Invalid, failed and unresolved cases do not get a size-training target. If several candidates pass, choose minimum tested concrete volume, then lower depth and width for ties. If none passes, that base scenario produces no ann_size.csv row. The audit files retain it with blank targets.

This gives a consistent target rather than many conflicting passing sizes for identical inputs. The selected size is optimal only among the tested candidates, not a proven global optimum. A sampled six-section matrix and an exhaustive 607-section search need not produce the same target quality; record candidates_tested.

### Smart size search (recommended for sizing targets)

Tick **Smart size search** in the sampling dialog. Instead of drawing shared random sizes, the study lists every allowed depth of each search width (default 300, 400, 500, 600 mm: 27 + 33 + 29 + 25 = 114 sizes under 0.3h ≤ b ≤ h). For each beam line and width, ETABS first runs the middle depth. A PASS rules out every deeper depth (they are assumed to pass) and the search continues among shallower ones; a FAIL rules out every shallower depth. Halving continues until the smallest passing depth on the 25 mm grid is found: at most ⌈log2(n+1)⌉ runs per width, 21 per beam line by default, so one 10,000-model study holds up to 476 beam lines. Deep-beam and self-weight-invalid depths are removed before searching. The pass rule is the ann_size.csv rule (strength and deflection when deflection checks are enabled). ann_size.csv then selects the minimum-area passing size over the searched widths, exactly as above.

The search assumes a deeper beam of the same width never fails where a shallower one passes. Verify this on a few beam lines: load the saved study, untick **Smart size search** on Candidate sizes to run every listed size, and compare size_targets_sls.csv with the search run. size_search.csv records, per beam line and width, the valid depth range, each probe (depth:PASS/FAIL), the ETABS run count and the result: found, none_passed (no listed depth passes), no_valid_depth, run_error (that width's search stops at an ETABS failure) or stopped.

## ANN 2: required reinforcement prediction

Use ann_beams.csv with reinforcement_regression_eligible=1. Inputs include the known candidate b/h, full span arrangement, D/L, materials and centroid/stiffness assumptions. Nine outputs are top and bottom As at left/mid/right in mm², and Asv at left/mid/right in mm²/m.

Use valid, clear strength-design requirements. A deflection failure may remain a valid required-reinforcement regression case; keep its separate deflection label. Do not turn a failed or unavailable ETABS reinforcement result into zero steel. The target is required reinforcement under the reference assumptions, not a final bar count/spacing schedule.

For a deflection ANN, use ann_deflection.csv with classification_eligible=1 and limit_mode=user_defined. Select either the binary targets or calculated deflection targets. Do not feed output deflections, utilization, statuses or target-derived eligibility into predictors. Keep all spans, sections and limit variations from the same structural scenario in the same data split.

The number of passing size and reinforcement samples cannot be known from the parameter ranges alone. Class balance and coverage must be checked after generation; the small example is an exporter verification, not a trainable research population.
