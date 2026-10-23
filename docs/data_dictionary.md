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
- The 85/15 split is part of the dataset design. SRM checks must use 85/15 as the expected ratio.
- Conversion is a rare event (under 0.4% in both groups).
