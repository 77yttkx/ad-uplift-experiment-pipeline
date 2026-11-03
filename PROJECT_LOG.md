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

### 2026-10-30: Power analysis and MDE: implementation and local verification

- Date: 2026-10-30
- Context: Implemented statistical power and minimum detectable effect (MDE) calculations for two independent proportions in `src/experimentation/power_analysis.py`, with unit tests in `tests/test_power_analysis.py`. The existing A/B test and SRM code were not changed.
- Implementation: Two-sided z-test power with unequal group sizes under a normal approximation. The null standard error is pooled at the expected pooled probability, the alternative standard error is unpooled, and both rejection tails are counted. The MDE is solved with `scipy.optimize.brentq` (no equal-allocation or fixed effect-size shortcut); inputs outside their ranges, a target power not above alpha, and an unreachable power all raise errors, and a non-converged solver raises `RuntimeError`. The script reads no dataset, writes `outputs/power_analysis_results.json` (git-ignored) with assumptions, observed data, results, post-hoc and hypothetical sections kept separate, and compares its constants with `outputs/ab_test_results.json`.
- Result: At alpha = 0.05 and power = 0.8 with control n = 2,096,937, treatment n = 11,882,655 and baseline 0.193759%: absolute MDE 9.3692e-05 (0.009369 pp), relative MDE 4.8355%. Power at 1%, 5%, 10% and 20% relative lift: 0.0893, 0.8255, 0.999931, 1.0. Hypothetical 50/50 vs 15/85 split at fixed total n: relative MDE 3.430% vs 4.835%. Post-hoc power at the observed +59.45% lift is 1.0; it is descriptive only and does not validate the experiment.
- Independent numerical cross-checks: Power values matched `statsmodels.stats.proportion.power_proportions_2indep` (statsmodels 0.15.0, installed only in a scratch environment, not a project dependency) to about 1e-15 relative error for equal and unequal group sizes, including swapped group roles, and statsmodels returned power 0.8000000000003 at the solved MDE. A separate standard-library reference implementation (`math.erfc` / `statistics.NormalDist`) and a plain bisection search reproduce the power and MDE in the tests. A textbook case (10% vs 15%, 80% power) needs 686 per group: 685 falls just short. Reference constants from statsmodels are hard-coded in the tests. Nine deliberate code breakages (dropped tail, wrong critical value, wrong standard error, wrong pooling, removed validation, removed convergence check, narrowed solver bracket) were checked; eight were caught at once. One, removing the convergence check, initially survived because scipy raised its own error; `disp=False` was added so the module's own check is the one that fires, and the breakage is now caught.
- Validation: 72 new tests cover input validation, power monotonicity, unequal allocation, solver convergence, reference cases, the consistency check and the report structure. The full suite (153 tests: 81 existing + 72 new) passed on a Mac in 3.42 s and also in a scratch environment. The JSON was generated on the Mac and `observed_data.consistency_with_ab_test_results.status` was `consistent` (control 2,096,937 / 4,063; treatment 11,882,655 / 36,711).
- Limitations: Criteo sub-sampled the public data non-uniformly and the original assignment probability is unverified (SRM `not_assessable`), so these are illustrative conditional sensitivity analyses, not verified power of the original experimental design. The baseline comes from the same data and its sampling error is not propagated. Independent simple-random assignment is assumed. Only conversion is covered, and with 3.60% exposure an intent-to-treat effect is diluted. Post-hoc power adds nothing beyond the p-value. Allocation scenarios are hypothetical.
- Open: Variance reduction, uplift modeling, business simulation, and the Parquet / S3 / cloud steps are not done.
- Learned: Post-hoc power at a lift 12 times the MDE is uninformative; the MDE and the power curve under stated assumptions are the useful outputs. A mutation check also exposed a library default (scipy raising its own error) that hid an untested branch.

### 2026-11-03: CSV to Parquet conversion: implementation and local verification

- Date: 2026-11-03 (the local run recorded in the report was generated 2026-10-30T21:51:20+00:00)
- Context: A1 requires a validated Parquet copy of the raw data whose row count matches the CSV. Implemented `src/ingestion/csv_to_parquet.py` with tests in `tests/test_csv_to_parquet.py`. The machine has 8 GB of RAM and the CSV is 3.0 GiB, so the data must not be materialised in pandas.
- Implementation: DuckDB reads the CSV and writes ZSTD-compressed Parquet in one streaming `COPY`, with a configurable memory limit (default 3GB) and a temporary directory for spilling. The output is written under a temporary name and renamed only after every check passes, so a failed run leaves no Parquet file. A JSON report records source and output sizes and SHA-256 checksums, the schema, per-column profiles and every check.
- Validation: Header names; parse without rounding; no missing values, non-finite features or non-binary values; independent line count vs. DuckDB row count; counts verified earlier (rows, group sizes, conversions, visits, exposure); then, on the Parquet, row count, column names, order and types, min/max, per-group sums and a content fingerprint equal to the CSV's. 64 new tests on synthetic files; 12 deliberate code breakages were checked, and 3 initially survived until tests for the output-validation failure path were added. The full suite (217 tests) passed on the Mac.
- Result: Local run on the full file. 13,979,592 rows in and out; CSV 3,248,115,221 bytes; Parquet 185,895,062 bytes (114 row groups, ZSTD), 94.28% smaller; 28 of 28 checks passed; 22.5 s end to end on one Mac. The CSV SHA-256 in the report equals the value recorded in the README. All binary columns contained only 0 and 1, with no missing values.
- Alternatives considered: Loading the CSV with pandas (rejected: 8 GB RAM); letting DuckDB infer types with `read_csv_auto` (rejected: the inferred types are not an explicit schema and cannot be asserted against); parsing the binary columns directly as INTEGER (rejected after the bug below).
- Limitations: The check shows the Parquet equals the CSV as parsed with this schema, not that the dataset is correct or representative. The content fingerprint is a consistency check, not cryptographic. The runtime is one run on one machine.
- Open: The Parquet file is local only. S3 upload, Snowflake loading and dbt are not done, and the A1 gate (S3 object, tag `v0.1`) is not passed. The next A1 step is AWS S3.
- Learned: Reconciling row counts alone would not have caught a value change; checking values (binary domain, group sums, content fingerprint) is what makes "the conversion preserved the data" a testable claim.

### 2026-11-03: DuckDB silently coerced fractional values in binary columns

- Date: 2026-11-03
- Problem: The first version of the converter read the four binary columns (treatment, conversion, visit, exposure) with an explicit INTEGER type, expecting a malformed value to fail the run. A unit test that put `0.5` in the `visit` column expected the run to fail. It passed.
- Diagnosis: The test, with DuckDB 1.5.6, showed that the run reported `passed` and wrote a Parquet file. A fractional value parsed into an INTEGER column had been accepted instead of raising an error. An integer column can only hold a whole number, so the stored value was no longer the value in the CSV. A later binary check on the integer data could not see the problem, because by then the value looked like a valid 0 or 1.
- Root Cause: Declaring an integer type on read is not a validation. The parser converts the value to fit the declared type, so the information needed to detect a non-binary value was discarded before the check ran. I had assumed the parser would be strict. I did not check that assumption until the test failed.
- Fix: Read every column as DOUBLE, so nothing is rounded during parsing. Check that the binary columns are exactly 0 or 1 in that representation. Only then cast them to INTEGER for the output. The content fingerprint hashes values after the same cast on both sides, so CSV and Parquet are compared as they are stored.
- Validation: With the original INTEGER parse restored as a deliberate breakage, 17 tests fail, including the `0.5` test. With the fix, `0.5`, `2` and `-1` in any binary column all fail the run with no Parquet file written, and the failed check names the column. On the real file, the binary check ran on the DOUBLE parse and found 0 non-binary values in every binary column, so no real value was altered. I observed this behaviour on DuckDB 1.5.6, the version pinned in `requirements.txt`; I did not investigate whether other versions behave differently.
- Lesson: Test the validation, not only the happy path. A type declaration changes the data it is meant to check, so validate on a lossless representation first and convert afterwards. The unit test with an adversarial value found the problem before it could reach the real data.

### 2026-11-03: Store the raw Parquet in a private S3 bucket and verify it

- Date: 2026-11-03
- Context: A1 requires the raw Parquet file to be in S3, with row counts reconciled, before the project moves to Snowflake and dbt in A2. The validated Parquet file (185,895,062 bytes, SHA-256 `f2e6f26b68cd...`) was only local.
- Decision / Problem: Where and how to store the raw layer safely, without putting credentials or data in the repository.
- Implementation: A private S3 bucket (`ad-uplift-projecta-7k29m`) with S3 Block Public Access on and SSE-S3 encryption. The file was uploaded to `raw/criteo-uplift-v2.1.parquet`. The upload used a dedicated IAM user (`projecta-s3-uploader`) through a named AWS CLI profile (`projecta`), not the root user. MFA was enabled on the AWS root user and a budget alert was configured. No access keys, secret keys, account ID or credential files are in the repository.
- Why: A private, encrypted bucket keeps the raw data from being publicly readable (the dataset's licence also limits redistribution). A dedicated IAM user with limited permissions means a leaked key could do less than root credentials could, and the root user stays protected by MFA. A budget alert limits the cost of a mistake. The intent for the IAM user is least privilege, meaning only the S3 access the upload needs.
- Validation: The S3 object's `ContentLength` equals the local size (185,895,062 bytes). The object was downloaded back and its SHA-256 equals the local SHA-256 exactly (`f2e6f26b68cdbb1ff4f04f58d095f9792b15355dc093d86e56447cf806e42a12`). The local file had been validated earlier against the CSV (28 of 28 checks, see the 2026-11-03 conversion entry), so the S3 copy is byte-identical to a validated file. The full local test suite (217 tests) had passed before this step. The S3 checks themselves were run by hand, not by a test.
- Result: `s3://ad-uplift-projecta-7k29m/raw/criteo-uplift-v2.1.parquet` holds the validated Parquet file. RESULTS.md records the bucket, key, size and checksum verification.
- Cost considerations: The stored object is 185,895,062 bytes (about 0.17 GiB), roughly 17.5 times smaller than the CSV, so it keeps storage and any later transfer small. A budget alert is configured; its amount is not recorded here. Costs for later steps (Snowflake compute, data transfer out of S3) are not assessed yet.
- Limitations: The IAM policy document is not reproduced in this repository, so the exact permissions of `projecta-s3-uploader` are not documented here. Verification was manual. This is storage only.
- Open: Snowflake trial, storage integration and external stage, `COPY INTO`, dbt models and tests, `docs` for A2, the A1 close-out documents (resume draft, interview notes, Evidence Check) and tag `v0.1-data-foundation` are not done. Snowflake and dbt are not started.
- Learned: Comparing the SHA-256 of the object downloaded from S3 with the local file checks the bytes that are actually stored, which a successful upload command alone does not show.

### 2026-11-03: Verify the IAM policy and AWS Budget in the console

- Date: 2026-11-03
- Context: The earlier S3 entry (2026-11-03) said the IAM policy of `projecta-s3-uploader` and the budget amount were not recorded in the repository. This entry replaces both gaps with what was read in the AWS Console on 2026-11-03.
- IAM policy (verified in the console): Customer-managed policy `ProjectA-S3-ReadWrite`, default version 1, attached to the IAM user `projecta-s3-uploader`. The exact JSON is saved in `docs/aws_iam_policy.json`. It has two statements. `ListProjectBucket` allows `s3:ListBucket` and `s3:GetBucketLocation` on the one bucket. `ReadWriteProjectObjects` allows `s3:GetObject` and `s3:PutObject` on the objects in that bucket (`bucket/*`). It does not grant `s3:DeleteObject`, S3 administrator access, or access to any other bucket. The ARNs contain the bucket name and no account ID.
- Why this design: The uploader needs to put the Parquet file in the bucket and to read it back for the checksum check, and nothing else. Scoping the actions and resources to that bucket limits what a leaked key could do. Leaving out delete means a leaked key could not delete objects through this policy (it could still overwrite them, because `PutObject` replaces an object with the same key).
- AWS Budget (verified in the console): One monthly cost budget of $5 USD with three alerts: actual cost at 85% ($4.25), actual cost at 100% ($5.00), and forecasted cost at 100% ($5.00).
- Validation: The policy JSON in the repo parses as valid JSON and matches the console policy statement for statement. This is a check of what the console showed on one day, not an audit.
- What was NOT verified: The user's full effective permissions were not audited. Other attached or inline policies, group memberships, permissions boundaries and the bucket's own policy were not reviewed, so the claim is limited to "this policy is attached and is bucket-scoped". Whether the alert notifications are delivered to a working address was not tested. The bucket's versioning status was not recorded.
- Cost considerations: AWS Budgets only sends notifications. It is not a hard spending cap, it does not stop services, and a budget alert does not mean spending is zero or that the project cannot incur charges. Charges can still build up between an alert and a response, and costs from later steps (Snowflake compute, data transfer out of S3) are not assessed yet. The stored object is about 0.17 GiB. The remedy for a surprise is to check the console and delete resources, not to rely on the alerts.
- Open: A1 close-out documents and the `v0.1-data-foundation` tag (not created). Snowflake and dbt are not started.
- Learned: Documenting an access policy means saving the policy text itself. A description such as "least privilege" is a claim, and the JSON is the evidence for it.
