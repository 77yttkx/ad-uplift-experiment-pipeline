"""Tests for the DuckDB aggregation behind the A/B test.

The point of these tests is validation, not arithmetic: DuckDB's ``CAST`` rounds a
fractional value instead of refusing it, so a column that is only checked with
MIN/MAX lets a 0.5 through and counts it as a success. That is the same failure
``csv_to_parquet`` documents for its own binary columns.
"""

import pytest

from src.experimentation.ab_test import fetch_group_counts, outcome_report
from tests.conftest import SAMPLE_ROWS, criteo_row


def test_valid_data_is_counted_per_arm(criteo_csv):
    counts = fetch_group_counts(criteo_csv)

    assert counts == {
        0: {"n": 4, "conversion": 1, "visit": 2},
        1: {"n": 8, "conversion": 2, "visit": 4},
    }


def test_fractional_conversion_is_rejected(make_criteo_csv):
    rows = [criteo_row(0, conversion=0.5), criteo_row(1), criteo_row(1)]

    with pytest.raises(ValueError, match="conversion must be exactly 0 or 1"):
        fetch_group_counts(make_criteo_csv(rows))


def test_fractional_visit_is_rejected(make_criteo_csv):
    rows = [criteo_row(0, visit=0.5), criteo_row(1)]

    with pytest.raises(ValueError, match="visit must be exactly 0 or 1"):
        fetch_group_counts(make_criteo_csv(rows))


def test_value_above_one_is_rejected(make_criteo_csv):
    rows = [criteo_row(0, conversion=2), criteo_row(1)]

    with pytest.raises(ValueError, match="conversion must be exactly 0 or 1"):
        fetch_group_counts(make_criteo_csv(rows))


def test_negative_value_is_rejected(make_criteo_csv):
    rows = [criteo_row(0), criteo_row(1, visit=-1)]

    with pytest.raises(ValueError, match="visit must be exactly 0 or 1"):
        fetch_group_counts(make_criteo_csv(rows))


def test_the_error_names_the_arm_and_the_row_count(make_criteo_csv):
    rows = [criteo_row(0, conversion=0.5), criteo_row(0, conversion=0.5), criteo_row(1)]

    with pytest.raises(ValueError) as error:
        fetch_group_counts(make_criteo_csv(rows))

    message = str(error.value)
    assert "treatment group 0" in message
    assert "2 row(s)" in message


def test_missing_values_are_rejected(make_criteo_csv):
    rows = list(SAMPLE_ROWS)
    broken = list(rows[0])
    broken[13] = ""  # conversion empty
    rows[0] = broken

    with pytest.raises(ValueError, match="Missing conversion/visit values"):
        fetch_group_counts(make_criteo_csv(rows))


def test_a_single_arm_is_rejected(make_criteo_csv):
    rows = [criteo_row(1), criteo_row(1, conversion=1)]

    with pytest.raises(ValueError, match="two treatment groups"):
        fetch_group_counts(make_criteo_csv(rows))


def test_a_third_arm_is_rejected(make_criteo_csv):
    rows = [criteo_row(0), criteo_row(1), criteo_row(2)]

    with pytest.raises(ValueError, match="two treatment groups"):
        fetch_group_counts(make_criteo_csv(rows))


def test_outcome_block_uses_the_validated_counts(criteo_csv):
    counts = fetch_group_counts(criteo_csv)

    block, result = outcome_report("conversion", "primary", counts)

    assert block["control"]["successes"] == 1
    assert block["control"]["n"] == 4
    assert block["treatment"]["successes"] == 2
    assert block["treatment"]["n"] == 8
    assert result.z_statistic == pytest.approx(0.0)
    assert block["test"]["alternative"] == "two-sided"
