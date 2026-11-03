"""Convert the Criteo Uplift v2.1 CSV into a validated Parquet file with DuckDB.

The CSV is streamed by DuckDB straight into Parquet, so the dataset is never
loaded into Python or pandas memory. The conversion keeps every row and every
column in the original order: nothing is filtered, deduplicated, rounded,
recoded or imputed.

Run from the project root (either form works):

    python src/ingestion/csv_to_parquet.py
    python -m src.ingestion.csv_to_parquet

Outputs (all git-ignored):

* ``data/criteo-uplift-v2.1.parquet`` (``--parquet`` to change)
* ``outputs/parquet_conversion_report.json`` (``--report`` to change)

The Parquet file is written to a temporary name, validated, and only then moved
to its final name. If any check fails, no Parquet file is left at the final
path, the report is still written with ``"status": "failed"``, and the exit
code is 1. Usage errors (missing CSV, existing output without ``--overwrite``,
malformed options) exit with code 2 and no report.

Expected schema
---------------
==================  ==========  =========================================
Column              Type        Meaning
==================  ==========  =========================================
f0 .. f11           DOUBLE      anonymised continuous user features
treatment           INTEGER     0/1 random assignment
conversion          INTEGER     0/1 outcome
visit               INTEGER     0/1 outcome
exposure            INTEGER     0/1 post-assignment indicator
==================  ==========  =========================================

The file must have exactly these 16 column names (order is preserved from the
source and recorded, not enforced). Every column is first parsed as DOUBLE, so a
value that cannot be parsed fails the run. Binary columns are deliberately NOT
parsed as integers: DuckDB would silently round a value such as 0.5 to 0 or 1.
They are checked to be exactly 0 or 1 as DOUBLE and only then stored as INTEGER.
No column may contain a missing value, and no feature may be NaN or infinite.

What the validation does and does not show
------------------------------------------
* It shows the Parquet file holds the same rows and values as the CSV as parsed
  with the schema above. The content check compares an order-independent sum of
  DuckDB's per-row hash of every value. It is a consistency check, not a
  cryptographic proof.
* It does not show the dataset itself is correct or representative.
"""

import argparse
import csv
import hashlib
import json
import math
import platform
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CSV_PATH = PROJECT_ROOT / "data" / "criteo-uplift-v2.1.csv"
DEFAULT_PARQUET_PATH = PROJECT_ROOT / "data" / "criteo-uplift-v2.1.parquet"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "outputs" / "parquet_conversion_report.json"
DEFAULT_MEMORY_LIMIT = "3GB"

FEATURE_COLUMNS = tuple(f"f{i}" for i in range(12))
BINARY_COLUMNS = ("treatment", "conversion", "visit", "exposure")
OUTCOME_COLUMNS = ("conversion", "visit", "exposure")
GROUPS = {"control": 0, "treatment": 1}

EXPECTED_COLUMN_TYPES = {
    **{name: "DOUBLE" for name in FEATURE_COLUMNS},
    **{name: "INTEGER" for name in BINARY_COLUMNS},
}
CANONICAL_ORDER = FEATURE_COLUMNS + BINARY_COLUMNS
# Every column is read as DOUBLE so that nothing is rounded during parsing.
SOURCE_PARSE_TYPE = "DOUBLE"

# Counts verified earlier: row and group sizes from the Day 1 EDA, conversion and
# visit counts from outputs/ab_test_results.json, exposure from RESULTS.md.
VERIFIED_COUNTS = {
    "rows": 13_979_592,
    "control_rows": 2_096_937,
    "treatment_rows": 11_882_655,
    "control_conversion": 4_063,
    "treatment_conversion": 36_711,
    "control_visit": 80_105,
    "treatment_visit": 576_824,
    "control_exposure": 0,
    "treatment_exposure": 428_212,
    "total_conversion": 4_063 + 36_711,
    "total_visit": 80_105 + 576_824,
    "total_exposure": 428_212,
}

COMPRESSION = "ZSTD"

LIMITATIONS = [
    "The check shows the Parquet file matches the CSV as parsed with the stated schema. "
    "It does not show the dataset is correct, representative, or free of upstream problems.",
    "The content check is an order-independent sum of per-row hashes. It is a consistency "
    "check, not a cryptographic guarantee. Row order is preserved by the conversion but "
    "is not part of that check.",
    "Expected counts are the values verified earlier in this project for the v2.1 file. "
    "They do not apply to other dataset versions.",
]


class SchemaError(ValueError):
    """The source file does not match the expected schema or data types."""


# ------------------------------------------------------------------- utilities


def _sql_string(value: str | Path) -> str:
    """Quote a value as a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _validate_memory_limit(value: str) -> str:
    if not re.fullmatch(r"\d+(\.\d+)?\s?(KB|MB|GB|TB|KiB|MiB|GiB|TiB)", value or ""):
        raise ValueError(f"memory limit must look like '3GB' or '512MB', got {value!r}")
    return value


def _finite_or_none(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _check(name: str, passed: bool, expected: Any, actual: Any, detail: str = "") -> dict:
    return {
        "name": name,
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
        "detail": detail,
    }


def file_digest(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Size, SHA-256 and physical line count of a file, in one streaming pass."""
    digest = hashlib.sha256()
    size = 0
    newlines = 0
    last_byte = b""
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(chunk)
            size += len(chunk)
            newlines += chunk.count(b"\n")
            last_byte = chunk[-1:]
    lines = newlines + (1 if size and last_byte != b"\n" else 0)
    return {"size_bytes": size, "sha256": digest.hexdigest(), "line_count": lines}


def read_header(csv_path: Path) -> list[str]:
    """Return the column names from the first line of the CSV."""
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        try:
            return [name.strip() for name in next(csv.reader(handle))]
        except StopIteration:
            raise SchemaError(f"{csv_path.name} is empty") from None


def check_header(header: list[str]) -> dict:
    """Header must contain exactly the 16 expected names, each once."""
    expected = sorted(CANONICAL_ORDER)
    actual = sorted(header)
    return _check(
        "source_columns_match_expected",
        actual == expected,
        expected,
        actual,
        "" if actual == expected else (
            f"missing: {sorted(set(expected) - set(header))}; "
            f"unexpected: {sorted(set(header) - set(expected))}; "
            f"duplicated: {sorted({n for n in header if header.count(n) > 1})}"
        ),
    )


def typed_csv_relation(csv_path: Path, header: list[str]) -> str:
    """SQL relation that reads every column as DOUBLE, without rounding anything."""
    columns = ", ".join(f"{_quote(n)}: {_sql_string(SOURCE_PARSE_TYPE)}" for n in header)
    return f"read_csv({_sql_string(csv_path)}, header=true, columns={{{columns}}})"


def output_select(header: list[str]) -> str:
    """Select list that stores the (already validated) binary columns as INTEGER."""
    return ", ".join(
        f"CAST({_quote(n)} AS INTEGER) AS {_quote(n)}" if n in BINARY_COLUMNS else _quote(n)
        for n in header
    )


# ---------------------------------------------------------------------- profile


def profile_relation(con: duckdb.DuckDBPyConnection, relation: str) -> dict[str, Any]:
    """One scan: counts, nulls, ranges, binary sums, per-group sums, content fingerprint."""
    parts = ["COUNT(*) AS rows"]
    for c in CANONICAL_ORDER:
        q = _quote(c)
        parts.append(f"COUNT(*) - COUNT({q}) AS null_{c}")
        parts.append(f"MIN({q}) AS min_{c}")
        parts.append(f"MAX({q}) AS max_{c}")
    for c in FEATURE_COLUMNS:
        q = _quote(c)
        parts.append(f"SUM(CASE WHEN {q} IS NOT NULL AND NOT isfinite({q}) THEN 1 ELSE 0 END) AS nonfinite_{c}")
    for c in BINARY_COLUMNS:
        q = _quote(c)
        parts.append(f"SUM(CASE WHEN {q} NOT IN (0, 1) THEN 1 ELSE 0 END) AS nonbinary_{c}")
        parts.append(f"SUM(CAST({q} AS DOUBLE)) AS sum_{c}")
    for group, value in GROUPS.items():
        parts.append(f"COUNT(*) FILTER (WHERE \"treatment\" = {value}) AS {group}_rows")
        for c in OUTCOME_COLUMNS:
            parts.append(
                f"SUM(CAST(\"{c}\" AS DOUBLE)) FILTER (WHERE \"treatment\" = {value}) AS {group}_{c}"
            )
    # Hash the values as they will be stored: binary columns as INTEGER, features as DOUBLE.
    hash_args = ", ".join(
        f"CAST({_quote(c)} AS INTEGER)" if c in BINARY_COLUMNS else _quote(c)
        for c in CANONICAL_ORDER
    )
    parts.append(f"SUM(CAST(hash({hash_args}) AS HUGEINT)) AS fingerprint")

    cursor = con.execute(f"SELECT {', '.join(parts)} FROM {relation}")
    row = dict(zip([d[0] for d in cursor.description], cursor.fetchone()))

    def number(key: str) -> int:
        return int(row[key]) if row[key] is not None else 0

    return {
        "rows": number("rows"),
        "nulls": {c: number(f"null_{c}") for c in CANONICAL_ORDER},
        "min": {c: _finite_or_none(row[f"min_{c}"]) for c in CANONICAL_ORDER},
        "max": {c: _finite_or_none(row[f"max_{c}"]) for c in CANONICAL_ORDER},
        "nonfinite": {c: number(f"nonfinite_{c}") for c in FEATURE_COLUMNS},
        "nonbinary": {c: number(f"nonbinary_{c}") for c in BINARY_COLUMNS},
        "sums": {c: number(f"sum_{c}") for c in BINARY_COLUMNS},
        "by_group": {
            group: {
                "rows": number(f"{group}_rows"),
                **{c: number(f"{group}_{c}") for c in OUTCOME_COLUMNS},
            }
            for group in GROUPS
        },
        "fingerprint": str(row["fingerprint"]),
    }


def observed_counts(profile: dict[str, Any]) -> dict[str, int]:
    """Flatten a profile into the same keys as ``VERIFIED_COUNTS``."""
    counts = {"rows": profile["rows"]}
    for group in GROUPS:
        counts[f"{group}_rows"] = profile["by_group"][group]["rows"]
        for c in OUTCOME_COLUMNS:
            counts[f"{group}_{c}"] = profile["by_group"][group][c]
    for c in OUTCOME_COLUMNS:
        counts[f"total_{c}"] = profile["sums"][c]
    return counts


# ----------------------------------------------------------------------- checks


def source_checks(profile: dict[str, Any], line_count: int) -> list[dict]:
    """Checks on the source CSV as parsed with the explicit schema."""
    nulls = {c: n for c, n in profile["nulls"].items() if n}
    nonfinite = {c: n for c, n in profile["nonfinite"].items() if n}
    nonbinary = {c: n for c, n in profile["nonbinary"].items() if n}
    return [
        _check("source_has_rows", profile["rows"] > 0, "> 0", profile["rows"]),
        _check(
            "source_line_count_matches_row_count",
            line_count - 1 == profile["rows"],
            line_count - 1,
            profile["rows"],
            "physical lines minus the header, counted independently of DuckDB",
        ),
        _check("source_no_missing_values", not nulls, {}, nulls),
        _check("source_features_finite", not nonfinite, {}, nonfinite),
        _check("source_binary_columns_only_0_1", not nonbinary, {}, nonbinary),
    ]


def known_count_checks(profile: dict[str, Any], expected: dict[str, int]) -> list[dict]:
    """Compare observed counts with counts verified earlier in the project."""
    observed = observed_counts(profile)
    return [
        _check(f"known_count_{key}", observed.get(key) == value, value, observed.get(key))
        for key, value in expected.items()
    ]


def compare_profiles(source: dict[str, Any], output: dict[str, Any]) -> list[dict]:
    """Checks that the Parquet content equals the source content."""
    return [
        _check("output_row_count_matches_source", output["rows"] == source["rows"],
               source["rows"], output["rows"]),
        _check("output_no_missing_values", not any(output["nulls"].values()), {},
               {c: n for c, n in output["nulls"].items() if n}),
        _check("output_binary_columns_only_0_1", not any(output["nonbinary"].values()), {},
               {c: n for c, n in output["nonbinary"].items() if n}),
        _check("output_features_finite", not any(output["nonfinite"].values()), {},
               {c: n for c, n in output["nonfinite"].items() if n}),
        _check("output_min_max_match_source",
               output["min"] == source["min"] and output["max"] == source["max"],
               {"min": source["min"], "max": source["max"]},
               {"min": output["min"], "max": output["max"]}),
        _check("output_counts_match_source",
               output["sums"] == source["sums"] and output["by_group"] == source["by_group"],
               {"sums": source["sums"], "by_group": source["by_group"]},
               {"sums": output["sums"], "by_group": output["by_group"]}),
        _check("output_content_fingerprint_matches_source",
               output["fingerprint"] == source["fingerprint"],
               source["fingerprint"], output["fingerprint"],
               "order-independent sum of per-row hashes over all 16 columns"),
    ]


def parquet_schema(con: duckdb.DuckDBPyConnection, path: Path) -> list[tuple[str, str]]:
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet({_sql_string(path)})").fetchall()
    return [(r[0], r[1]) for r in rows]


def schema_checks(source_header: list[str], schema: list[tuple[str, str]]) -> list[dict]:
    names = [n for n, _ in schema]
    types = dict(schema)
    expected_types = {n: EXPECTED_COLUMN_TYPES[n] for n in source_header}
    return [
        _check("output_columns_and_order_match_source", names == source_header,
               source_header, names),
        _check("output_column_types_match_expected", types == expected_types,
               expected_types, types),
    ]


def parquet_file_info(con: duckdb.DuckDBPyConnection, path: Path) -> dict[str, Any]:
    row_groups, codecs = con.execute(
        f"SELECT COUNT(DISTINCT row_group_id), list(DISTINCT compression) "
        f"FROM parquet_metadata({_sql_string(path)})"
    ).fetchone()
    return {"row_groups": int(row_groups), "compression": sorted(codecs)}


# ------------------------------------------------------------------- conversion


def _finish(report: dict, checks: list[dict], started: float, report_path: Path) -> dict:
    report["validation"] = {
        "status": "passed" if checks and all(c["passed"] for c in checks) else "failed",
        "checks": checks,
        "failed_checks": [c["name"] for c in checks if not c["passed"]],
    }
    report["status"] = report["validation"]["status"]
    report["metadata"]["elapsed_seconds"] = round(time.monotonic() - started, 1)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return report


def convert(
    csv_path: Path = DEFAULT_CSV_PATH,
    parquet_path: Path = DEFAULT_PARQUET_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    *,
    expected_counts: dict[str, int] | None = VERIFIED_COUNTS,
    overwrite: bool = False,
    memory_limit: str = DEFAULT_MEMORY_LIMIT,
    threads: int | None = None,
) -> dict[str, Any]:
    """Convert, validate and report. Returns the report; ``report["status"]`` is the verdict.

    Raises:
        FileNotFoundError: the CSV does not exist.
        FileExistsError: the Parquet file exists and ``overwrite`` is false.
        ValueError: ``memory_limit`` is malformed.
    """
    csv_path, parquet_path, report_path = Path(csv_path), Path(parquet_path), Path(report_path)
    memory_limit = _validate_memory_limit(memory_limit)
    if not csv_path.exists():
        raise FileNotFoundError(f"Dataset not found: {csv_path}")
    if parquet_path.exists() and not overwrite:
        raise FileExistsError(f"{parquet_path} already exists; pass overwrite=True / --overwrite")

    started = time.monotonic()
    report: dict[str, Any] = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": "src/ingestion/csv_to_parquet.py",
            "environment": {"python": platform.python_version(), "duckdb": duckdb.__version__},
            "duckdb_settings": {"memory_limit": memory_limit, "threads": threads},
        },
        "methodology": {
            "engine": "DuckDB streams the CSV into Parquet; the data is not loaded into pandas",
            "csv_parsing": (
                "read_csv with every column read as DOUBLE (no rounding); unparseable values "
                "fail the run; binary columns must be exactly 0 or 1 before being stored as INTEGER"
            ),
            "transformations": "none: no filtering, deduplication, rounding, recoding or imputation",
            "expected_column_types": EXPECTED_COLUMN_TYPES,
            "parquet_compression": COMPRESSION,
            "known_counts_checked": expected_counts is not None,
            "known_counts_source": (
                "rows and group sizes from the Day 1 EDA; conversion and visit from "
                "outputs/ab_test_results.json; exposure from RESULTS.md"
            ),
        },
        "source": {"file": csv_path.name},
        "output": {"file": parquet_path.name, "written": False},
        "limitations": LIMITATIONS,
    }
    checks: list[dict] = []

    digest = file_digest(csv_path)
    report["source"].update(digest)

    try:
        header = read_header(csv_path)
    except SchemaError as error:
        checks.append(_check("source_has_header", False, "16 column names", str(error)))
        return _finish(report, checks, started, report_path)
    report["source"]["columns"] = header
    header_check = check_header(header)
    checks.append(header_check)
    if not header_check["passed"]:
        return _finish(report, checks, started, report_path)

    tmp_dir = Path(tempfile.mkdtemp(prefix=".duckdb_tmp_", dir=csv_path.parent))
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    partial = parquet_path.with_name(parquet_path.name + ".partial")
    config: dict[str, Any] = {"memory_limit": memory_limit, "temp_directory": str(tmp_dir)}
    if threads is not None:
        config["threads"] = int(threads)
    con = duckdb.connect(":memory:", config=config)
    try:
        relation = typed_csv_relation(csv_path, header)
        try:
            source_profile = profile_relation(con, relation)
        except duckdb.Error as error:
            checks.append(_check("source_values_parse_with_expected_types", False,
                                 "all values parse", "parse error", str(error).splitlines()[0]))
            return _finish(report, checks, started, report_path)
        checks.append(_check("source_values_parse_with_expected_types", True,
                             "all values parse", "ok"))
        report["source"]["profile"] = source_profile

        checks.extend(source_checks(source_profile, digest["line_count"]))
        if expected_counts is not None:
            checks.extend(known_count_checks(source_profile, expected_counts))
        if not all(c["passed"] for c in checks):
            return _finish(report, checks, started, report_path)  # nothing is written

        partial.unlink(missing_ok=True)
        con.execute(
            f"COPY (SELECT {output_select(header)} FROM {relation}) TO {_sql_string(partial)} "
            f"(FORMAT PARQUET, COMPRESSION {COMPRESSION})"
        )

        output_profile = profile_relation(con, f"read_parquet({_sql_string(partial)})")
        schema = parquet_schema(con, partial)
        report["output"]["schema"] = [{"column": n, "type": t} for n, t in schema]
        report["output"]["profile"] = output_profile
        report["output"].update(parquet_file_info(con, partial))
        checks.extend(schema_checks(header, schema))
        checks.extend(compare_profiles(source_profile, output_profile))
        output_digest = file_digest(partial)
        report["output"]["size_bytes"] = output_digest["size_bytes"]
        report["output"]["sha256"] = output_digest["sha256"]

        if all(c["passed"] for c in checks):
            partial.replace(parquet_path)
            report["output"]["written"] = True
    finally:
        con.close()
        partial.unlink(missing_ok=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return _finish(report, checks, started, report_path)


def print_summary(report: dict[str, Any]) -> None:
    print("\n=== CSV -> PARQUET CONVERSION ===")
    source, output = report["source"], report["output"]
    print(f"Source: {source['file']}  {source['size_bytes']:,} bytes  sha256 {source['sha256']}")
    if "profile" in source:
        print(f"Source rows: {source['profile']['rows']:,}")
    if output.get("written"):
        print(f"Output: {output['file']}  {output['size_bytes']:,} bytes  sha256 {output['sha256']}")
        print(f"Output rows: {output['profile']['rows']:,}; row groups: {output['row_groups']}; "
              f"compression: {', '.join(output['compression'])}")
    else:
        print("Output: NOT written")
    for check in report["validation"]["checks"]:
        print(f"  [{'ok' if check['passed'] else 'FAIL'}] {check['name']}"
              + ("" if check["passed"] or not check["detail"] else f"  ({check['detail']})"))
    print(f"\nStatus: {report['status'].upper()}  ({report['metadata']['elapsed_seconds']} s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_CSV_PATH, help="source CSV path")
    parser.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET_PATH,
                        help="output Parquet path")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH,
                        help="JSON validation report path")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace an existing Parquet file")
    parser.add_argument("--skip-known-counts", action="store_true",
                        help="skip the comparison with the verified v2.1 counts (other data only)")
    parser.add_argument("--memory-limit", default=DEFAULT_MEMORY_LIMIT,
                        help="DuckDB memory limit, for example 3GB")
    parser.add_argument("--threads", type=int, default=None, help="DuckDB thread count")
    args = parser.parse_args(argv)

    try:
        report = convert(args.data, args.parquet, args.report, overwrite=args.overwrite,
                         memory_limit=args.memory_limit, threads=args.threads,
                         expected_counts=None if args.skip_known_counts else VERIFIED_COUNTS)
    except (FileNotFoundError, FileExistsError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print_summary(report)
    print(f"\nSaved report: {args.report}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
