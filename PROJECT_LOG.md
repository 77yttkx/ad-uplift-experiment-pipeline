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
