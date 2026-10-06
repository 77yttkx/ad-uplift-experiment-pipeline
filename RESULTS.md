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

## Experiment

| Metric | Value | Evidence / script |
|---|---|---|
| Treatment N / Control N | TODO | |
| Conversion rate (T / C) | TODO | |
| Visit rate (T / C) | TODO | |
| Absolute lift / relative lift | TODO | |
| 95% CI | TODO | |
| p-value | TODO | |
| SRM p-value | TODO | |
| MDE / power | TODO | |
| Variance reduction (covariate adjustment) | TODO | |

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
