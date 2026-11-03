# RESULTS

Single source of truth for verified numbers. Every resume number must trace to a line here and to reproducible code or output.
Assumed ad cost / conversion value / profit must be labeled simulation / assumption.

## Data Pipeline

| Metric | Value | Evidence / script |
|---|---|---|
| Raw rows | 13,979,592 | `wc -l` = 13,979,593 minus 1 header line |
| Raw file | criteo-uplift-v2.1.csv, 3,248,115,221 bytes | SHA-256 e4d7c710ca1f38e523309d0f8a0745d1b53e7392d51f20d1088b6cfeaef222ef |
| Parquet rows | 13,979,592 (equal to the CSV row count) | `src/ingestion/csv_to_parquet.py` -> `outputs/parquet_conversion_report.json` |
| Parquet file | criteo-uplift-v2.1.parquet, 185,895,062 bytes (114 row groups, ZSTD) | same |
| Parquet SHA-256 | f2e6f26b68cdbb1ff4f04f58d095f9792b15355dc093d86e56447cf806e42a12 | same |
| Size reduction vs. CSV | 94.28% smaller (17.5x) | computed from the two byte sizes above |
| Conversion validation | 28 of 28 checks passed (see "Parquet conversion validation") | same |
| S3 object (key, size) | TODO | |

## Parquet conversion validation

The CSV was converted to Parquet with DuckDB, which streams the file, so the dataset was not loaded into Python or pandas memory. Nothing was filtered, deduplicated, rounded, recoded or imputed. Local run on a Mac (report generated 2026-10-30T21:51:20+00:00, Python 3.12.7, DuckDB 1.5.6, DuckDB memory limit 3GB). The run took 22.5 s end to end, including both SHA-256 computations and the validation scans. That is one local run on one machine, not a benchmark.

| Metric | Value | Evidence / script |
|---|---|---|
| Overall status | `passed`; no failed checks | `outputs/parquet_conversion_report.json` |
| Rows, CSV (DuckDB parse) / Parquet | 13,979,592 / 13,979,592 | same |
| Physical lines in CSV, counted independently | 13,979,593 (rows plus 1 header line) | same |
| Columns | 16, in the original CSV order: f0 to f11 as DOUBLE; treatment, conversion, visit, exposure as INTEGER | same |
| Missing values / non-finite features / non-binary values | 0 / 0 / 0, in both the CSV and the Parquet | same |
| Group sizes | control 2,096,937; treatment 11,882,655 | same |
| Conversions (control / treatment) | 4,063 / 36,711 | same |
| Visits (control / treatment) | 80,105 / 576,824 | same |
| Exposed users (control / treatment) | 0 / 428,212 | same |
| Content fingerprint, CSV vs. Parquet | equal | same |

How the validation works:

- **Schema.** The CSV header must contain exactly the 16 expected column names, each once. The Parquet column names, order and types are compared with the expectation.
- **Parsing without rounding.** Every column is first read as DOUBLE. The four binary columns must then be exactly 0 or 1 before they are stored as INTEGER (see PROJECT_LOG, 2026-11-03, for why).
- **Row counts.** The DuckDB row count of the CSV, the independent physical line count minus the header, and the Parquet row count must all agree.
- **Counts verified earlier.** Total rows, group sizes, and conversion, visit and exposure counts per group must equal the values already recorded in this file and in `outputs/ab_test_results.json`.
- **Content.** The CSV and the Parquet must have identical minimum and maximum per column, identical binary sums per group, and an identical content fingerprint (an order-independent sum of per-row DuckDB hashes over all 16 columns).
- **Write safety.** The Parquet file is written under a temporary name and moved to its final name only if every check passes.

Reproduce (from the project root, with `.venv` active):

```bash
python -m pytest tests/test_csv_to_parquet.py
python src/ingestion/csv_to_parquet.py     # or: python -m src.ingestion.csv_to_parquet
```

`tests/test_csv_to_parquet.py` has 64 pytest tests on small synthetic CSV files: successful conversion, value and row-order preservation, invalid or duplicated column names, unparseable values, non-binary values, missing values, NaN / infinity, row-count and content mismatches, and existing-output handling. The full suite (217 tests) passed on the Mac.

Limits:

- The checks show that the Parquet file matches the CSV as parsed with this schema. They do not show that the dataset is correct, representative, or free of upstream problems.
- The content fingerprint is an order-independent sum of hashes. It is a consistency check, not a cryptographic proof, and it does not check row order (the conversion preserves it).
- The expected counts apply to this v2.1 file only.
- The Parquet file and the CSV stay local and git-ignored. **No S3 upload, Snowflake load or dbt model exists yet**, so the "S3 object" row above is still TODO.

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

### Power analysis and minimum detectable effect (MDE)

Conditional sensitivity analysis, not verified power of the original experiment. Criteo states that the public data was sub-sampled non-uniformly and does not state the original randomization probability, and the formal SRM status is `not_assessable`. The calculations below take the released sample's group sizes and control conversion rate as given and ask what a two-proportion z-test could detect under a normal-approximation model. They do not show that the public sample represents the original experimental design.

**Design assumptions** (applied to the released sample; `outputs/power_analysis_results.json`, `design_assumptions`)

| Assumption | Value |
|---|---|
| Test | Two-sided two-proportion z-test, unequal group sizes |
| Significance level (alpha) | 0.05 |
| Target power | 0.8 |
| Baseline conversion rate (control conversions / control n, same data) | 0.193759% (4,063 / 2,096,937) |
| Control n | 2,096,937 |
| Treatment n | 11,882,655 (85.0000% of users) |

**Minimum detectable effect** at alpha = 0.05, power = 0.8

| Metric | Value |
|---|---|
| Absolute MDE | 9.3692e-05 (0.009369 percentage points) |
| Relative MDE | 4.8355% of the baseline |
| Treatment rate at the MDE | 0.203128% |
| Power at the solved MDE | 0.800000000000 (root solver converged in 21 iterations) |

**Theoretical power at assumed relative lifts** (same sample sizes and baseline; hypothetical effects, not findings)

| Assumed relative lift | Power |
|---|---|
| 1% (absolute +0.00194 pp) | 0.089265 |
| 5% (absolute +0.00969 pp) | 0.825518 |
| 10% (absolute +0.01938 pp) | 0.999931 |
| 20% (absolute +0.03875 pp) | 1.000000 |

Power at a 1% relative lift is low (8.9%): an effect that small would usually go undetected with these group sizes. Power at 5% is 82.6%, just above the 80% target, consistent with the MDE of about 4.84%.

**Post-hoc power at the observed lift (descriptive only).** At the observed relative lift of 59.45%, the same formula gives power of 1.0, with a type II error probability of about 4.0e-214. This does not validate the experiment. Post-hoc power is a function of the observed effect and the sample size, so it adds no information beyond the p-value, and it says nothing about randomization, selection into the sample, or whether the effect would generalize. The observed lift is also about 12 times the MDE, so a power near 1 is expected and carries no further meaning.

**Hypothetical allocation comparison** (HYPOTHETICAL: not how users were assigned). Total sample size (13,979,592) and the baseline rate are held fixed; only the treatment share changes. The reference lift for the power column is 5% relative.

| Scenario | Control n | Treatment n | Relative MDE | Power at a 5% relative lift |
|---|---|---|---|---|
| 50/50 (hypothetical) | 6,989,796 | 6,989,796 | 3.430% | 0.9825 |
| 15/85 (the released split, rounded share) | 2,096,939 | 11,882,653 | 4.835% | 0.8255 |

The 15/85 row uses a rounded treatment share, so its group sizes differ from the observed counts by 2 users and its MDE matches the observed-size MDE above to four significant figures. In this model a balanced split would detect a smaller effect (relative MDE 3.43% vs 4.84%). This shows the cost of unbalanced allocation only. It is not a recommendation about the original design. The file also contains scenarios for treatment shares of 5%, 10%, 15%, 30%, 70% and 95%.

Code: `src/experimentation/power_analysis.py` (pure functions plus a CLI; it reads no dataset) and `tests/test_power_analysis.py` (72 pytest tests). Method: null standard error pooled at the expected pooled probability, alternative standard error unpooled, both rejection tails included, and the MDE found with `scipy.optimize.brentq` rather than an equal-allocation shortcut. The full formulas are in the JSON `methodology` block and the module docstring.

Reproduce (from the project root, with `.venv` active):

```bash
python -m pytest tests/test_power_analysis.py
python src/experimentation/power_analysis.py     # or: python -m src.experimentation.power_analysis
```

Verification: the full suite (153 tests) passed on a Mac, the JSON was generated, and the script's built-in check of its constants against `outputs/ab_test_results.json` reported `consistent`.

Limits specific to this section:

- **Normal approximation.** The null standard error uses the expected pooled probability implied by the assumed rates, not a sample-specific value.
- **Baseline has sampling error.** The baseline is the control rate from the same data, and that error is not propagated into the MDE.
- **Assumes independent, simple-random assignment.** Clustering, interference and non-compliance are not modelled.
- **Conversion only, assignment not exposure.** Only 3.60% of the treatment group was exposed, so an intent-to-treat effect is diluted relative to an effect per exposed user.
- **Direction.** The MDE is for an increase in conversion (treatment above control).
- **No business meaning yet.** Whether an effect near the MDE matters commercially has not been assessed.

### Still open in this section

| Metric | Value | Evidence / script |
|---|---|---|
| Formal SRM test | Not assessable: no verified original assignment probability, so no p-value was calculated (see "Sample ratio diagnostics (SRM)") | `srm_check.py` |
| MDE / power | Done as a conditional sensitivity analysis only, not verified power of the original design (see "Power analysis and minimum detectable effect (MDE)") | `power_analysis.py` |
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
