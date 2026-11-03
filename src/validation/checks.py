"""Follow-up checks on the Criteo uplift dataset (descriptive, read-only).

1. Binary validation: treatment, conversion, visit, exposure contain only 0/1.
2. Outcome consistency: relationship between visit and conversion.
3. Exposure vs. treatment: exposure is a post-assignment variable, not the randomized treatment.

Nothing runs at import time, so the module can be imported (and tested) without the
3 GiB dataset.

Run from the project root (either form works):

    python src/validation/checks.py
    python -m src.validation.checks

The findings are printed and saved as JSON to ``outputs/checks_results.json``
(``--output`` to change).

Exit codes: 0 when every binary column holds only 0/1, 1 when a column does not
(the printed PROBLEM line names it), 2 when the dataset is missing or an option is
invalid.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if not __package__:  # run as a script: `src` is not importable from src/validation/
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse  # noqa: E402
import json  # noqa: E402
from typing import Any  # noqa: E402

import pandas  # noqa: E402

from src.validation.dataset_source import (  # noqa: E402
    BINARY_COLUMNS,
    DEFAULT_DATA_PATH,
    VIEW_NAME,
    as_number,
    connect,
    quote,
)

DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "checks_results.json"
BINARY_COLS = BINARY_COLUMNS


def _float_or_none(value: Any) -> float | None:
    """A ratio stays ``None`` when the arm has no rows to average (all values NULL)."""
    return None if value is None else float(value)


def binary_validation(con) -> dict[str, dict[str, Any]]:
    """MIN, MAX, distinct count and non-binary row count for every binary column."""
    select_parts = []
    for column in BINARY_COLS:
        quoted = quote(column)
        select_parts.append(f"MIN({quoted}) AS {column}_min")
        select_parts.append(f"MAX({quoted}) AS {column}_max")
        select_parts.append(f"COUNT(DISTINCT {quoted}) AS {column}_distinct")
        select_parts.append(
            f"SUM(CASE WHEN {quoted} NOT IN (0, 1) THEN 1 ELSE 0 END) AS {column}_non_binary"
        )
    cursor = con.execute(f"SELECT {', '.join(select_parts)} FROM {quote(VIEW_NAME)}")
    stats = dict(zip([description[0] for description in cursor.description], cursor.fetchone()))
    return {
        column: {
            "min": stats[f"{column}_min"],
            "max": stats[f"{column}_max"],
            "distinct": int(stats[f"{column}_distinct"]),
            "non_binary_rows": int(stats[f"{column}_non_binary"]),
        }
        for column in BINARY_COLS
    }


def visit_conversion(con) -> list[dict[str, int]]:
    return [
        {
            "visit": as_number(visit),
            "conversion": as_number(conversion),
            "rows": int(rows),
        }
        for visit, conversion, rows in con.execute(
            f"""
            SELECT visit, conversion, COUNT(*) AS rows
            FROM {quote(VIEW_NAME)}
            GROUP BY visit, conversion
            ORDER BY visit, conversion
            """
        ).fetchall()
    ]


def exposure_by_treatment(con) -> list[dict[str, Any]]:
    return [
        {
            "treatment": as_number(treatment),
            "users": int(users),
            "exposed_users": as_number(exposed_users),
            "exposure_rate": _float_or_none(exposure_rate),
            "visit_rate": _float_or_none(visit_rate),
            "conversion_rate": _float_or_none(conversion_rate),
        }
        for treatment, users, exposed_users, exposure_rate, visit_rate, conversion_rate in con.execute(
            f"""
            SELECT
                treatment,
                COUNT(*) AS users,
                SUM(exposure) AS exposed_users,
                AVG(CAST(exposure AS DOUBLE)) AS exposure_rate,
                AVG(CAST(visit AS DOUBLE)) AS visit_rate,
                AVG(CAST(conversion AS DOUBLE)) AS conversion_rate
            FROM {quote(VIEW_NAME)}
            GROUP BY treatment
            ORDER BY treatment
            """
        ).fetchall()
    ]


def treatment_x_exposure(con) -> list[dict[str, Any]]:
    return [
        {
            "treatment": as_number(treatment),
            "exposure": as_number(exposure),
            "users": int(users),
            "visit_rate": _float_or_none(visit_rate),
            "conversion_rate": _float_or_none(conversion_rate),
        }
        for treatment, exposure, users, visit_rate, conversion_rate in con.execute(
            f"""
            SELECT
                treatment,
                exposure,
                COUNT(*) AS users,
                AVG(CAST(visit AS DOUBLE)) AS visit_rate,
                AVG(CAST(conversion AS DOUBLE)) AS conversion_rate
            FROM {quote(VIEW_NAME)}
            GROUP BY treatment, exposure
            ORDER BY treatment, exposure
            """
        ).fetchall()
    ]


def run_checks(data_path: str | Path = DEFAULT_DATA_PATH) -> dict[str, Any]:
    """Run all three checks on one CSV. Opens and closes its own connection.

    Raises:
        FileNotFoundError: ``data_path`` does not exist.
    """
    path = Path(data_path)
    con = connect(path)
    try:
        binary = binary_validation(con)
        correlation = visit_conversion(con)
        conversion_without_visit = int(
            con.execute(
                f"SELECT COUNT(*) FROM {quote(VIEW_NAME)} WHERE conversion = 1 AND visit = 0"
            ).fetchone()[0]
        )
        by_treatment = exposure_by_treatment(con)
        matrix = treatment_x_exposure(con)
        control_exposed = int(
            con.execute(
                f"SELECT COUNT(*) FROM {quote(VIEW_NAME)} WHERE treatment = 0 AND exposure = 1"
            ).fetchone()[0]
        )
    finally:
        con.close()

    all_binary = all(entry["non_binary_rows"] == 0 for entry in binary.values())
    return {
        "dataset": {"file": path.name},
        "binary_validation": {"columns": binary, "all_binary": all_binary},
        "visit_conversion": correlation,
        "conversion_without_visit": conversion_without_visit,
        "exposure_by_treatment": by_treatment,
        "treatment_exposure": matrix,
        "control_exposed": control_exposed,
        "status": "passed" if all_binary else "failed",
    }


def print_checks(report: dict[str, Any]) -> None:
    """Print the same sections the script always printed."""
    print("\n=== 1. BINARY VALIDATION ===")
    for column, entry in report["binary_validation"]["columns"].items():
        ok = entry["non_binary_rows"] == 0
        print(
            f"{column}: min={entry['min']}, max={entry['max']}, "
            f"distinct={entry['distinct']}, non-binary rows={entry['non_binary_rows']:,} "
            f"-> {'OK' if ok else 'PROBLEM'}"
        )
    print("All four columns binary:", report["binary_validation"]["all_binary"])

    print("\n=== 2. VISIT x CONVERSION ===")
    print(
        pandas.DataFrame(
            report["visit_conversion"], columns=["visit", "conversion", "rows"]
        ).to_string(index=False)
    )
    print(f"\nconversion = 1 AND visit = 0: {report['conversion_without_visit']:,} rows")

    print("\n=== 3. EXPOSURE BY TREATMENT ===")
    print(
        pandas.DataFrame(
            report["exposure_by_treatment"],
            columns=[
                "treatment",
                "users",
                "exposed_users",
                "exposure_rate",
                "visit_rate",
                "conversion_rate",
            ],
        ).to_string(index=False)
    )

    print("\n=== 3b. TREATMENT x EXPOSURE ===")
    print(
        pandas.DataFrame(
            report["treatment_exposure"],
            columns=["treatment", "exposure", "users", "visit_rate", "conversion_rate"],
        ).to_string(index=False)
    )
    print(f"\ntreatment = 0 AND exposure = 1: {report['control_exposed']:,} rows")
    print("\nChecks completed.")


def run(
    data_path: str | Path = DEFAULT_DATA_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
) -> dict[str, Any]:
    """Run the checks, print the summary and save the JSON report."""
    report = run_checks(data_path)
    print_checks(report)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(f"Saved: {output_path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH, help="Criteo CSV path")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON report path"
    )
    args = parser.parse_args(argv)
    try:
        report = run(args.data, args.output)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
