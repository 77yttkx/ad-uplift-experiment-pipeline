"""Unit tests for src/experimentation/proportion_tests.py.

All inputs are small synthetic counts chosen so the expected values can be
checked by hand, plus one regression case taken from a previously reported run.
No test reads the Criteo file or claims an empirical finding.
"""

import json
import math

import numpy as np
import pytest

from src.experimentation.proportion_tests import TwoProportionResult, two_proportion_ztest

# Standard normal 97.5th percentile, the textbook value for a 95% interval.
Z_975 = 1.959963984540054


def two_sided_p(z: float) -> float:
    """Normal two-sided p-value via erfc, independent of scipy."""
    return math.erfc(abs(z) / math.sqrt(2))


# ---------------------------------------------------------------- known results


def test_known_result_hand_computed():
    # control 50/1000 = 5%, treatment 70/1000 = 7%
    # pooled = 0.06; se0 = sqrt(0.06 * 0.94 * (1/1000 + 1/1000)) = 0.010620
    # z = 0.02 / 0.010620 = 1.8831
    # se_ci = sqrt(0.05 * 0.95 / 1000 + 0.07 * 0.93 / 1000) = 0.010611
    # CI = 0.02 -/+ 1.96 * 0.010611
    r = two_proportion_ztest(50, 1000, 70, 1000)

    assert r.control_rate == pytest.approx(0.05)
    assert r.treatment_rate == pytest.approx(0.07)
    assert r.absolute_lift == pytest.approx(0.02)
    assert r.relative_lift == pytest.approx(0.4)
    assert r.z_statistic == pytest.approx(1.8831, abs=5e-5)
    assert r.p_value == pytest.approx(two_sided_p(r.z_statistic), rel=1e-9)
    assert r.p_value == pytest.approx(0.05969, abs=5e-6)
    assert r.ci_lower == pytest.approx(-0.000798, abs=5e-7)
    assert r.ci_upper == pytest.approx(0.040798, abs=5e-7)
    assert r.confidence_level == 0.95
    assert r.notes == ()


def test_ci_is_centered_on_the_absolute_lift():
    r = two_proportion_ztest(50, 1000, 70, 1000)
    assert (r.ci_lower + r.ci_upper) / 2 == pytest.approx(r.absolute_lift)
    se = math.sqrt(0.05 * 0.95 / 1000 + 0.07 * 0.93 / 1000)
    assert r.ci_upper - r.ci_lower == pytest.approx(2 * Z_975 * se, rel=1e-9)


def test_regression_against_previously_reported_run():
    """Guards the refactor against drift.

    The counts and the rounded values below are what the pre-refactor
    ab_test.py reported on the Criteo Uplift v2.1 file (as reported by the
    project owner). This is a regression check, not independent validation, and
    it says nothing about the validity of the effect itself.
    """
    r = two_proportion_ztest(4063, 2_096_937, 36_711, 11_882_655)

    assert r.absolute_lift * 100 == pytest.approx(0.115187, abs=5e-7)  # percentage points
    assert r.relative_lift * 100 == pytest.approx(59.45, abs=5e-3)  # percent
    assert r.z_statistic == pytest.approx(28.5165, abs=5e-5)
    assert r.p_value == pytest.approx(7.30826e-179, rel=1e-5)
    assert r.ci_lower < r.absolute_lift < r.ci_upper


# ------------------------------------------------------------- zero successes


def test_zero_successes_in_control_only():
    r = two_proportion_ztest(0, 1000, 10, 1000)

    assert r.control_rate == 0.0
    assert r.absolute_lift == pytest.approx(0.01)
    assert r.z_statistic == pytest.approx(3.1702, abs=5e-5)  # pooled = 0.005
    assert r.p_value == pytest.approx(two_sided_p(r.z_statistic), rel=1e-9)
    assert r.relative_lift is None
    assert any("relative_lift" in note for note in r.notes)
    assert any("Wald" in note for note in r.notes)


def test_zero_successes_in_treatment_only():
    r = two_proportion_ztest(10, 1000, 0, 1000)

    assert r.treatment_rate == 0.0
    assert r.absolute_lift == pytest.approx(-0.01)
    assert r.relative_lift == pytest.approx(-1.0)  # a 100% drop
    assert r.z_statistic == pytest.approx(-3.1702, abs=5e-5)
    assert r.p_value == pytest.approx(two_sided_p(r.z_statistic), rel=1e-9)


def test_zero_successes_in_both_groups_leaves_the_test_undefined():
    r = two_proportion_ztest(0, 1000, 0, 2000)

    assert r.absolute_lift == 0.0
    assert r.relative_lift is None
    assert r.z_statistic is None
    assert r.p_value is None
    assert (r.ci_lower, r.ci_upper) == (0.0, 0.0)
    assert any("undefined" in note and "p_value" in note for note in r.notes)
    assert any("Wald" in note for note in r.notes)


# ---------------------------------------------------------------- equal rates


def test_equal_rates_with_unequal_sample_sizes():
    r = two_proportion_ztest(100, 1000, 200, 2000)

    assert r.control_rate == pytest.approx(0.10)
    assert r.treatment_rate == pytest.approx(0.10)
    assert r.absolute_lift == pytest.approx(0.0, abs=1e-12)
    assert r.relative_lift == pytest.approx(0.0, abs=1e-12)
    assert r.z_statistic == pytest.approx(0.0, abs=1e-9)
    assert r.p_value == pytest.approx(1.0)
    assert r.ci_lower == pytest.approx(-r.ci_upper)
    assert r.ci_lower < 0 < r.ci_upper


# ------------------------------------------------------ zero baseline conversion


def test_zero_baseline_rate_gives_none_not_nan_or_inf():
    r = two_proportion_ztest(0, 5000, 25, 5000)

    assert r.relative_lift is None
    assert r.absolute_lift == pytest.approx(0.005)
    # The result must still serialize as strict JSON (no NaN, no Infinity).
    encoded = json.dumps(r.to_dict(), allow_nan=False)
    assert json.loads(encoded)["relative_lift"] is None


# ----------------------------------------------------------- invalid counts


@pytest.mark.parametrize(
    "args",
    [
        (-1, 100, 5, 100),  # negative control successes
        (5, 100, -1, 100),  # negative treatment successes
        (101, 100, 5, 100),  # control successes above control n
        (5, 100, 101, 100),  # treatment successes above treatment n
        (0, 0, 5, 100),  # zero control n
        (0, 100, 0, 0),  # zero treatment n
        (5, -100, 5, 100),  # negative control n
        (5, 100, 5, -100),  # negative treatment n
    ],
)
def test_invalid_counts_raise_value_error(args):
    with pytest.raises(ValueError):
        two_proportion_ztest(*args)


@pytest.mark.parametrize(
    "args",
    [
        (5.5, 100, 5, 100),  # fractional count
        (5.0, 100, 5, 100),  # float, even if whole
        (5, 100.0, 5, 100),
        (True, 100, 5, 100),  # bool is not a count
        ("5", 100, 5, 100),
        (None, 100, 5, 100),
    ],
)
def test_non_integer_counts_raise_type_error(args):
    with pytest.raises(TypeError):
        two_proportion_ztest(*args)


@pytest.mark.parametrize("level", [0, 1, -0.5, 1.5, float("nan")])
def test_invalid_confidence_level_raises_value_error(level):
    with pytest.raises(ValueError):
        two_proportion_ztest(5, 100, 10, 100, confidence_level=level)


# ------------------------------------------------------------ other properties


def test_swapping_groups_flips_signs_but_not_the_p_value():
    a = two_proportion_ztest(50, 1000, 70, 1000)
    b = two_proportion_ztest(70, 1000, 50, 1000)

    assert b.absolute_lift == pytest.approx(-a.absolute_lift)
    assert b.z_statistic == pytest.approx(-a.z_statistic)
    assert b.p_value == pytest.approx(a.p_value)
    assert (b.ci_lower, b.ci_upper) == pytest.approx((-a.ci_upper, -a.ci_lower))


def test_higher_confidence_level_gives_a_wider_interval():
    narrow = two_proportion_ztest(50, 1000, 70, 1000, confidence_level=0.90)
    default = two_proportion_ztest(50, 1000, 70, 1000)
    wide = two_proportion_ztest(50, 1000, 70, 1000, confidence_level=0.99)

    assert (narrow.ci_upper - narrow.ci_lower) < (default.ci_upper - default.ci_lower)
    assert (default.ci_upper - default.ci_lower) < (wide.ci_upper - wide.ci_lower)
    assert wide.confidence_level == 0.99


def test_numpy_integers_are_accepted_and_stored_as_python_ints():
    r = two_proportion_ztest(np.int64(50), np.int64(1000), np.int64(70), np.int64(1000))

    assert type(r.control_n) is int
    assert r.z_statistic == pytest.approx(1.8831, abs=5e-5)


def test_result_is_immutable():
    r = two_proportion_ztest(50, 1000, 70, 1000)
    assert isinstance(r, TwoProportionResult)
    with pytest.raises(Exception):
        r.p_value = 0.0  # type: ignore[misc]
