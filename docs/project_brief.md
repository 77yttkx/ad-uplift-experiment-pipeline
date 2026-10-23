# Project Brief

## Business question
How much incremental conversion does the ad drive, for whom, and is targeting only the top-k% of users worth it?

## Data
Criteo Uplift Prediction Dataset (v2.1): 13,979,592 users from a randomized ad experiment, 85% treatment / 15% control. See `docs/data_dictionary.md`.

## Hypotheses
- H0: assigning users to the ad has no effect on conversion rate (ATE = 0).
- H1: assigning users to the ad changes the conversion rate (ATE != 0, two-sided).
- Exploratory: the effect differs across users (heterogeneous effect), so ranking users by predicted uplift beats targeting at random.

## Metric definitions (every metric needs numerator and denominator)
| Metric | Numerator | Denominator |
|---|---|---|
| Conversion rate (primary) | users with conversion = 1 in the arm | all users in the arm (by assignment) |
| Visit rate (secondary) | users with visit = 1 in the arm | all users in the arm (by assignment) |
| Exposure rate (diagnostic only, not an outcome) | users with exposure = 1 in the arm | all users in the arm |

Arms are defined by `treatment` (random assignment), not by `exposure`.

## Success criteria
- A1: raw data loaded, validated and reconciled; row counts match across every stage.
- A3: effect estimate with 95% CI and p-value; SRM check passed with an 85/15 expected ratio; MDE and power reported.
- A4: uplift model beats a random-targeting baseline on a held-out set (Qini / AUUC).
- A5: top-k% policy compared against targeting everyone, with assumed cost and value labeled as simulation.
