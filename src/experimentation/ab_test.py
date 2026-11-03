"""A/B test of the randomized ad assignment on the Criteo Uplift v2.1 dataset.

Runs a two-sided two-proportion z-test (with a 95% unpooled Wald interval) for
the primary outcome (conversion) and the secondary outcome (visit), comparing
users by their randomized ``treatment`` assignment. Counts are aggregated inside
DuckDB, so the CSV is never loaded into pandas.

Run from the project root (either form works):

    python src/experimentation/ab_test.py
    python -m src.experimentation.ab_test

Results are printed and saved to ``outputs/ab_test_results.json``.

Interpretation limits
---------------------
* The Criteo public dataset is a released research sample. Its sampling and any
  preprocessing done by the provider cannot be verified here, and the sample may
  not represent Criteo's full user base, other advertisers, or other time periods.
* These comparisons describe the randomized assignment inside this dataset
  (intent-to-treat). They are NOT validated population-level causal effects and
  should not be presented as such.
* ``exposure`` happens after assignment and is selective, so it is not used to
  define the groups.
* p-values and intervals are not adjusted for testing two outcomes.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Running this file directly puts src/experimentation/ on sys.path, not the project
# root, so `from src...` below would fail. `python -m src.experimentation.ab_test`
# run from the project root needs no help.
if not __package__:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import platform  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import duckdb  # noqa: E402
import numpy  # noqa: E402
import scipy  # noqa: E402

from src.experimentation.proportion_tests import (  # noqa: E402
    METHOD_CONFIDENCE_INTERVAL,
    METHOD_SIGNIFICANCE_TEST,
    TwoProportionResult,
    two_proportion_ztest,
)

DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "criteo-uplift-v2.1.csv"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "ab_test_results.json"
CONFIDENCE_LEVEL = 0.95

# (column, role). Conversion is the primary outcome; visit is secondary.
OUTCOMES = (("conversion", "primary"), ("visit", "secondary"))

LIMITATIONS = [
    "The Criteo public dataset is a released research sample. Its sampling and any "
    "preprocessing by the data provider cannot be verified here, and it may not "
    "represent the full user population, other advertisers, or other time periods.",
    "These statistics compare the randomized assignment (treatment = 1 vs 0) inside "
    "this dataset only. They are not validated population-level causal effects and "
    "should not be presented as such.",
    "Intervals and p-values reflect sampling variance within this dataset only, not "
    "how users were selected into it.",
    "Exposure is a post-assignment, selective variable and is not used to define the groups.",
    "No multiplicity adjustment is applied across the two outcomes; visit is secondary.",
]


def fetch_group_counts(data_path: Path) -> dict[int, dict[str, int]]:
    """Aggregate user counts and successes per treatment group inside DuckDB.

    Returns ``{0: {...}, 1: {...}}`` with keys ``n`` and one success count per
    outcome. Raises ``ValueError`` if the data does not look as expected: an arm
    other than 0/1, a missing value, or an outcome value that is not exactly 0 or
    1. The binary check runs on the parsed (lossless) value, before the cast to an
    integer used for the sum, because ``CAST`` rounds a fractional value instead of
    refusing it.
    """
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT
                treatment,
                COUNT(*)                          AS n,
                COUNT(conversion)                 AS conversion_non_null,
                COUNT(visit)                      AS visit_non_null,
                SUM(CASE WHEN conversion NOT IN (0, 1) THEN 1 ELSE 0 END)
                                                  AS conversion_non_binary,
                SUM(CASE WHEN visit NOT IN (0, 1) THEN 1 ELSE 0 END)
                                                  AS visit_non_binary,
                SUM(CAST(conversion AS BIGINT))   AS conversion_successes,
                SUM(CAST(visit AS BIGINT))        AS visit_successes
            FROM read_csv_auto(?, header=true)
            GROUP BY treatment
            ORDER BY treatment
            """,
            [str(data_path)],
        ).fetchall()
    finally:
        con.close()

    if [row[0] for row in rows] != [0, 1]:
        raise ValueError(
            f"Expected exactly two treatment groups, 0 and 1; got {[row[0] for row in rows]}"
        )

    counts: dict[int, dict[str, int]] = {}
    for (group, n, conv_nn, visit_nn, conv_non_binary, visit_non_binary,
         conv_successes, visit_successes) in rows:
        if conv_nn != n or visit_nn != n:
            raise ValueError(f"Missing conversion/visit values in treatment group {group}")
        for outcome, offenders in (("conversion", conv_non_binary), ("visit", visit_non_binary)):
            if offenders:
                raise ValueError(
                    f"{outcome} must be exactly 0 or 1 in treatment group {group}: "
                    f"{int(offenders)} row(s) hold another value"
                )
        counts[int(group)] = {
            "n": int(n),
            "conversion": int(conv_successes),
            "visit": int(visit_successes),
        }
    return counts


def sha256_of_file(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def outcome_report(
    outcome: str, role: str, counts: dict[int, dict[str, int]]
) -> tuple[dict, TwoProportionResult]:
    """Run the test for one outcome and build its JSON-ready block."""
    result = two_proportion_ztest(
        control_successes=counts[0][outcome],
        control_n=counts[0]["n"],
        treatment_successes=counts[1][outcome],
        treatment_n=counts[1]["n"],
        confidence_level=CONFIDENCE_LEVEL,
    )
    block = {
        "role": role,
        "control": {
            "n": result.control_n,
            "successes": result.control_successes,
            "rate": result.control_rate,
        },
        "treatment": {
            "n": result.treatment_n,
            "successes": result.treatment_successes,
            "rate": result.treatment_rate,
        },
        "effect": {
            "absolute_lift": result.absolute_lift,
            "absolute_lift_percentage_points": result.absolute_lift * 100,
            "relative_lift": result.relative_lift,
            "relative_lift_percent": (
                None if result.relative_lift is None else result.relative_lift * 100
            ),
        },
        "test": {
            "z_statistic": result.z_statistic,
            "p_value": result.p_value,
            "alternative": "two-sided",
        },
        "confidence_interval": {
            "level": result.confidence_level,
            "lower": result.ci_lower,
            "upper": result.ci_upper,
            "lower_percentage_points": result.ci_lower * 100,
            "upper_percentage_points": result.ci_upper * 100,
        },
        "notes": list(result.notes),
    }
    return block, result


def print_outcome(outcome: str, role: str, r: TwoProportionResult) -> None:
    rel = "undefined" if r.relative_lift is None else f"{r.relative_lift:.2%}"
    z = "undefined" if r.z_statistic is None else f"{r.z_statistic:.4f}"
    p = "undefined" if r.p_value is None else f"{r.p_value:.6g}"
    print(f"\n=== A/B TEST: {outcome.upper()} ({role}) ===")
    print(f"Control:   {r.control_successes:,} / {r.control_n:,} = {r.control_rate:.6%}")
    print(f"Treatment: {r.treatment_successes:,} / {r.treatment_n:,} = {r.treatment_rate:.6%}")
    print(f"Absolute lift: {r.absolute_lift:.6%}")
    print(f"Relative lift: {rel}")
    print(f"Z-statistic: {z}")
    print(f"P-value: {p}")
    print(
        f"{r.confidence_level:.0%} CI for absolute lift: "
        f"[{r.ci_lower:.6%}, {r.ci_upper:.6%}]"
    )
    for note in r.notes:
        print(f"Note: {note}")


def run(data_path: Path, output_path: Path) -> dict:
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset not found: {data_path}")

    counts = fetch_group_counts(data_path)

    results: dict[str, dict] = {}
    for outcome, role in OUTCOMES:
        block, result = outcome_report(outcome, role, counts)
        results[outcome] = block
        print_outcome(outcome, role, result)

    report = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": "src/experimentation/ab_test.py",
            "dataset": {
                "name": "Criteo Uplift Prediction Dataset v2.1",
                "file": data_path.name,
                "size_bytes": data_path.stat().st_size,
                "sha256": sha256_of_file(data_path),
                "total_users": counts[0]["n"] + counts[1]["n"],
            },
            "environment": {
                "python": platform.python_version(),
                "duckdb": duckdb.__version__,
                "numpy": numpy.__version__,
                "scipy": scipy.__version__,
            },
            "methodology": {
                "unit_of_analysis": "user",
                "groups": "treatment = 1 vs treatment = 0 (randomized assignment, intent-to-treat)",
                "significance_test": METHOD_SIGNIFICANCE_TEST,
                "confidence_interval": METHOD_CONFIDENCE_INTERVAL,
                "confidence_level": CONFIDENCE_LEVEL,
                "alpha": round(1 - CONFIDENCE_LEVEL, 10),
                "absolute_lift": "treatment rate minus control rate, as a proportion",
                "relative_lift": "absolute lift divided by control rate; null if control rate is 0",
                "primary_outcome": "conversion",
                "secondary_outcomes": ["visit"],
                "multiplicity": "none applied",
                "aggregation": "counts computed in DuckDB directly from the CSV",
            },
            "limitations": LIMITATIONS,
        },
        "results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")

    print("\n--- Interpretation limits ---")
    for line in LIMITATIONS[:2]:
        print(f"* {line}")
    print(f"\nSaved: {output_path}")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH, help="Criteo CSV path")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON results path"
    )
    args = parser.parse_args(argv)
    run(args.data, args.output)


if __name__ == "__main__":
    main()
