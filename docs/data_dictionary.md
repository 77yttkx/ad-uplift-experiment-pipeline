# Data Dictionary

Dataset: Criteo Uplift Prediction Dataset (v2.1). Rows: 13,979,592. Columns: 16. Missing values: 0 in every column.
Treatment share: 85.0% (11,882,655) treatment, 15.0% (2,096,937) control. Visit rate: 4.8543% treatment, 3.8201% control. Conversion rate: 0.3089% treatment, 0.1938% control.

Checked in `src/validation/explore.py` and `src/validation/checks.py`.

| Column | Type | Missing % | Description |
|---|---|---|---|
| f0 to f11 | float (continuous) | 0% | Anonymized user features supplied by Criteo. Their meaning is not disclosed, so no business meaning (age, income, etc.) is assumed. |
| treatment | binary 0/1 | 0% | Random assignment. 1 = user eligible to be shown the ad, 0 = control. This is the experiment arm. |
| exposure | binary 0/1 | 0% | 1 = the ad was actually shown. Only possible in the treatment group (0 exposed users in control). Happens after assignment and is selective, so it is not the arm and not a model feature (data leakage). |
| visit | binary 0/1 | 0% | 1 = user visited the advertiser's site during the observation window. |
| conversion | binary 0/1 | 0% | 1 = user converted. Every converted user also has visit = 1 (0 rows with conversion = 1 and visit = 0). |

## Notes

- Primary outcome: conversion. Secondary outcome: visit.
- The 85/15 split describes the released dataset (Criteo lists a treatment ratio of .85 for this version). It is not a verified prespecified assignment probability, so it is used only as a descriptive reference. The formal SRM status is `not_assessable` (see RESULTS.md).
- Conversion is a rare event (under 0.4% in both groups).
