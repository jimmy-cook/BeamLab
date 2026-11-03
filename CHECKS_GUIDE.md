# Checks in version 0.7.0

ETABS ACI 318-19 beam design exports required top/bottom/shear reinforcement and per-combination section-feasibility evidence. Those results remain separate from deflection criteria. No manually provided reinforcement or discrete-bar checker is added.

The default deflection panel uses two user-defined limits and an immediate/long-term total selector. See DEFLECTION_GUIDE.md for equations, assumptions, statuses and legacy ACI mode. User-defined PASS is not an ACI-category or complete-code acceptance label.

ann_size.csv requires all spans to pass analysis, design, flexure, shear, overall section feasibility and deflection, then chooses one minimum-tested-volume uniform section. ann_beams.csv retains the broader audit and required-area regression data. See PARAMETER_VARIATIONS.md for training filters and counts.

Unknown global ETABS errors are not guessed to belong to one action. Per-action evidence and original messages are preserved. Missing requirements, ambiguous evidence and unsupported actions remain REVIEW/NOT_EVALUATED rather than a false PASS or binary failure. full_aci_verified remains 0.
