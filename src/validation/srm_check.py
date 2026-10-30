"""Sample-ratio diagnostics for the Criteo Uplift v2.1 dataset (descriptive only).

Counts the actual treatment and control users in the CSV with DuckDB, computes the
observed group proportions, and compares them descriptively with the approximate
85/15 split published for the released dataset. It does NOT run a formal sample
ratio mismatch (SRM) test, and the report states why.

Run from the project root (either form works):

    python src/validation/srm_check.py
    python -m src.validation.srm_check

The report is printed and saved to ``outputs/srm_results.json``.

Why the formal SRM status is ``not_assessable``
-----------------------------------------------
An SRM test asks whether observed arm counts are consistent with the assignment
probability that was prespecified for the experiment. Here that probability is
unknown:

* Criteo's dataset page lists a treatment ratio of .85 for the un-biased version
  (.846 for the original release), rounded to two decimals.
* The same page says the data was sub-sampled non-uniformly so that the original
  incrementality level cannot be deduced, and it does not state the original
  randomization probability.

So 85/15 describes the release; it is not a verified prespecified probability.
Testing against it would not be a valid SRM test, and a close match would say
nothing about whether the original assignment was random.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Integral, Real
from pathlib import Path
from typing import Any

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "criteo-uplift-v2.1.csv"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "srm_results.json"

# Approximate split published for the released dataset. These describe the release;
# they are NOT a verified prespecified randomization probability.
REFERENCE_CONTROL_PROPORTION = 0.15
REFERENCE_TREATMENT_PROPORTION = 0.85
REFERENCE_SOURCE_URL = "https://ailab.criteo.com/criteo-uplift-prediction-dataset/"

STATUS_NOT_ASSESSABLE = "not_assessable"

SOURCE_LIMITATIONS = [
    "The reference split (0.15 control / 0.85 treatment) is approximate. Criteo's "
    "dataset page lists a treatment ratio of .85 for the un-biased version (.846 for "
    "the original release), rounded to two decimals. It was read from that web page "
    "on 2026-10-30 and has not been independently verified.",
    "Criteo's page states that the data was sub-sampled non-uniformly so that the "
    "original incrementality level cannot be deduced from the dataset. The arm "
    "proportions of the release therefore need not equal the original randomization "
    "probability.",
    "Criteo's page describes the source as a randomized trial in which a random part "
    "of the population is withheld from advertising, but it does not state the "
    "original randomization probability.",
    "The observed counts come from the CSV in this repository and describe this file only.",
    "Agreement or disagreement between the observed and published proportions "
    "describes the release. It does not show whether the original assignment was random.",
]

REASON_NOT_ASSESSABLE = (
    "A formal SRM test compares observed counts with the treatment probability that "
    "was prespecified for the original experiment. That probability is not available: "
    "the released dataset was sub-sampled non-uniformly by the provider, so its "
    "published treatment share (about 85%) describes the release and cannot be assumed "
    "to equal the original assignment probability. Testing against 85/15 would not be "
    "a valid SRM test."
)

REQUIRED_FOR_FORMAL_TEST = (
    "An independently verified assignment probability that applies to the data as "
    "released: the original prespecified probability plus documentation that the "
    "release did not change the arm proportions, or a documented sampling scheme from "
    "which the expected released proportion can be derived."
)


@dataclass(frozen=True)
class GroupSplit:
    """Observed user counts per arm and the resulting proportions."""

    control_n: int
    treatment_n: int
    total_n: int
    control_proportion: float
    treatment_proportion: float


@dataclass(frozen=True)
class SrmAssessment:
    """Whether a formal SRM test can be run, and why."""

    status: str
    formal_test_performed: bool
    reason: str
    required_for_formal_test: str


def _as_count(name: str, value: Any) -> int:
    """Return ``value`` as an ``int``, rejecting bools, floats and strings."""
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    return int(value)


def _as_probability(name: str, value: Any) -> float:
    """Return ``value`` as a float strictly between 0 and 1."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a number, got {type(value).__name__}")
    if not 0 < value < 1:  # also rejects NaN
        raise ValueError(f"{name} must be strictly between 0 and 1, got {value}")
    return float(value)


def compute_split(control_n: int, treatment_n: int) -> GroupSplit:
    """Compute observed arm proportions from the two group counts.

    Raises:
        TypeError: A count is not an integer (floats and bools are rejected).
        ValueError: A count is zero or negative. A two-arm comparison needs users
            in both arms.
    """
    control = _as_count("control_n", control_n)
    treatment = _as_count("treatment_n", treatment_n)
    for label, n in (("control_n", control), ("treatment_n", treatment)):
        if n <= 0:
            raise ValueError(f"{label} must be positive, got {n}")
    total = control + treatment
    return GroupSplit(
        control_n=control,
        treatment_n=treatment,
        total_n=total,
        control_proportion=control / total,
        treatment_proportion=treatment / total,
    )


def compare_to_reference(
    split: GroupSplit,
    reference_control_proportion: float = REFERENCE_CONTROL_PROPORTION,
    reference_treatment_proportion: float = REFERENCE_TREATMENT_PROPORTION,
) -> dict[str, float]:
    """Describe how far the observed split is from a reference split.

    All differences are observed minus reference. This is a descriptive comparison
    only: nothing here is a hypothesis test and no threshold is applied.

    Raises:
        TypeError: A reference proportion is not a number.
        ValueError: A reference proportion is not strictly between 0 and 1, or the
            two do not sum to 1.
    """
    ref_control = _as_probability("reference_control_proportion", reference_control_proportion)
    ref_treatment = _as_probability(
        "reference_treatment_proportion", reference_treatment_proportion
    )
    if not math.isclose(ref_control + ref_treatment, 1.0, abs_tol=1e-9):
        raise ValueError(
            "reference proportions must sum to 1, got "
            f"{ref_control} + {ref_treatment} = {ref_control + ref_treatment}"
        )

    expected_control = split.total_n * ref_control
    expected_treatment = split.total_n * ref_treatment
    control_diff = split.control_proportion - ref_control
    treatment_diff = split.treatment_proportion - ref_treatment
    return {
        "control_proportion_difference": control_diff,
        "treatment_proportion_difference": treatment_diff,
        "control_difference_percentage_points": control_diff * 100,
        "treatment_difference_percentage_points": treatment_diff * 100,
        "expected_control_n_at_reference": expected_control,
        "expected_treatment_n_at_reference": expected_treatment,
        "control_n_difference": split.control_n - expected_control,
        "treatment_n_difference": split.treatment_n - expected_treatment,
    }


def assess_srm(verified_assignment_probability: float | None = None) -> SrmAssessment:
    """Decide whether a formal SRM test can be run.

    A formal test needs an independently verified assignment probability for the
    data as released. Without one the status is ``not_assessable`` and no test is
    run. The project has no such probability, so ``None`` is the normal input.

    Raises:
        TypeError / ValueError: ``verified_assignment_probability`` is given but is
            not a number strictly between 0 and 1.
        NotImplementedError: A valid probability is given. The formal test is
            intentionally not implemented until a verified probability exists, so
            it has to be added deliberately and reviewed rather than slipped in.
    """
    if verified_assignment_probability is None:
        return SrmAssessment(
            status=STATUS_NOT_ASSESSABLE,
            formal_test_performed=False,
            reason=REASON_NOT_ASSESSABLE,
            required_for_formal_test=REQUIRED_FOR_FORMAL_TEST,
        )
    _as_probability("verified_assignment_probability", verified_assignment_probability)
    raise NotImplementedError(
        "A formal SRM test is not implemented. Add it, with its provenance checks, "
        "only once an independently verified assignment probability exists."
    )


def build_report(
    split: GroupSplit, comparison: dict[str, float], assessment: SrmAssessment
) -> dict[str, Any]:
    """Assemble the JSON-ready report body (no file access, no timestamps)."""
    return {
        "observed": {
            "control": {"n": split.control_n, "proportion": split.control_proportion},
            "treatment": {"n": split.treatment_n, "proportion": split.treatment_proportion},
            "total_n": split.total_n,
        },
        "reference": {
            "control_proportion": REFERENCE_CONTROL_PROPORTION,
            "treatment_proportion": REFERENCE_TREATMENT_PROPORTION,
            "description": (
                "Approximate split published for the released dataset, rounded. "
                "It describes the release, not the original experiment."
            ),
            "source_url": REFERENCE_SOURCE_URL,
            "verified_assignment_probability": None,
        },
        "descriptive_comparison": {
            "convention": "observed minus reference",
            **comparison,
            "note": "Descriptive only. No hypothesis test was performed.",
        },
        "assessment": {
            "srm_status": assessment.status,
            "formal_test_performed": assessment.formal_test_performed,
            "reason": assessment.reason,
            "required_for_formal_test": assessment.required_for_formal_test,
        },
        "source_limitations": list(SOURCE_LIMITATIONS),
    }


def fetch_group_counts(data_path: Path) -> tuple[int, int]:
    """Count control (0) and treatment (1) users directly from the CSV with DuckDB.

    Returns ``(control_n, treatment_n)``. Raises ``ValueError`` unless the
    ``treatment`` column holds exactly the values 0 and 1.
    """
    con = duckdb.connect()
    try:
        rows = con.execute(
            """
            SELECT treatment, COUNT(*) AS n
            FROM read_csv_auto(?, header=true)
            GROUP BY treatment
            ORDER BY treatment
            """,
            [str(data_path)],
        ).fetchall()
    finally:
        con.close()

    groups = [row[0] for row in rows]
    if groups != [0, 1]:
        raise ValueError(f"Expected exactly two treatment groups, 0 and 1; got {groups}")
    return int(rows[0][1]), int(rows[1][1])


def sha256_of_file(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
    return digest.hexdigest()


def print_summary(
    split: GroupSplit, comparison: dict[str, float], assessment: SrmAssessment
) -> None:
    print("\n=== SAMPLE RATIO DIAGNOSTICS (descriptive) ===")
    print(f"Control:   {split.control_n:,} ({split.control_proportion:.6%})")
    print(f"Treatment: {split.treatment_n:,} ({split.treatment_proportion:.6%})")
    print(f"Total:     {split.total_n:,}")
    print(
        f"\nPublished approximate split: {REFERENCE_CONTROL_PROPORTION:.0%} control / "
        f"{REFERENCE_TREATMENT_PROPORTION:.0%} treatment"
    )
    print(
        "Observed minus reference: "
        f"control {comparison['control_difference_percentage_points']:+.6f} pp "
        f"({comparison['control_n_difference']:+,.1f} users), "
        f"treatment {comparison['treatment_difference_percentage_points']:+.6f} pp "
        f"({comparison['treatment_n_difference']:+,.1f} users)"
    )
    print("This is a descriptive comparison, not a hypothesis test.")
    print(f"\nFormal SRM status: {assessment.status}")
    print(textwrap.fill(assessment.reason, width=88))


def run(data_path: Path, output_path: Path) -> dict[str, Any]:
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset not found: {data_path}")

    control_n, treatment_n = fetch_group_counts(data_path)
    split = compute_split(control_n, treatment_n)
    comparison = compare_to_reference(split)
    assessment = assess_srm()
    print_summary(split, comparison, assessment)

    report = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": "src/validation/srm_check.py",
            "dataset": {
                "name": "Criteo Uplift Prediction Dataset v2.1",
                "file": data_path.name,
                "size_bytes": data_path.stat().st_size,
                "sha256": sha256_of_file(data_path),
                "total_users": split.total_n,
            },
            "environment": {
                "python": platform.python_version(),
                "duckdb": duckdb.__version__,
            },
            "methodology": {
                "counting": "COUNT(*) grouped by treatment, computed in DuckDB directly from the CSV",
                "comparison": "descriptive differences from the published approximate split; no hypothesis test",
            },
        },
        **build_report(split, comparison, assessment),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")

    print(f"\nSaved: {output_path}")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH, help="Criteo CSV path")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON report path"
    )
    args = parser.parse_args(argv)
    run(args.data, args.output)


if __name__ == "__main__":
    main()
