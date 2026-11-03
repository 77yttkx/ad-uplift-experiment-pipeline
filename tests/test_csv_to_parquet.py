"""Unit tests for src/ingestion/csv_to_parquet.py.

All inputs are tiny synthetic CSV files built in a temporary directory. No test
reads the Criteo file, and none of the counts below are Criteo results.
"""

import hashlib
import json
from pathlib import Path

import duckdb
import pytest

from src.ingestion.csv_to_parquet import (
    BINARY_COLUMNS,
    CANONICAL_ORDER,
    EXPECTED_COLUMN_TYPES,
    FEATURE_COLUMNS,
    PROJECT_ROOT,
    VERIFIED_COUNTS,
    SchemaError,
    check_header,
    compare_profiles,
    convert,
    file_digest,
    main,
    observed_counts,
    parquet_schema,
    profile_relation,
    read_header,
)

HEADER = list(CANONICAL_ORDER)

# (treatment, conversion, visit, exposure) per row. 10 rows: 7 treated, 3 control.
ROWS = [
    (1, 0, 0, 0), (1, 1, 1, 1), (1, 0, 1, 1), (1, 0, 0, 0), (1, 0, 1, 0),
    (1, 1, 1, 1), (1, 0, 0, 0), (0, 0, 1, 0), (0, 0, 0, 0), (0, 1, 1, 0),
]

SYNTHETIC_COUNTS = {
    "rows": 10,
    "control_rows": 3,
    "treatment_rows": 7,
    "control_conversion": 1,
    "treatment_conversion": 2,
    "control_visit": 2,
    "treatment_visit": 4,
    "control_exposure": 0,
    "treatment_exposure": 3,
    "total_conversion": 3,
    "total_visit": 6,
    "total_exposure": 3,
}


def feature_values(i):
    """Distinct, awkward-looking feature values so rounding would be noticed."""
    return [f"{12.616364906986497 + i / 7:.15f}", f"{-0.16867922100056124 * (i + 1):.17g}"] + [
        f"{(i + 1) * 1e-9 + j:.17g}" for j in range(10)
    ]


def make_lines(rows=ROWS, header=HEADER):
    lines = [",".join(header)]
    for i, row in enumerate(rows):
        values = dict(zip(FEATURE_COLUMNS, feature_values(i)))
        values.update(zip(BINARY_COLUMNS, map(str, row)))
        lines.append(",".join(values[name] for name in header))
    return lines


def write_csv(tmp_path, lines=None, name="source.csv", trailing_newline=True):
    path = tmp_path / name
    text = "\n".join(make_lines() if lines is None else lines)
    path.write_text(text + ("\n" if trailing_newline else ""))
    return path


def run_convert(tmp_path, csv_path, **kwargs):
    kwargs.setdefault("expected_counts", SYNTHETIC_COUNTS)
    return convert(
        csv_path,
        tmp_path / "out" / "result.parquet",
        tmp_path / "out" / "report.json",
        **kwargs,
    )


def failed_names(report):
    return set(report["validation"]["failed_checks"])


def parquet_exists(tmp_path):
    return (tmp_path / "out" / "result.parquet").exists()


def leftovers(tmp_path):
    """Partial files and DuckDB temp folders that should never survive a run."""
    return [p.name for p in tmp_path.rglob("*") if p.name.endswith(".partial") or ".duckdb_tmp" in p.name]


# ------------------------------------------------------------ successful conversion


def test_successful_conversion_reports_passed_and_writes_parquet(tmp_path):
    csv_path = write_csv(tmp_path)

    report = run_convert(tmp_path, csv_path)

    assert report["status"] == "passed"
    assert report["validation"]["failed_checks"] == []
    assert report["output"]["written"] is True
    assert parquet_exists(tmp_path)
    assert report["source"]["profile"]["rows"] == report["output"]["profile"]["rows"] == 10
    assert leftovers(tmp_path) == []


def test_parquet_has_all_rows_columns_and_expected_types(tmp_path):
    csv_path = write_csv(tmp_path)
    run_convert(tmp_path, csv_path)

    con = duckdb.connect()
    schema = parquet_schema(con, tmp_path / "out" / "result.parquet")
    count = con.execute(
        "SELECT COUNT(*) FROM read_parquet(?)", [str(tmp_path / "out" / "result.parquet")]
    ).fetchone()[0]

    assert count == 10
    assert [name for name, _ in schema] == HEADER
    assert dict(schema) == EXPECTED_COLUMN_TYPES
    assert all(dict(schema)[c] == "DOUBLE" for c in FEATURE_COLUMNS)
    assert all(dict(schema)[c] == "INTEGER" for c in BINARY_COLUMNS)


def test_values_and_row_order_are_preserved_exactly(tmp_path):
    csv_path = write_csv(tmp_path)
    run_convert(tmp_path, csv_path)

    con = duckdb.connect()
    rows = con.execute(
        "SELECT * FROM read_parquet(?)", [str(tmp_path / "out" / "result.parquet")]
    ).fetchall()

    for i, row in enumerate(rows):
        expected_features = [float(v) for v in feature_values(i)]
        assert list(row[:12]) == expected_features  # exact float equality, no rounding
        assert tuple(row[12:]) == ROWS[i]


def test_source_column_order_is_preserved_when_it_differs(tmp_path):
    shuffled = ["exposure", "f3", "treatment", "f0", "f1", "f2", "visit", "f4", "f5",
                "f6", "f7", "f8", "conversion", "f9", "f10", "f11"]
    csv_path = write_csv(tmp_path, make_lines(header=shuffled))

    report = run_convert(tmp_path, csv_path)

    assert report["status"] == "passed"
    assert [c["column"] for c in report["output"]["schema"]] == shuffled


def test_report_records_sizes_checksums_and_validation(tmp_path):
    csv_path = write_csv(tmp_path)
    report = run_convert(tmp_path, csv_path)
    parquet = tmp_path / "out" / "result.parquet"

    assert report["source"]["sha256"] == hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert report["source"]["size_bytes"] == csv_path.stat().st_size
    assert report["output"]["sha256"] == hashlib.sha256(parquet.read_bytes()).hexdigest()
    assert report["output"]["size_bytes"] == parquet.stat().st_size
    assert report["output"]["compression"] == ["ZSTD"]
    assert report["output"]["row_groups"] >= 1
    assert report["source"]["line_count"] == 11
    assert report["metadata"]["script"] == "src/ingestion/csv_to_parquet.py"

    on_disk = json.loads((tmp_path / "out" / "report.json").read_text())
    assert on_disk["status"] == "passed"
    assert {c["name"] for c in on_disk["validation"]["checks"]} >= {
        "source_columns_match_expected",
        "source_no_missing_values",
        "source_binary_columns_only_0_1",
        "output_row_count_matches_source",
        "output_column_types_match_expected",
        "output_content_fingerprint_matches_source",
        "known_count_treatment_rows",
    }


def test_report_is_strict_json_and_has_no_nan(tmp_path):
    report = run_convert(tmp_path, write_csv(tmp_path))
    json.dumps(report, allow_nan=False)


def test_observed_counts_match_the_synthetic_ground_truth(tmp_path):
    report = run_convert(tmp_path, write_csv(tmp_path))
    assert observed_counts(report["source"]["profile"]) == SYNTHETIC_COUNTS


def test_conversion_works_without_trailing_newline(tmp_path):
    report = run_convert(tmp_path, write_csv(tmp_path, trailing_newline=False))
    assert report["status"] == "passed"
    assert report["source"]["line_count"] == 11


def test_custom_memory_limit_and_threads_are_accepted(tmp_path):
    report = run_convert(tmp_path, write_csv(tmp_path), memory_limit="512MB", threads=1)
    assert report["status"] == "passed"
    assert report["metadata"]["duckdb_settings"] == {"memory_limit": "512MB", "threads": 1}


@pytest.mark.parametrize("bad", ["", "lots", "3 gigabytes", "-1GB", "GB"])
def test_invalid_memory_limit_is_rejected(tmp_path, bad):
    with pytest.raises(ValueError, match="memory limit"):
        run_convert(tmp_path, write_csv(tmp_path), memory_limit=bad)


# ---------------------------------------------------------------- known-count checks


def test_default_expected_counts_are_the_verified_project_values():
    assert VERIFIED_COUNTS["rows"] == 13_979_592
    assert VERIFIED_COUNTS["control_rows"] + VERIFIED_COUNTS["treatment_rows"] == 13_979_592
    assert VERIFIED_COUNTS["control_conversion"] == 4_063
    assert VERIFIED_COUNTS["treatment_conversion"] == 36_711
    assert VERIFIED_COUNTS["treatment_exposure"] == 428_212
    assert VERIFIED_COUNTS["control_exposure"] == 0


def test_wrong_expected_row_count_fails_and_writes_nothing(tmp_path):
    expected = {**SYNTHETIC_COUNTS, "rows": 11}

    report = run_convert(tmp_path, write_csv(tmp_path), expected_counts=expected)

    assert report["status"] == "failed"
    assert failed_names(report) == {"known_count_rows"}
    assert report["output"]["written"] is False
    assert not parquet_exists(tmp_path)
    assert leftovers(tmp_path) == []


@pytest.mark.parametrize(
    "key", ["treatment_rows", "control_conversion", "treatment_visit", "treatment_exposure",
            "total_conversion"],
)
def test_each_known_count_mismatch_is_detected(tmp_path, key):
    expected = {**SYNTHETIC_COUNTS, key: SYNTHETIC_COUNTS[key] + 1}

    report = run_convert(tmp_path, write_csv(tmp_path), expected_counts=expected)

    assert report["status"] == "failed"
    assert failed_names(report) == {f"known_count_{key}"}
    assert not parquet_exists(tmp_path)


def test_known_count_checks_can_be_skipped_for_other_data(tmp_path):
    report = run_convert(tmp_path, write_csv(tmp_path), expected_counts=None)
    assert report["status"] == "passed"
    assert not any(c["name"].startswith("known_count_") for c in report["validation"]["checks"])


# --------------------------------------------------------------------- invalid schema


@pytest.mark.parametrize(
    "header",
    [
        [c for c in HEADER if c != "f5"],  # missing column
        HEADER + ["extra"],  # extra column
        ["f0_renamed" if c == "f0" else c for c in HEADER],  # renamed column
        HEADER[:-1] + ["f0"],  # duplicated name, one missing
    ],
    ids=["missing", "extra", "renamed", "duplicated"],
)
def test_invalid_header_fails_and_writes_nothing(tmp_path, header):
    lines = [",".join(header)] + ["1," * (len(header) - 1) + "1"] * 3
    csv_path = write_csv(tmp_path, lines)

    report = run_convert(tmp_path, csv_path)

    assert report["status"] == "failed"
    assert failed_names(report) == {"source_columns_match_expected"}
    assert report["output"]["written"] is False
    assert not parquet_exists(tmp_path)
    assert (tmp_path / "out" / "report.json").exists()


def test_empty_file_fails_cleanly(tmp_path):
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("")

    report = run_convert(tmp_path, csv_path)

    assert report["status"] == "failed"
    assert failed_names(report) == {"source_has_header"}


def test_header_only_file_fails_because_it_has_no_rows(tmp_path):
    report = run_convert(tmp_path, write_csv(tmp_path, [",".join(HEADER)]), expected_counts=None)

    assert report["status"] == "failed"
    assert "source_has_rows" in failed_names(report)
    assert not parquet_exists(tmp_path)


def test_unparseable_value_is_an_error_not_a_silent_conversion(tmp_path):
    lines = make_lines()
    cells = lines[3].split(",")
    cells[4] = "not_a_number"
    lines[3] = ",".join(cells)

    report = run_convert(tmp_path, write_csv(tmp_path, lines))

    assert report["status"] == "failed"
    assert failed_names(report) == {"source_values_parse_with_expected_types"}
    assert not parquet_exists(tmp_path)


def test_fractional_value_in_a_binary_column_is_not_rounded(tmp_path):
    lines = make_lines()
    cells = lines[2].split(",")
    cells[HEADER.index("visit")] = "0.5"
    lines[2] = ",".join(cells)

    report = run_convert(tmp_path, write_csv(tmp_path, lines))

    assert report["status"] == "failed"
    assert not parquet_exists(tmp_path)


def test_read_header_and_check_header_directly(tmp_path):
    assert read_header(write_csv(tmp_path)) == HEADER
    assert check_header(HEADER)["passed"] is True
    bad = check_header(HEADER[:-1])
    assert bad["passed"] is False
    assert "exposure" in bad["detail"]
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    with pytest.raises(SchemaError):
        read_header(empty)


# ------------------------------------------------------------ invalid binary values


@pytest.mark.parametrize("column", BINARY_COLUMNS)
@pytest.mark.parametrize("bad_value", ["2", "-1"])
def test_non_binary_value_fails_and_writes_nothing(tmp_path, column, bad_value):
    lines = make_lines()
    cells = lines[5].split(",")
    cells[HEADER.index(column)] = bad_value
    lines[5] = ",".join(cells)

    report = run_convert(tmp_path, write_csv(tmp_path, lines), expected_counts=None)

    assert report["status"] == "failed"
    assert failed_names(report) == {"source_binary_columns_only_0_1"}
    assert report["validation"]["checks"][-1]["actual"] == {column: 1}
    assert not parquet_exists(tmp_path)


# ------------------------------------------------------------------- missing values


@pytest.mark.parametrize("column", ["f0", "f7", "treatment", "visit"])
def test_missing_value_fails_and_writes_nothing(tmp_path, column):
    lines = make_lines()
    cells = lines[4].split(",")
    cells[HEADER.index(column)] = ""
    lines[4] = ",".join(cells)

    report = run_convert(tmp_path, write_csv(tmp_path, lines), expected_counts=None)

    assert report["status"] == "failed"
    assert "source_no_missing_values" in failed_names(report)
    assert not parquet_exists(tmp_path)


@pytest.mark.parametrize("token", ["nan", "inf", "-inf"])
def test_non_finite_feature_fails_and_report_stays_json_safe(tmp_path, token):
    lines = make_lines()
    cells = lines[2].split(",")
    cells[1] = token
    lines[2] = ",".join(cells)

    report = run_convert(tmp_path, write_csv(tmp_path, lines), expected_counts=None)

    assert report["status"] == "failed"
    assert "source_features_finite" in failed_names(report)
    assert not parquet_exists(tmp_path)
    json.dumps(report, allow_nan=False)


# --------------------------------------------------------------- row-count mismatch


def profile_of(parquet_path):
    con = duckdb.connect()
    try:
        return profile_relation(con, f"read_parquet('{parquet_path}')")
    finally:
        con.close()


def tampered_copy(tmp_path, source_parquet, where):
    """Rewrite a valid Parquet file with some rows removed or altered."""
    target = tmp_path / "tampered.parquet"
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{source_parquet}') {where}) "
        f"TO '{target}' (FORMAT PARQUET)"
    )
    con.close()
    return target


def test_dropped_row_is_detected_by_row_count_and_content_checks(tmp_path):
    run_convert(tmp_path, write_csv(tmp_path))
    good = tmp_path / "out" / "result.parquet"
    reference = profile_of(good)

    dropped = profile_of(tampered_copy(tmp_path, good, "WHERE NOT (f1 = (SELECT MAX(f1) FROM read_parquet('%s')))" % good))
    checks = {c["name"]: c["passed"] for c in compare_profiles(reference, dropped)}

    assert checks["output_row_count_matches_source"] is False
    assert checks["output_content_fingerprint_matches_source"] is False
    assert checks["output_counts_match_source"] is False


def test_identical_profiles_pass_every_comparison(tmp_path):
    run_convert(tmp_path, write_csv(tmp_path))
    reference = profile_of(tmp_path / "out" / "result.parquet")

    assert all(c["passed"] for c in compare_profiles(reference, reference))


def test_altered_value_with_same_row_count_is_still_detected(tmp_path):
    run_convert(tmp_path, write_csv(tmp_path))
    good = tmp_path / "out" / "result.parquet"
    reference = profile_of(good)
    altered = tmp_path / "altered.parquet"
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * REPLACE (f2 + 1e-9 AS f2) FROM read_parquet('{good}')) "
        f"TO '{altered}' (FORMAT PARQUET)"
    )
    con.close()

    checks = {c["name"]: c["passed"] for c in compare_profiles(reference, profile_of(altered))}

    assert checks["output_row_count_matches_source"] is True
    assert checks["output_content_fingerprint_matches_source"] is False


def test_recoded_binary_value_is_detected(tmp_path):
    run_convert(tmp_path, write_csv(tmp_path))
    good = tmp_path / "out" / "result.parquet"
    reference = profile_of(good)
    altered = tmp_path / "recoded.parquet"
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * REPLACE (1 - visit AS visit) FROM read_parquet('{good}')) "
        f"TO '{altered}' (FORMAT PARQUET)"
    )
    con.close()

    checks = {c["name"]: c["passed"] for c in compare_profiles(reference, profile_of(altered))}

    assert checks["output_counts_match_source"] is False
    assert checks["output_content_fingerprint_matches_source"] is False


def test_line_count_disagreeing_with_row_count_is_flagged(tmp_path):
    # A stray blank line makes the physical line count exceed the parsed row count.
    lines = make_lines()
    lines.insert(4, "")
    report = run_convert(tmp_path, write_csv(tmp_path, lines), expected_counts=None)

    assert "source_line_count_matches_row_count" in failed_names(report)
    assert not parquet_exists(tmp_path)


def test_file_digest_counts_lines_with_and_without_trailing_newline(tmp_path):
    with_newline = tmp_path / "a.txt"
    with_newline.write_bytes(b"x\ny\nz\n")
    without_newline = tmp_path / "b.txt"
    without_newline.write_bytes(b"x\ny\nz")
    empty = tmp_path / "c.txt"
    empty.write_bytes(b"")

    assert file_digest(with_newline)["line_count"] == 3
    assert file_digest(without_newline)["line_count"] == 3
    assert file_digest(empty)["line_count"] == 0
    assert file_digest(with_newline)["sha256"] == hashlib.sha256(b"x\ny\nz\n").hexdigest()


def test_output_validation_failure_inside_convert_leaves_no_parquet(tmp_path, monkeypatch):
    # Force the Parquet-versus-source comparison to report a mismatch.
    from src.ingestion import csv_to_parquet as module

    real = module.compare_profiles

    def broken(source, output):
        checks = real(source, output)
        checks[0] = module._check("output_row_count_matches_source", False, 10, 9)
        return checks

    monkeypatch.setattr(module, "compare_profiles", broken)

    report = run_convert(tmp_path, write_csv(tmp_path))

    assert report["status"] == "failed"
    assert failed_names(report) == {"output_row_count_matches_source"}
    assert report["output"]["written"] is False
    assert not parquet_exists(tmp_path)
    assert leftovers(tmp_path) == []


def test_wrong_output_types_inside_convert_fail_the_run(tmp_path, monkeypatch):
    from src.ingestion import csv_to_parquet as module

    real = module.parquet_schema
    monkeypatch.setattr(
        module, "parquet_schema",
        lambda con, path: [(n, "BIGINT" if n == "visit" else t) for n, t in real(con, path)],
    )

    report = run_convert(tmp_path, write_csv(tmp_path))

    assert report["status"] == "failed"
    assert failed_names(report) == {"output_column_types_match_expected"}
    assert not parquet_exists(tmp_path)


def test_schema_checks_detect_wrong_names_order_and_types():
    from src.ingestion.csv_to_parquet import schema_checks

    good = [(n, EXPECTED_COLUMN_TYPES[n]) for n in HEADER]
    assert all(c["passed"] for c in schema_checks(HEADER, good))

    reordered = good[1:] + good[:1]
    by_name = {c["name"]: c["passed"] for c in schema_checks(HEADER, reordered)}
    assert by_name["output_columns_and_order_match_source"] is False

    retyped = [(n, "FLOAT" if n == "f3" else t) for n, t in good]
    by_name = {c["name"]: c["passed"] for c in schema_checks(HEADER, retyped)}
    assert by_name["output_column_types_match_expected"] is False


# --------------------------------------------------------- paths, overwrite, the CLI


def test_missing_source_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        run_convert(tmp_path, tmp_path / "missing.csv")


def test_existing_output_is_not_overwritten_by_default(tmp_path):
    csv_path = write_csv(tmp_path)
    run_convert(tmp_path, csv_path)
    parquet = tmp_path / "out" / "result.parquet"
    before = parquet.read_bytes()

    with pytest.raises(FileExistsError):
        run_convert(tmp_path, csv_path)
    assert parquet.read_bytes() == before

    assert run_convert(tmp_path, csv_path, overwrite=True)["status"] == "passed"


def test_failed_rerun_does_not_destroy_an_existing_good_output(tmp_path):
    csv_path = write_csv(tmp_path)
    run_convert(tmp_path, csv_path)
    parquet = tmp_path / "out" / "result.parquet"
    before = parquet.read_bytes()

    bad = write_csv(tmp_path, make_lines()[:-1] + ["1,2"], name="bad.csv")
    report = run_convert(tmp_path, bad, overwrite=True)

    assert report["status"] == "failed"
    assert parquet.read_bytes() == before
    assert leftovers(tmp_path) == []


def test_main_returns_zero_on_success_and_one_on_failure(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    base = ["--data", str(csv_path), "--report", str(tmp_path / "r.json")]

    # Default expected counts are the real Criteo values, so the tiny file must fail them.
    assert main(base + ["--parquet", str(tmp_path / "x.parquet")]) == 1
    assert "FAILED" in capsys.readouterr().out
    assert not (tmp_path / "x.parquet").exists()
    assert json.loads((tmp_path / "r.json").read_text())["status"] == "failed"


def test_main_usage_errors_exit_with_code_two(tmp_path, capsys):
    csv_path = write_csv(tmp_path)
    args = ["--data", str(csv_path), "--parquet", str(tmp_path / "x.parquet"),
            "--report", str(tmp_path / "r.json"), "--skip-known-counts"]

    assert main(["--data", str(tmp_path / "missing.csv")]) == 2
    assert "not found" in capsys.readouterr().err
    assert main(args + ["--memory-limit", "lots"]) == 2
    assert main(args) == 0
    assert main(args) == 2  # output exists, no --overwrite
    assert "already exists" in capsys.readouterr().err
    assert main(args + ["--overwrite"]) == 0


def test_skip_known_counts_flag_converts_other_data_and_records_the_skip(tmp_path):
    csv_path = write_csv(tmp_path)
    args = ["--data", str(csv_path), "--parquet", str(tmp_path / "x.parquet"),
            "--report", str(tmp_path / "r.json"), "--skip-known-counts"]

    assert main(args) == 0
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["status"] == "passed"
    assert report["methodology"]["known_counts_checked"] is False
    assert (tmp_path / "x.parquet").exists()


def test_report_records_that_known_counts_were_checked(tmp_path):
    report = run_convert(tmp_path, write_csv(tmp_path))
    assert report["methodology"]["known_counts_checked"] is True


def test_default_paths_follow_project_conventions():
    from src.ingestion import csv_to_parquet as module

    assert module.DEFAULT_CSV_PATH == PROJECT_ROOT / "data" / "criteo-uplift-v2.1.csv"
    assert module.DEFAULT_PARQUET_PATH.parent == PROJECT_ROOT / "data"
    assert module.DEFAULT_REPORT_PATH == PROJECT_ROOT / "outputs" / "parquet_conversion_report.json"


def test_data_files_and_reports_are_git_ignored():
    patterns = (PROJECT_ROOT / ".gitignore").read_text().split()

    assert "data/" in patterns  # covers the CSV and the default Parquet location
    assert "*.parquet" in patterns
    assert "*.csv" in patterns
    assert "outputs/" in patterns  # covers the JSON report


