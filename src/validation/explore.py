"""Profile the Criteo uplift CSV: size, schema, missing values, arm split, raw lift.

Read-only exploration. Nothing runs at import time, so the module can be imported
(and tested) without the 3 GiB dataset; the CSV is only touched when a function is
called.

Run from the project root (either form works):

    python src/validation/explore.py
    python -m src.validation.explore

The human-readable summary is printed and the same aggregates are saved to
``outputs/explore_results.json`` (``--output`` to change). The JSON report holds
aggregates only: the first rows of the file are printed for a quick look, not
persisted.

Exit codes: 0 on success, 2 when the dataset is missing or an option is invalid.
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
    DEFAULT_DATA_PATH,
    VIEW_NAME,
    as_number,
    connect,
    quote,
)

DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "explore_results.json"
PREVIEW_ROWS = 5
DESCRIBE_COLUMNS = ("column_name", "column_type", "null", "key", "default", "extra")


def describe_columns(con) -> list[tuple]:
    """Rows of ``DESCRIBE criteo`` (name, type, null, key, default, extra)."""
    return con.execute(f"DESCRIBE {quote(VIEW_NAME)}").fetchall()


def profile_dataset(data_path: str | Path = DEFAULT_DATA_PATH) -> dict[str, Any]:
    """Aggregates for one CSV. Opens and closes its own connection.

    Raises:
        FileNotFoundError: ``data_path`` does not exist.
    """
    path = Path(data_path)
    con = connect(path)
    try:
        total_rows = int(con.execute(f"SELECT COUNT(*) FROM {quote(VIEW_NAME)}").fetchone()[0])
        described = describe_columns(con)
        columns = [row[0] for row in described]

        # One scan for every column: the previous version issued one COUNT per column.
        null_counts = ", ".join(
            f"COUNT(*) - COUNT({quote(name)}) AS {quote('missing_' + name)}"
            for name in columns
        )
        missing_row = con.execute(f"SELECT {null_counts} FROM {quote(VIEW_NAME)}").fetchone()
        missing = {name: int(value) for name, value in zip(columns, missing_row)}

        by_arm = [
            {
                "treatment": as_number(treatment),
                "users": int(users),
                "conversion_rate": None if rate is None else float(rate),
            }
            for treatment, users, rate in con.execute(
                f"""
                SELECT
                    treatment,
                    COUNT(*) AS users,
                    AVG(CAST(conversion AS DOUBLE)) AS conversion_rate
                FROM {quote(VIEW_NAME)}
                GROUP BY treatment
                ORDER BY treatment
                """
            ).fetchall()
        ]

        raw_lift = con.execute(
            f"""
            SELECT
                AVG(CASE WHEN treatment = 1
                    THEN CAST(conversion AS DOUBLE) END)
                -
                AVG(CASE WHEN treatment = 0
                    THEN CAST(conversion AS DOUBLE) END)
                AS absolute_lift
            FROM {quote(VIEW_NAME)}
            """
        ).fetchone()[0]

        cursor = con.execute(f"SELECT * FROM {quote(VIEW_NAME)} LIMIT {PREVIEW_ROWS}")
        preview_columns = [description[0] for description in cursor.description]
        preview_rows = [list(row) for row in cursor.fetchall()]
    finally:
        con.close()

    return {
        "dataset": {"file": path.name, "rows": total_rows, "columns": len(columns)},
        "schema": [
            {
                "column": name,
                "type": type_name,
                "nullable": null_flag == "YES",
                "key": key,
                "default": default,
                "extra": extra,
            }
            for name, type_name, null_flag, key, default, extra in described
        ],
        "missing_values": missing,
        "treatment_distribution": by_arm,
        "raw_conversion_rate_difference": None if raw_lift is None else float(raw_lift),
        "preview": {"columns": preview_columns, "rows": preview_rows},
    }


def print_profile(profile: dict[str, Any]) -> None:
    """Print the same sections the script always printed."""
    dataset = profile["dataset"]
    print("\n=== DATASET SIZE ===")
    print(pandas.DataFrame({"total_rows": [dataset["rows"]]}))
    print("\n=== COLUMN SCHEMA ===")
    print(
        pandas.DataFrame(
            [
                {
                    "column_name": entry["column"],
                    "column_type": entry["type"],
                    "null": "YES" if entry["nullable"] else "NO",
                    "key": entry["key"],
                    "default": entry["default"],
                    "extra": entry["extra"],
                }
                for entry in profile["schema"]
            ],
            columns=list(DESCRIBE_COLUMNS),
        ).to_string(index=False)
    )
    print("\n=== FIRST 5 ROWS ===")
    print(
        pandas.DataFrame(
            profile["preview"]["rows"], columns=profile["preview"]["columns"]
        ).to_string(index=False)
    )
    print("\n=== MISSING VALUES ===")
    for column, missing in profile["missing_values"].items():
        print(f"{column}: {missing:,} missing")
    print("\n=== TREATMENT DISTRIBUTION ===")
    print(
        pandas.DataFrame(
            profile["treatment_distribution"],
            columns=["treatment", "users", "conversion_rate"],
        ).to_string(index=False)
    )
    print("\n=== RAW CONVERSION RATE DIFFERENCE ===")
    print(
        pandas.DataFrame(
            {"absolute_lift": [profile["raw_conversion_rate_difference"]]}
        ).to_string(index=False)
    )


def report_payload(profile: dict[str, Any]) -> dict[str, Any]:
    """The profile without the data rows: aggregates only."""
    payload = dict(profile)
    payload.pop("preview", None)
    return payload


def run(
    data_path: str | Path = DEFAULT_DATA_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
) -> dict[str, Any]:
    """Profile, print the summary and save the JSON report. Returns the payload."""
    profile = profile_dataset(data_path)
    print_profile(profile)
    payload = report_payload(profile)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print("\nExploration completed successfully.")
    print(f"Saved: {output_path}")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH, help="Criteo CSV path")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON report path"
    )
    args = parser.parse_args(argv)
    try:
        run(args.data, args.output)
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
