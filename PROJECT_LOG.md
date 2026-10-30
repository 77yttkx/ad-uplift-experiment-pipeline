# PROJECT_LOG

Only record important decisions, meaningful bugs, failed validations, surprising results, plan changes. Not a diary.

## Decision template

- Date:
- Context:
- Decision / Problem:
- Alternatives:
- Why:
- Validation:
- Result:
- Learned:

## Debug template

- Date:
- Problem:
- Diagnosis:
- Root Cause:
- Fix:
- Validation:
- Lesson:

## Negative-result template

- Expected:
- Actual:
- How verified:
- What I checked:
- Changed method or not, and why:
- Business meaning:

---

## Entries

### 2026-10-23: Use treatment assignment, not exposure, as the treatment variable

- Date: 2026-10-23
- Context: Day 1 exploration of the Criteo uplift data (13,979,592 rows). Control has 0 exposed users; only 3.60% of the treatment group is exposed.
- Decision / Problem: Which variable defines the experiment arms, `treatment` or `exposure`?
- Alternatives: Compare exposed users against control or against unexposed users (per-protocol).
- Why: `treatment` is randomly assigned; `exposure` happens after assignment. Treated-but-unexposed users convert at 0.1194%, below control at 0.1938%, and exposed users convert at 5.3784%. Exposure is therefore selective, and exposed-vs-others comparisons would mix the ad effect with who got to see the ad.
- Validation: treatment x exposure table from `src/validation/checks.py` (see RESULTS.md).
- Result: Analyze by assigned arm (intent-to-treat). `exposure` is not used as a model feature.
- Learned: A post-assignment variable can look like a treatment and still not be a valid one.

### 2026-10-23: Move the A/B test math into a tested pure function

- Date: 2026-10-23
- Context: The first `ab_test.py` computed the conversion z-test inline in the script. The visit outcome needed the same test, and the numbers had to be defensible.
- Decision / Problem: Put the two-proportion z-test and the 95% Wald interval in one pure function with input validation, run it for conversion (primary) and visit (secondary), and write the results to JSON.
- Alternatives: Keep the inline script and copy the math for visit.
- Why: One implementation for both outcomes, testable without the 3 GB file, and edge cases (zero baseline rate, zero successes) return explicit `None` values with notes instead of NaN.
- Validation: 31 pytest tests: hand-computed known results, zero successes, equal rates, invalid counts, zero baseline rate, and one regression test pinned to the earlier conversion output. The z, p-value and Wald interval for the conversion counts were also cross-checked against statsmodels in a scratch environment (not a repo dependency).
- Result: Conversion results unchanged after the refactor (z = 28.5165, p = 7.30826e-179). All 31 tests pass.
- Learned: A regression test catches refactor drift but is not independent validation; the hand-computed cases are what supply that.

### 2026-10-23: Rename `statistics.py` to `proportion_tests.py`

- Date: 2026-10-23
- Context: The new module was first named `src/experimentation/statistics.py`, the same name as Python's standard-library `statistics`.
- Decision / Problem: Any `import statistics` run from `src/experimentation/` would pick up the project file instead of the standard library, and `ab_test.py` carried a `sys.path` workaround for it. No library import actually broke in testing (duckdb, numpy, scipy, pandas), so this was a latent risk, not an observed failure.
- Alternatives: Keep the name and the workaround.
- Why: A name collision is a latent bug. Renaming removes the cause instead of working around it.
- Validation: After the rename, `import statistics` from that folder resolves to the standard library (checked in a scratch environment). The full suite still passes (31 tests), and both `python src/experimentation/ab_test.py` and `python -m src.experimentation.ab_test` run without import errors.
- Result: The shadowing part of the workaround is gone. One line, `sys.path.insert(0, <project root>)`, remains because running the file directly puts `src/experimentation/` on the path instead of the project root; without it the direct run fails with `ModuleNotFoundError: No module named 'src'` (tested in a scratch copy). The `-m` form does not need it.
- Learned: Do not name a module after a standard-library module, even inside a package. Scripts run by file path put their own folder first on `sys.path`.

### 2026-10-23: Local verification of the A/B test on the full dataset

- Date: 2026-10-23
- Context: After the refactor and the rename, ran everything locally on a Mac against the real 13,979,592-row Criteo file.
- Validation: All 31 pytest tests pass. Both execution methods run without import errors and produce identical results. Output saved to `outputs/ab_test_results.json` (git-ignored, regenerated on demand).
- Result: Conversion +0.115187 pp (+59.45%), z = 28.5165, p = 7.30826e-179, 95% CI [0.108451%, 0.121924%]. Visit +1.034240 pp (+27.07%), z = 65.2474, 95% CI [1.005563%, 1.062918%]. Full tables in RESULTS.md.
- Problem: The visit p-value underflows to 0.0 in double precision. It is reported as "< 1e-300 (underflow)", not as exactly zero.
- Open: SRM check, MDE / power, variance reduction, uplift modeling, business simulation, and the Parquet / S3 / cloud steps are not done.
- Learned: With millions of users, p-values stop being informative; the effect size and its interval carry the information.

### 2026-10-30: Do not run a formal SRM test on the released dataset

- Date: 2026-10-30
- Context: Day 1 showed an 85/15 treatment/control split, and the plan includes an SRM check. Criteo's dataset page lists a treatment ratio of .846 for the original release and .85 for the un-biased version, says the data was sub-sampled non-uniformly so that the original incrementality level cannot be deduced, and does not state the original randomization probability.
- Decision / Problem: How to check the sample ratio when the prespecified assignment probability of the original experiment is unknown?
- Alternatives: (1) Test the observed counts against 85/15. (2) Test against 50/50. (3) Report counts and proportions descriptively and mark the formal SRM status as not assessable.
- Why: An SRM test is only valid against the probability prespecified for the experiment that produced the data. 85/15 is a property of the release, so option 1 would test whether the release matches its own published ratio, and a "pass" would be uninformative and easy to misread as evidence of valid randomization. Option 2 is simply wrong for this data.
- Validation: The observed counts are 2,096,937 control and 11,882,655 treatment, within 2 users of the published split. That describes the release and does not tell us about the original assignment.
- Result: Option 3. The formal SRM status is `not_assessable`, and no SRM test statistic or p-value was calculated. The A/B results are described as relying on the provider's description of random assignment.
- Learned: A test needs the right null hypothesis. "Not assessable" is a legitimate, defensible result and is not the same as "passed".

### 2026-10-30: SRM diagnostics: implementation and local verification

- Date: 2026-10-30
- Context: Implemented the descriptive sample ratio diagnostics in `src/validation/srm_check.py`, with unit tests in `tests/test_srm_check.py`.
- Implementation: DuckDB counts users per `treatment` group straight from the CSV and rejects data unless the groups are exactly 0 and 1. Pure functions compute the observed proportions, compare them with the published 15/85 split (observed minus reference, no threshold), and decide the assessment. The assessment returns `not_assessable` without a verified assignment probability, and it refuses to return a result if one is supplied, so a formal test has to be added deliberately. The JSON report (`outputs/srm_results.json`, git-ignored) holds the counts, proportions, reference, source limitations and status, and has no p-value or statistic fields.
- Validation: 50 unit tests cover the proportion arithmetic, invalid group counts (zero, negative, non-integer), the comparison with the reference, the assessment logic, the report structure and the DuckDB counting. All 81 tests of the full suite (31 existing + 50 new) pass on a Mac. The 50 new tests also pass in a scratch environment and in a second Linux environment on Python 3.10. Seven deliberate code breakages, including one that claimed a formal test had been performed, were each caught by the tests.
- Result: Run on the full 13,979,592-row file on a Mac. Control 2,096,937 (14.999987%), treatment 11,882,655 (85.000013%), status `not_assessable`, JSON generated. The file size and SHA-256 recorded in the JSON match the README.
- Open: MDE / power, variance reduction, uplift modeling, business simulation, and the Parquet / S3 / cloud steps are not done.
- Learned: Keeping the report free of test fields, and testing for that, makes it hard to later cite a p-value that was never computed.
