"""Unit tests for src/validation/srm_check.py.

The tests use small synthetic counts and tiny temporary CSV files. They check the
arithmetic, the input validation, and that the assessment never claims a formal
SRM result. They do not read the Criteo file.
"""

import json

import duckdb
import numpy as np
import pytest

from src.validation.srm_check import (
    REFERENCE_CONTROL_PROPORTION,
    REFERENCE_TREATMENT_PROPORTION,
    STATUS_NOT_ASSESSABLE,
    SrmAssessment,
    assess_srm,
    build_report,
    compare_to_reference,
    compute_split,
    fetch_group_counts,
    run,
)


def write_csv(path, treatment_values):
    """Write a minimal CSV; only the treatment column matters to the SRM script."""
    lines = ["conversion,treatment"] + [f"0,{value}" for value in treatment_values]
    path.write_text("\n".join(lines) + "\n")
    return path


def all_keys(node):
    """Yield every key in a nested dict/list structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from all_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from all_keys(item)


# ------------------------------------------------------ proportion calculations


def test_compute_split_known_values():
    split = compute_split(15, 85)

    assert split.control_n == 15
    assert split.treatment_n == 85
    assert split.total_n == 100
    assert split.control_proportion == pytest.approx(0.15)
    assert split.treatment_proportion == pytest.approx(0.85)


def test_compute_split_uneven_counts():
    split = compute_split(1, 3)

    assert split.control_proportion == pytest.approx(0.25)
    assert split.treatment_proportion == pytest.approx(0.75)


def test_proportions_sum_to_one_and_total_is_the_sum():
    # Group counts of the 13,979,592-row file, as reported by the earlier checks.
    split = compute_split(2_096_937, 11_882_655)

    assert split.total_n == 13_979_592
    assert split.control_proportion + split.treatment_proportion == pytest.approx(1.0)
    assert split.control_proportion == pytest.approx(0.15, abs=1e-6)
    assert split.treatment_proportion == pytest.approx(0.85, abs=1e-6)


def test_compute_split_accepts_numpy_integers_and_stores_python_ints():
    split = compute_split(np.int64(20), np.int64(80))

    assert type(split.control_n) is int
    assert split.treatment_proportion == pytest.approx(0.8)


# ----------------------------------------------------------- invalid group counts


@pytest.mark.parametrize(
    "args",
    [(0, 100), (100, 0), (0, 0), (-1, 100), (100, -5)],
)
def test_non_positive_group_counts_raise_value_error(args):
    with pytest.raises(ValueError):
        compute_split(*args)


@pytest.mark.parametrize(
    "args",
    [(1.5, 10), (10.0, 10), (10, 10.0), (True, 10), ("5", 10), (None, 10)],
)
def test_non_integer_group_counts_raise_type_error(args):
    with pytest.raises(TypeError):
        compute_split(*args)


# ------------------------------------------------------ comparison to reference


def test_exact_match_has_zero_differences():
    comparison = compare_to_reference(compute_split(15, 85), 0.15, 0.85)

    assert comparison["control_proportion_difference"] == pytest.approx(0.0, abs=1e-12)
    assert comparison["treatment_proportion_difference"] == pytest.approx(0.0, abs=1e-12)
    assert comparison["expected_control_n_at_reference"] == pytest.approx(15)
    assert comparison["expected_treatment_n_at_reference"] == pytest.approx(85)
    assert comparison["control_n_difference"] == pytest.approx(0.0, abs=1e-9)


def test_known_deviation_is_observed_minus_reference():
    # 20/80 observed against a 15/85 reference: control is 5 points over.
    comparison = compare_to_reference(compute_split(20, 80), 0.15, 0.85)

    assert comparison["control_proportion_difference"] == pytest.approx(0.05)
    assert comparison["treatment_proportion_difference"] == pytest.approx(-0.05)
    assert comparison["control_difference_percentage_points"] == pytest.approx(5.0)
    assert comparison["treatment_difference_percentage_points"] == pytest.approx(-5.0)
    assert comparison["expected_control_n_at_reference"] == pytest.approx(15)
    assert comparison["control_n_difference"] == pytest.approx(5.0)
    assert comparison["treatment_n_difference"] == pytest.approx(-5.0)


def test_default_reference_is_the_published_85_15_split():
    comparison = compare_to_reference(compute_split(15, 85))

    assert REFERENCE_CONTROL_PROPORTION == 0.15
    assert REFERENCE_TREATMENT_PROPORTION == 0.85
    assert comparison["control_proportion_difference"] == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize(
    "reference",
    [
        (0.0, 1.0),
        (1.0, 0.0),
        (-0.1, 1.1),
        (0.15, 0.80),  # does not sum to 1
        (float("nan"), 0.85),
        (0.15, float("nan")),
    ],
)
def test_invalid_reference_proportions_raise_value_error(reference):
    with pytest.raises(ValueError):
        compare_to_reference(compute_split(15, 85), *reference)


@pytest.mark.parametrize("reference", [("0.15", 0.85), (0.15, None), (True, 0.85)])
def test_non_numeric_reference_proportions_raise_type_error(reference):
    with pytest.raises(TypeError):
        compare_to_reference(compute_split(15, 85), *reference)


# --------------------------------------------------------------- assessment logic


def test_default_assessment_is_not_assessable_and_runs_no_test():
    assessment = assess_srm()

    assert isinstance(assessment, SrmAssessment)
    assert assessment.status == STATUS_NOT_ASSESSABLE == "not_assessable"
    assert assessment.formal_test_performed is False
    assert "sub-sampled" in assessment.reason
    assert "assignment probability" in assessment.reason
    assert assessment.required_for_formal_test


def test_explicit_none_matches_the_default():
    assert assess_srm(None) == assess_srm()


@pytest.mark.parametrize("probability", [0, 1, -0.1, 1.5, float("nan")])
def test_out_of_range_verified_probability_raises_value_error(probability):
    with pytest.raises(ValueError):
        assess_srm(probability)


@pytest.mark.parametrize("probability", [True, "0.85", [0.85]])
def test_non_numeric_verified_probability_raises_type_error(probability):
    with pytest.raises(TypeError):
        assess_srm(probability)


def test_valid_verified_probability_is_refused_until_a_test_is_implemented():
    # The formal test is intentionally absent; supplying a probability must not
    # silently produce a result.
    with pytest.raises(NotImplementedError):
        assess_srm(0.85)


def test_assessment_is_immutable():
    assessment = assess_srm()
    with pytest.raises(Exception):
        assessment.status = "assessed"  # type: ignore[misc]


# ------------------------------------------------------------------------ report


def make_report():
    split = compute_split(2_096_937, 11_882_655)
    return build_report(split, compare_to_reference(split), assess_srm())


def test_report_has_the_required_sections_and_status():
    report = make_report()

    assert set(report) >= {
        "observed",
        "reference",
        "descriptive_comparison",
        "assessment",
        "source_limitations",
    }
    assert report["observed"]["control"]["n"] == 2_096_937
    assert report["observed"]["treatment"]["n"] == 11_882_655
    assert report["observed"]["total_n"] == 13_979_592
    assert report["reference"]["control_proportion"] == 0.15
    assert report["reference"]["treatment_proportion"] == 0.85
    assert report["reference"]["verified_assignment_probability"] is None
    assert report["assessment"]["srm_status"] == "not_assessable"
    assert report["assessment"]["formal_test_performed"] is False
    assert report["source_limitations"]
    assert all(isinstance(text, str) and text for text in report["source_limitations"])


def test_report_contains_no_hypothesis_test_fields():
    forbidden = {"p_value", "z_statistic", "chi2", "chi_square", "test_statistic", "statistic"}
    assert not forbidden & set(all_keys(make_report()))


def test_report_is_strict_json():
    encoded = json.dumps(make_report(), allow_nan=False)
    assert json.loads(encoded)["assessment"]["srm_status"] == "not_assessable"


# ------------------------------------------------------- DuckDB counting from CSV


def test_fetch_group_counts_reads_counts_from_the_csv(tmp_path):
    path = write_csv(tmp_path / "data.csv", [0, 0, 0, 1, 1, 1, 1, 1, 1, 1])

    assert fetch_group_counts(path) == (3, 7)


@pytest.mark.parametrize(
    "values",
    [
        [0, 1, 2],  # unexpected third group
        [1, 1, 1],  # no control group
        [0, 0, 0],  # no treatment group
        [0, 1, ""],  # missing treatment value
    ],
)
def test_fetch_group_counts_rejects_unexpected_groups(tmp_path, values):
    path = write_csv(tmp_path / "data.csv", values)

    with pytest.raises(ValueError):
        fetch_group_counts(path)


def test_fetch_group_counts_fails_when_the_treatment_column_is_missing(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("conversion,visit\n0,0\n1,1\n")

    with pytest.raises(duckdb.Error):
        fetch_group_counts(path)


def test_run_writes_the_report_and_sets_not_assessable(tmp_path):
    data = write_csv(tmp_path / "data.csv", [0] * 15 + [1] * 85)
    output = tmp_path / "out" / "srm_results.json"

    run(data, output)

    report = json.loads(output.read_text())
    assert report["observed"]["control"]["n"] == 15
    assert report["observed"]["treatment"]["n"] == 85
    assert report["observed"]["control"]["proportion"] == pytest.approx(0.15)
    assert report["assessment"]["srm_status"] == "not_assessable"
    assert report["metadata"]["dataset"]["total_users"] == 100
    assert len(report["metadata"]["dataset"]["sha256"]) == 64
    assert report["metadata"]["script"] == "src/validation/srm_check.py"


def test_run_raises_for_a_missing_dataset(tmp_path):
    with pytest.raises(FileNotFoundError):
        run(tmp_path / "missing.csv", tmp_path / "out.json")
