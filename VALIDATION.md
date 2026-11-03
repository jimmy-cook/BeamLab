# Version 0.7.0 verification

The Excel pilot is embedded as a frozen JSON resource, with exactly the same 100 scenario inputs, 20 section pairs, 2,000 candidate IDs and 6,000 planned beam rows as the supplied workbook. No full 2,000-model ETABS collection was completed for release validation. A user-started collection in the earlier preview build was detected during packaging and left untouched; it is not included as validation evidence.

68 automated tests passed. New checks lock every planned input and canonical identifier to the original workbook plan, verify 38 invalid candidate inputs (114 beam rows), check enabled immediate DL L/120 and total D+L L/240 settings, confirm each load returns independent editable inputs, and exercise all 100 scenarios through the form conversion without changing their IDs. The existing backend, CSV, required-reinforcement, serviceability, API and DPI regression tests also passed.

The standalone Windows app launched. Its pilot input button and the loaded review were visually observed: 100 scenarios, 2,000 candidate models, 6,000 beam rows, 1,962 eligible models and 38 invalid inputs. Further clicks were interrupted by user activity, so no full UI generation run is claimed. The final executable was rebuilt after the form-identifier fix; its embedded pilot JSON and PerMonitorV2/version manifest are checked during packaging.

The prior real ETABS sample below remains the engineering validation evidence for the unchanged calculation backend. It was not rerun for this preset/UI release. Simple deflection status uses entered limits, not universal ACI category acceptance. Full code verification remains outside the supported scope.

## Existing backend sample (0.6.0)

On 29 October 2026 (Asia/Bangkok), one isolated ETABS 22.5.0 model completed with three equal 6 m spans, uniform 300×600 mm sections, fc=30 MPa, fy/fyt=420 MPa, centroid cover=60 mm, total D=20 kN/m and L=10 kN/m. Strength used fully loaded 1.2D+1.6L; service used unfactored combinations. No manual bars were assigned and no large collection was run.

The default user-defined checks are immediate D against L/120=50 mm and immediate D+L against L/240=25 mm. End spans B1/B3 give approximately 6.043646 mm for D and 9.065469 mm for D+L. Middle span B2 gives 0.155399 mm and 0.233098 mm. All pass those entered limits; aci_status is correctly NOT_EVALUATED in simple mode. The final exports contain one all-pass sizing target for the whole beam line.

An independent numerical moment-area integration matches the total-deflection maxima within 1e-8 mm. Recomputing the same real force results against a stricter reference limit of 6 mm gives FAIL at both end spans and PASS at the middle span. A separate illustrative long-term recomputation with only D sustained for at least five years gives about 21.152760 mm at the end spans. These are offline recomputations, not additional ETABS analyses.

Original ETABS force and required-reinforcement CSVs remain unchanged when the final ANN exports are recomputed. The sample contains 3 beam rows, 63 strength/design station rows, 189 service-force rows, 63 deflection station rows, 6 deflection criterion rows and 1 ann_size.csv target. See sample_simple_3span/simple_validation.csv and manifest.json for provenance and hashes.

64 automated tests passed, including prior physics/API/DPI checks, user-limit PASS/FAIL boundaries, immediate versus long-term curve selection, missing/disabled inputs, legacy mode compatibility, compact ANN fields, all-span sizing selection, 25 mm sampling, three equal spans, within-range material sampling, all 875 section pairs and read-only count calculations.

An earlier 0.6 desktop build launched and its input screen was visually inspected using the computer-use skill. Further screen interaction was interrupted by user activity/minimization; the final updated Design checks and sampling-dialog flow has not been fully clicked through. The final executable was compiled and packaged with its PerMonitorV2 DPI manifest checked. Actual ETABS-to-CSV verification used the same backend through Python.

The method is the approximate midspan-Ie, span-by-span moment-area procedure described in DEFLECTION_GUIDE.md, using original ETABS elastic end moments and idealized required areas. It has not been independently commissioned against nonlinear cracked-stiffness redistribution. Complete detailing, development/laps, staged loading, support movement, deep beams, general axial/torsion response and full code acceptance remain outside this validation.
