"""Tests for the exploratory validation scripts and their shared DuckDB helper.

The dataset is 3 GiB and is not in the repository, so every test here writes a
small CSV with the same 16 columns. Nothing asserts an empirical finding about
Criteo: the checks are about counting, missing-value detection, binary validation
and the report/exit-code contract.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.validation import checks, explore
from src.validation.dataset_source import (
    BINARY_COLUMNS,
    CRITEO_COLUMNS,
    VIEW_NAME,
    connect,
    quote,
    require_dataset,
    sql_string,
)
from tests.conftest import SAMPLE_ROWS, criteo_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ dataset_source


def test_sql_string_doubles_single_quotes(tmp_path):
    assert sql_string("/tmp/it's here.csv") == "'/tmp/it''s here.csv'"


def test_sql_string_accepts_a_path(tmp_path):
    assert sql_string(tmp_path) == f"'{tmp_path}'"


def test_quote_escapes_double_quotes():
    assert quote('od"d') == '"od""d"'


def test_require_dataset_names_the_missing_path(tmp_path):
    missing = tmp_path / "absent.csv"

    with pytest.raises(FileNotFoundError, match="Dataset not found"):
        require_dataset(missing)


def test_connect_exposes_the_criteo_view(criteo_csv):
    con = connect(criteo_csv)
    try:
        columns = [row[0] for row in con.execute(f"DESCRIBE {quote(VIEW_NAME)}").fetchall()]
        rows = con.execute(f"SELECT COUNT(*) FROM {quote(VIEW_NAME)}").fetchone()[0]
    finally:
        con.close()

    assert columns == list(CRITEO_COLUMNS)
    assert rows == len(SAMPLE_ROWS)


# ------------------------------------------------------------------------ explore


def test_importing_the_scripts_touches_no_file(tmp_path):
    """Import must not need (or read) the dataset: the old modules ran at import."""
    environment = dict(os.environ)
    # Append, never replace: this environment inherits a PYTHONPATH that supplies
    # packages the virtualenv shares (replacing it makes `import pandas` fail).
    inherited = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{PROJECT_ROOT}{os.pathsep}{inherited}" if inherited else str(PROJECT_ROOT)
    )

    result = subprocess.run(
        [sys.executable, "-c", "import src.validation.explore, src.validation.checks"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_profile_dataset_counts_rows_and_columns(criteo_csv):
    profile = explore.profile_dataset(criteo_csv)

    assert profile["dataset"] == {
        "file": "criteo.csv",
        "rows": len(SAMPLE_ROWS),
        "columns": 16,
    }
    assert [entry["column"] for entry in profile["schema"]] == list(CRITEO_COLUMNS)


def test_profile_dataset_reports_no_missing_values(criteo_csv):
    profile = explore.profile_dataset(criteo_csv)

    assert set(profile["missing_values"]) == set(CRITEO_COLUMNS)
    assert not any(profile["missing_values"].values())


def test_profile_dataset_counts_missing_values(make_criteo_csv):
    rows = list(SAMPLE_ROWS)
    broken = list(rows[0])
    broken[3] = ""  # f3 empty in one row, everything else intact
    rows[0] = broken

    profile = explore.profile_dataset(make_criteo_csv(rows))

    assert profile["missing_values"]["f3"] == 1
    assert profile["missing_values"]["f0"] == 0


def test_profile_dataset_arm_split_and_raw_lift(criteo_csv):
    profile = explore.profile_dataset(criteo_csv)

    by_arm = {entry["treatment"]: entry for entry in profile["treatment_distribution"]}
    assert by_arm[0]["users"] == 4
    assert by_arm[1]["users"] == 8
    assert by_arm[0]["conversion_rate"] == pytest.approx(1 / 4)
    assert by_arm[1]["conversion_rate"] == pytest.approx(2 / 8)
    # treatment minus control: 0.25 - 0.25
    assert profile["raw_conversion_rate_difference"] == pytest.approx(0.0)


def test_profile_keeps_a_preview_out_of_the_saved_report(criteo_csv, tmp_path):
    output = tmp_path / "reports" / "explore.json"

    payload = explore.run(criteo_csv, output)

    on_disk = json.loads(output.read_text())
    assert "preview" not in on_disk
    assert "preview" not in payload
    assert on_disk["dataset"]["rows"] == len(SAMPLE_ROWS)
    assert output.read_text().endswith("\n")


def test_explore_prints_the_sections_and_returns_zero(criteo_csv, tmp_path, capsys):
    exit_code = explore.main(["--data", str(criteo_csv), "--output", str(tmp_path / "e.json")])

    printed = capsys.readouterr().out
    assert exit_code == 0
    for section in (
        "=== DATASET SIZE ===",
        "=== COLUMN SCHEMA ===",
        "=== FIRST 5 ROWS ===",
        "=== MISSING VALUES ===",
        "=== TREATMENT DISTRIBUTION ===",
        "=== RAW CONVERSION RATE DIFFERENCE ===",
    ):
        assert section in printed
    assert "Exploration completed successfully." in printed


def test_explore_missing_dataset_exits_two(tmp_path, capsys):
    exit_code = explore.main(
        ["--data", str(tmp_path / "nope.csv"), "--output", str(tmp_path / "e.json")]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Dataset not found" in captured.err
    assert not (tmp_path / "e.json").exists()


# ------------------------------------------------------------------------- checks


def test_checks_pass_on_binary_data(criteo_csv):
    report = checks.run_checks(criteo_csv)

    assert report["binary_validation"]["all_binary"] is True
    assert report["status"] == "passed"
    for column in BINARY_COLUMNS:
        assert report["binary_validation"]["columns"][column]["non_binary_rows"] == 0


def test_checks_count_the_visit_conversion_table(criteo_csv):
    report = checks.run_checks(criteo_csv)

    counts = {
        (entry["visit"], entry["conversion"]): entry["rows"]
        for entry in report["visit_conversion"]
    }
    assert counts == {(0, 0): 6, (1, 0): 3, (1, 1): 3}
    assert report["conversion_without_visit"] == 0


def test_checks_flag_a_conversion_without_a_visit(make_criteo_csv):
    rows = list(SAMPLE_ROWS) + [criteo_row(1, conversion=1, visit=0)]

    report = checks.run_checks(make_criteo_csv(rows))

    assert report["conversion_without_visit"] == 1
    assert report["status"] == "passed"  # consistency, not a binary violation


def test_checks_flag_a_fractional_binary_value(make_criteo_csv):
    rows = list(SAMPLE_ROWS)
    rows.append(criteo_row(1, visit=0.5))

    report = checks.run_checks(make_criteo_csv(rows))

    assert report["binary_validation"]["columns"]["visit"]["non_binary_rows"] == 1
    assert report["binary_validation"]["all_binary"] is False
    assert report["status"] == "failed"


def test_fractional_grouping_values_are_not_truncated(make_criteo_csv):
    """A 0.5 visit and a 0 visit are different rows in the table, not one row."""
    rows = list(SAMPLE_ROWS) + [criteo_row(1, visit=0.5)]

    report = checks.run_checks(make_criteo_csv(rows))

    pairs = [(entry["visit"], entry["conversion"]) for entry in report["visit_conversion"]]
    assert pairs.count((0, 0)) == 1
    assert pairs.count((0.5, 0)) == 1


def test_checks_flag_a_value_above_one(make_criteo_csv):
    rows = list(SAMPLE_ROWS) + [criteo_row(1, exposure=2)]

    report = checks.run_checks(make_criteo_csv(rows))

    assert report["binary_validation"]["columns"]["exposure"]["non_binary_rows"] == 1
    assert report["status"] == "failed"


def test_checks_report_exposure_only_in_treatment(criteo_csv):
    report = checks.run_checks(criteo_csv)

    by_arm = {entry["treatment"]: entry for entry in report["exposure_by_treatment"]}
    assert by_arm[0]["exposed_users"] == 0
    assert by_arm[1]["exposed_users"] == 3
    assert report["control_exposed"] == 0
    matrix = {(entry["treatment"], entry["exposure"]): entry for entry in report["treatment_exposure"]}
    assert set(matrix) == {(0, 0), (1, 0), (1, 1)}


def test_checks_exit_codes_follow_the_binary_verdict(criteo_csv, make_criteo_csv, tmp_path, capsys):
    passing = checks.main(
        ["--data", str(criteo_csv), "--output", str(tmp_path / "checks_ok.json")]
    )
    failing = checks.main(
        [
            "--data",
            str(make_criteo_csv(list(SAMPLE_ROWS) + [criteo_row(0, conversion=0.5)])),
            "--output",
            str(tmp_path / "checks_bad.json"),
        ]
    )
    capsys.readouterr()

    assert passing == 0
    assert failing == 1


def test_checks_saves_json_and_prints_the_problem_line(make_criteo_csv, tmp_path, capsys):
    output = tmp_path / "checks.json"
    rows = list(SAMPLE_ROWS) + [criteo_row(1, visit=0.5)]

    report = checks.run(make_criteo_csv(rows), output)

    printed = capsys.readouterr().out
    assert "visit:" in printed and "non-binary rows=1 -> PROBLEM" in printed
    assert "All four columns binary: False" in printed
    on_disk = json.loads(output.read_text())
    assert on_disk["status"] == "failed"
    assert report["binary_validation"]["columns"]["visit"]["non_binary_rows"] == 1


def test_checks_missing_dataset_exits_two(tmp_path, capsys):
    exit_code = checks.main(
        ["--data", str(tmp_path / "nope.csv"), "--output", str(tmp_path / "c.json")]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Dataset not found" in captured.err
