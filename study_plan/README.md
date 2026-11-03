# Excel pilot inputs

The workbook documents 100 scenarios × 20 shared sections = 2,000 candidate models and 6,000 beam rows. It contains planned inputs; ETABS outputs remain uncomputed.

In the app, choose **Load Excel pilot: 100 × 20** on Beam scenarios. The exact inputs are embedded in the executable; Excel and Python are not required to load them. The source copy is beam_lab/data/ann_pilot_100x20.json, also compatible with Load study.

The app enables user-defined immediate DL L/120 and total D+L L/240 checks. D includes self-weight. All spans carry the same D and L; strength uses only fully loaded 1.2D+1.6L. Reinforcement is the automatic ETABS requirement.

38 candidate models (114 beam rows) have entered total D below the candidate beam self-weight. They remain in the plan and are excluded from analysis as invalid inputs, never mislabeled structural FAIL. The remaining 1,962 models represent 5,886 beam rows before any later analysis/design issues or eligibility filtering. Deep beams or missing results may need REVIEW. Passing training-row counts cannot be known before execution.

Canonical scenario_id and candidate_id values match the workbook. Friendly M0001 and S01 labels are workbook display identifiers; join app CSVs using canonical identifiers. All spans and candidates of a scenario keep the same training split (71 train / 19 validation / 10 test scenarios).

Editing inputs in the app changes the study; reload the preset to restore the original Excel plan. Loading and reviewing do not start ETABS. Choose Generate CSV dataset explicitly when ready. Runs are sequential; stop completes the current model. Automatic resume is not available.
