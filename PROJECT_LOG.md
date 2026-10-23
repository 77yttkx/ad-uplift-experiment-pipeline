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
