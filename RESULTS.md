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

Status (2026-10-30): the two-proportion z-test and 95% confidence interval are done for conversion (primary) and visit (secondary), with automated tests. Sample ratio diagnostics are reported descriptively, and the formal SRM status is `not_assessable` (see below). Not done yet: MDE / power, variance reduction, uplift modeling, business simulation, and the Parquet / S3 / cloud steps.

### A/B test methodology

| Item | Choice |
|---|---|
| Groups | `treatment = 1` vs `treatment = 0`, the randomized assignment (intent-to-treat). `exposure` is not used to define groups. |
| Unit of analysis | User |
| Primary outcome | `conversion` |
| Secondary outcome | `visit` |
| Significance test | Two-sided two-proportion z-test, standard error pooled under H0 |
| Confidence interval | 95% unpooled Wald interval for the absolute difference (treatment minus control) |
| Absolute lift | Treatment rate minus control rate (reported in percentage points) |
| Relative lift | Absolute lift divided by control rate |
| Multiple comparisons | No adjustment across the two outcomes |
| Aggregation | Counts computed in DuckDB directly from the CSV (not loaded into pandas) |

Code: `src/experimentation/proportion_tests.py` (pure test function), `src/experimentation/ab_test.py` (aggregation, reporting, JSON output), `tests/test_statistics.py` (31 pytest tests).

Reproduce (from the project root, with `.venv` active):

```bash
python -m pytest
python src/experimentation/ab_test.py     # or: python -m src.experimentation.ab_test
```

Output is written to `outputs/ab_test_results.json`. That folder is git-ignored, so regenerate it instead of versioning it.

Verification (local run on the full 13,979,592-row file): all 31 tests pass, both ways of running the script work, and both give identical results.

### Primary outcome: conversion

| Metric | Value | Evidence / script |
|---|---|---|
| Control | 4,063 / 2,096,937 = 0.193759% | `ab_test.py` -> `outputs/ab_test_results.json` |
| Treatment | 36,711 / 11,882,655 = 0.308946% | same |
| Absolute difference | +0.115187 percentage points | same |
| Relative difference | +59.45% | same |
| z-statistic | 28.5165 | same |
| p-value (two-sided) | 7.30826e-179 | same |
| 95% CI for absolute difference | [0.108451%, 0.121924%] | same |

### Secondary outcome: visit

| Metric | Value | Evidence / script |
|---|---|---|
| Control | 80,105 / 2,096,937 = 3.820096% | `ab_test.py` -> `outputs/ab_test_results.json` |
| Treatment | 576,824 / 11,882,655 = 4.854336% | same |
| Absolute difference | +1.034240 percentage points | same |
| Relative difference | +27.07% | same |
| z-statistic | 65.2474 | same |
| p-value (two-sided) | Underflows to 0.0 in double precision. This is not exactly zero; the true value is far below 1e-300. | same |
| 95% CI for absolute difference | [1.005563%, 1.062918%] | same |

### Sample ratio diagnostics (SRM)

Descriptive only. The formal SRM status is `not_assessable`. No SRM test statistic or p-value was calculated. Do not read this section as "SRM passed" or "SRM failed".

| Metric | Value | Evidence / script |
|---|---|---|
| Total users | 13,979,592 | `src/validation/srm_check.py` -> `outputs/srm_results.json` |
| Control (`treatment = 0`) | 2,096,937 (14.999987%) | same |
| Treatment (`treatment = 1`) | 11,882,655 (85.000013%) | same |
| Published approximate split | 15% control / 85% treatment (Criteo lists a treatment ratio of .85 for this version, rounded to two decimals) | [Criteo dataset page](https://ailab.criteo.com/criteo-uplift-prediction-dataset/) |
| Observed minus published | control -0.000013 pp (-1.8 users); treatment +0.000013 pp (+1.8 users) | `srm_check.py` |
| Formal SRM status | `not_assessable` | `srm_check.py` |
| SRM test statistic / p-value | Not calculated | |

Code: `src/validation/srm_check.py` counts users per arm with DuckDB directly from the CSV, computes the proportions, compares them with the published split, and writes `outputs/srm_results.json` (git-ignored). `tests/test_srm_check.py` (50 pytest tests) covers the proportion arithmetic, invalid group counts, the comparison with the reference, the assessment logic, the report structure (it must contain no p-value or test-statistic fields), and the DuckDB counting.

Reproduce (from the project root, with `.venv` active):

```bash
python -m pytest tests/test_srm_check.py
python src/validation/srm_check.py     # or: python -m src.validation.srm_check
```

Verification: the script was run on the full 13,979,592-row file on a Mac and generated the JSON. The data file size and SHA-256 recorded in that JSON match the values in the README.

Interpretation and limits:

- **Why it is not assessable.** A formal SRM test compares observed counts with the assignment probability prespecified for the original experiment. That probability has not been verified. Criteo states that the data was sub-sampled non-uniformly so that the original incrementality level cannot be deduced, and it does not state the original randomization probability. The roughly 85% treatment share describes the released dataset.
- **What the counts show.** The observed split matches the published one to within 2 users. That describes the release only. It neither confirms nor contradicts random assignment in the original experiment.
- **Why not test against 85/15.** That would ask whether the release matches its own published ratio. A "pass" would say nothing about the original assignment and could be misread as evidence of valid randomization.
- **Reference is approximate and unverified.** The 85/15 reference is rounded and was read from the provider's page on 2026-10-30. It has not been independently verified.
- **What would make it assessable.** An independently verified assignment probability for the data as released: the original prespecified probability plus documentation that the release did not change the arm proportions, or a documented sampling scheme from which the expected released proportion can be derived.
- **Consequence for the A/B results.** They rely on the provider's description of random assignment, which nothing in this repository can confirm or contradict.

### Still open in this section

| Metric | Value | Evidence / script |
|---|---|---|
| Formal SRM test | Not assessable: no verified original assignment probability, so no p-value was calculated (see "Sample ratio diagnostics (SRM)") | `srm_check.py` |
| MDE / power | Not done | |
| Variance reduction (covariate adjustment) | Not done | |

### Interpretation limits

- **Sampling.** The Criteo public dataset is a released research sample. Its sampling and any preprocessing by the provider cannot be verified here, and it may not represent Criteo's full user population, other advertisers, or other time periods. These comparisons are not validated population-level causal effects and should not be presented as such.
- **Assignment, not exposure.** The estimates are for being assigned to the ad group. Only 3.60% of the treatment group was exposed (428,212 of 11,882,655), so these numbers are not the effect per exposed user. That quantity has not been estimated.
- **Randomization not verified.** A formal SRM test could not be run (see "Sample ratio diagnostics (SRM)"), so nothing here confirms or contradicts the provider's description of random assignment. The comparison relies on that description. The 85/15 split describes the release; it is not a verified assignment probability.
- **Size vs. significance.** With about 14 million users, very small differences give very large z-statistics. The absolute differences (0.115 pp on a 0.194% base, 1.03 pp on a 3.82% base) and the intervals matter more than the p-values. No cost or value is attached to them yet.
- **Two outcomes, no adjustment.** Visit is secondary and p-values are not corrected for testing two outcomes.
- **Visit p-value.** Report it as "< 1e-300 (underflow)", never as 0.
- **Interval method.** The Wald interval uses a normal approximation. Conversion counts are in the thousands in both groups, so it is reasonable here.

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
