# Simple RC deflection dataset — 0.7.0

## Visible inputs

- DL Deflection Limit, L/: the divisor for immediate dead-load deflection.
- Total Deflection Limit, L/: the divisor for the selected total-deflection basis.
- Total basis: immediate D+L, or long-term D+L including additional creep/shrinkage.

The editable starting values are 120 and 240. For a 6000 mm span these give 50 mm and 25 mm. These are user-defined research/project limits, not universal ACI 318-19 requirements. Simple mode exports limit_source=USER_DEFINED and aci_status=NOT_EVALUATED.

Both divisors are required when simple-mode checks are enabled. Long-term total additionally requires the actual sustained live-load percentage and duration. There is no hidden sustained percentage. The Advanced section opens automatically for long-term total. DL remains immediate even when total uses long-term behaviour.

## Calculation

ETABS provides SLS_D=D, SLS_DL=D+L and SLS_SUSTAINED=D+ψL moment diagrams. All are excluded from strength design. SLS_D includes beam self-weight: inside the entered total D, or added by ETABS when D is superimposed. The existing strength combination remains 1.2D+1.6L in the research preset.

Python computes the RC response using the approximate prismatic-member midspan Ie procedure in ACI 318-19 24.2.3.7 and Table 24.2.3.5. Ec=4700√fc MPa, Es=200000 MPa and fr=0.62√fc MPa for normal-weight concrete. The cracked transformed section uses the actual required top/bottom ETABS areas at the unique midspan station, idealized at the configured centroid. It ignores concrete tension and accounts for the concrete displaced by compression steel.

Mcr=fr Ig/yt. Ie=Ig when Ma≤(2/3)Mcr; otherwise Ie=Icr/[1−((2/3)Mcr/Ma)²(1−Icr/Ig)], capped at Ig. Full D+L midspan cracking is retained for D and sustained-load cases; it is not assumed to heal on unloading. This does not use the ETABS stiffness modifier (now 1.0; earlier studies 0.35) for deflection.

The original ETABS elastic end moments and UDL are integrated span by span using constant midspan Ie, enforcing zero deflection at the ideal supports. Rectangular elastic shear deflection is included with k=5/6 and Poisson ratio 0.2. The method does not iterate moment redistribution for different cracked stiffnesses between spans. It is an approximate moment-area calculation, not nonlinear cracked analysis.

Deflection is relative to the support chord, downward-positive. Whole-span polynomial extrema are located before taking magnitudes; maxima need not occur at midspan or a sampled output station.

For long-term total, additional creep/shrinkage movement is λ times the immediate sustained-load curve, with λ=ξ/(1+50ρ′). The selected ACI duration entries give ξ=1.0, 1.2, 1.4 or 2.0 at 3, 6, 12 or at least 60 months. The compression ratio uses automatic required midspan reinforcement in the compression zone. Long-term total is immediate D+L plus this additional curve. Immediate dead movement is not deducted from a user-defined total-long-term check.

## Labels

For each criterion, allowable deflection=span/divisor and utilization=maximum absolute calculated deflection/allowable. Values up to 1 within numerical tolerance pass. Each criterion has its own governing station. Overall simple-mode status combines DL and total only.

PASS=1, FAIL=0; missing/ambiguous evidence produces REVIEW with a blank binary target. Disabled checks are NOT_EVALUATED. ETABS design errors/warnings, missing required reinforcement, unsupported axial/minor/torsion actions, and analytical span≤4h do not produce manufactured deflection passes. The implementation is limited to the stated normal-weight prismatic gravity-beam population.

The compact ann_deflection.csv includes geometry/material/load inputs, full span arrangement, limit divisors, total basis, relevant time assumptions, calculated deflections, stations, allowable values, ratios, DL/Total/Overall statuses and binary labels. These labels concern the entered criteria under idealized automatic required reinforcement. They cannot approve an arbitrary user-selected bar arrangement.

## Legacy advanced ACI mode

Older ACI-category studies remain supported under Advanced settings. That mode adds the applicable live-load or after-attachment criterion and keeps any entered project limits separate. It requires the relevant category, time/attachment assumptions and, for non-sensitive attachments, tolerance. Its ACI/project decisions remain distinct. Simple mode does not require these category choices.

Both modes leave full_aci_verified=0. Anchorage, laps, full detailing, support critical sections, serviceability outside this approximation, roof snow/rain/ponding and complete code/project acceptance require separate verification.

References: ACI 318-19 Chapter 24 and the installed CSI API/manuals. [ACI discussion of the effective-inertia procedure](https://www.concrete.org/publications/internationalconcreteabstractsportal.aspx?ID=51746784&m=details). Licensed documents are not redistributed.
