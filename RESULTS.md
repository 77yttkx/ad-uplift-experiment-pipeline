# RESULTS

Single source of truth for verified numbers. Every resume number must trace to a line here and to reproducible code or output.
Assumed ad cost / conversion value / profit must be labeled simulation / assumption.

## Data Pipeline

| Metric | Value | Evidence / script |
|---|---|---|
| Raw rows | 13,979,592 | `wc -l` = 13,979,593 minus 1 header line |
| Raw file | criteo-uplift-v2.1.csv, 3,248,115,221 bytes | SHA-256 e4d7c710ca1f38e523309d0f8a0745d1b53e7392d51f20d1088b6cfeaef222ef |
| Parquet rows | TODO | |
| S3 object (key, size) | TODO | |

## Data Validation (Day 1, descriptive)

| Check | Result | Evidence / script |
|---|---|---|
| Missing values | 0 in all 16 columns | `src/validation/explore.py` |
| treatment / conversion / visit / exposure binary | all only 0 and 1 | `src/validation/checks.py` |
| conversion = 1 AND visit = 0 | 0 rows (conversion is a subset of visit) | `src/validation/checks.py` |
| treatment = 0 AND exposure = 1 | 0 rows | `src/validation/checks.py` |
| Exposure rate, treatment group | 3.60% (428,212 of 11,882,655) | `src/validation/checks.py` |

## Experiment

Descriptive numbers only (raw rates). No test has been run yet: CI, p-value and SRM come in A3.

| Metric | Value | Evidence / script |
|---|---|---|
| Treatment N / Control N | 11,882,655 (85.0%) / 2,096,937 (15.0%) | `explore.py`, `checks.py` |
| Conversion rate (T / C) | 0.3089% / 0.1938% | `explore.py`, `checks.py` |
| Visit rate (T / C) | 4.8543% / 3.8201% | `checks.py` |
| Absolute lift / relative lift (raw, untested) | about +0.115 pp / about +59.4% | computed from the rates above |
| 95% CI | TODO | |
| p-value | TODO | |
| SRM p-value (expected split 85/15, not 50/50) | TODO | |
| MDE / power | TODO | |
| Variance reduction (covariate adjustment) | TODO | |

Conversion rate by treatment x exposure (for the exposure-selection finding, see PROJECT_LOG):

| Group | Users | Visit rate | Conversion rate |
|---|---|---|---|
| Control | 2,096,937 | 3.8201% | 0.1938% |
| Treatment, not exposed | 11,454,443 | 3.4861% | 0.1194% |
| Treatment, exposed | 428,212 | 41.4538% | 5.3784% |

## Uplift Modeling

| Metric | Value | Evidence / script |
|---|---|---|
| T-learner Qini / AUUC | TODO | |
| S-learner Qini / AUUC | TODO | |
| Random baseline | TODO | |
| Selected model | TODO | |

## Business Simulation (simulation / assumption)

| Metric | Value | Evidence / script |
|---|---|---|
| Selected top-k% | TODO | |
| Incremental conversions | TODO | |
| Assumed ad cost / conversion value | TODO | |
| Estimated incremental profit | TODO | |
| Sensitivity analysis | TODO | |
