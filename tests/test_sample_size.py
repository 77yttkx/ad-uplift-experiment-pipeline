"""Tests for the sample-size planner (the inverse of the MDE calculation).

Two independent checks stand behind the numbers here:

* the planner is run backwards against ``minimum_detectable_effect``: asking for the
  sample that detects the MDE at the observed allocation must return the observed
  sample size;
* at equal allocation the answer is compared with the textbook closed form
  (``(z_{1-a/2} * sqrt(2*pbar*qbar) + z_{1-b} * sqrt(p0*q0 + p1*q1))^2 / delta^2``),
  which is an independent way of writing the same normal approximation.

The search bracket is also verified directly: power at the returned size reaches
the target and power one control user lower does not.
"""

import json
import math
from statistics import NormalDist

import numpy as np
import pytest

from src.experimentation.power_analysis import (
    CONTROL_CONVERSIONS,
    CONTROL_N,
    TREATMENT_N,
    SampleSizeResult,
    build_report,
    minimum_detectable_effect,
    power_two_proportions,
    required_sample_size,
    sample_size_for_relative_lifts,
)

P0 = CONTROL_CONVERSIONS / CONTROL_N
RATIO = TREATMENT_N / CONTROL_N
_NORMAL = NormalDist()


def closed_form_n_per_arm(p0, p1, alpha=0.05, power=0.80):
    """Textbook normal-approximation sample size for equal groups."""
    z_alpha = _NORMAL.inv_cdf(1 - alpha / 2)
    z_beta = _NORMAL.inv_cdf(power)
    pbar = (p0 + p1) / 2
    delta = p1 - p0
    return (
        z_alpha * math.sqrt(2 * pbar * (1 - pbar)) + z_beta * math.sqrt(p0 * (1 - p0) + p1 * (1 - p1))
    ) ** 2 / delta**2


# ------------------------------------------------------------------- correctness


def test_round_trip_inverts_the_mde():
    mde = minimum_detectable_effect(P0, CONTROL_N, TREATMENT_N)

    result = required_sample_size(P0, absolute_effect=mde.absolute_mde, allocation_ratio=RATIO)

    assert result.n_control == CONTROL_N
    assert result.n_treatment == TREATMENT_N
    assert result.achieved_power >= result.target_power


def test_returned_size_is_the_smallest_one_that_reaches_the_target():
    result = required_sample_size(P0, relative_lift=0.05, allocation_ratio=RATIO)

    n0, n1 = result.n_control, result.n_treatment
    assert power_two_proportions(P0, result.p_treatment, n0, n1).power >= 0.80
    assert power_two_proportions(P0, result.p_treatment, n0 - 1, n1).power < 0.80
    assert result.power_at_one_fewer_control < result.target_power


@pytest.mark.parametrize("lift", [0.02, 0.05, 0.10, 0.20])
def test_equal_allocation_matches_the_closed_form(lift):
    p1 = P0 * (1 + lift)

    result = required_sample_size(P0, relative_lift=lift, allocation_ratio=1.0)
    textbook = closed_form_n_per_arm(P0, p1)

    assert result.n_control == pytest.approx(textbook, rel=1e-4)
    assert result.n_treatment == result.n_control


def test_bigger_effects_need_fewer_users():
    small = required_sample_size(P0, relative_lift=0.05, allocation_ratio=1.0)
    large = required_sample_size(P0, relative_lift=0.10, allocation_ratio=1.0)

    assert large.n_control < small.n_control


def test_higher_power_needs_more_users():
    eighty = required_sample_size(P0, relative_lift=0.05, power=0.80, allocation_ratio=1.0)
    ninety = required_sample_size(P0, relative_lift=0.05, power=0.90, allocation_ratio=1.0)

    assert ninety.n_control > eighty.n_control


def test_stricter_alpha_needs_more_users():
    relaxed = required_sample_size(P0, relative_lift=0.05, alpha=0.10, allocation_ratio=1.0)
    strict = required_sample_size(P0, relative_lift=0.05, alpha=0.01, allocation_ratio=1.0)

    assert strict.n_control > relaxed.n_control


def test_a_balanced_split_needs_fewer_users_than_the_observed_ratio():
    balanced = required_sample_size(P0, relative_lift=0.05, allocation_ratio=1.0)
    observed = required_sample_size(P0, relative_lift=0.05, allocation_ratio=RATIO)

    assert balanced.total_n < observed.total_n


def test_treatment_size_follows_the_allocation_ratio():
    for ratio in (0.5, 1.0, 2.0, RATIO):
        result = required_sample_size(P0, relative_lift=0.05, allocation_ratio=ratio)
        assert result.n_treatment == math.ceil(ratio * result.n_control)
        assert result.total_n == result.n_control + result.n_treatment


def test_relative_lift_and_absolute_effect_agree():
    lift = 0.05
    by_lift = required_sample_size(P0, relative_lift=lift, allocation_ratio=1.0)
    by_effect = required_sample_size(
        P0, absolute_effect=P0 * lift, allocation_ratio=1.0
    )

    assert by_effect.n_control == by_lift.n_control
    assert by_effect.p_treatment == pytest.approx(by_lift.p_treatment)
    assert by_effect.relative_effect == pytest.approx(lift)


def test_result_records_the_solver_and_the_effect():
    result = required_sample_size(P0, relative_lift=0.05, allocation_ratio=RATIO)

    assert isinstance(result, SampleSizeResult)
    assert result.solver_converged is True
    assert result.solver_iterations > 0
    assert result.solver_function_calls >= result.solver_iterations
    assert result.solver_log_bracket == (0.0, math.log(1_000_000_000))
    assert result.absolute_effect == pytest.approx(P0 * 0.05)
    assert result.p_control == P0


def test_result_is_immutable():
    result = required_sample_size(P0, relative_lift=0.05)

    with pytest.raises(Exception):
        result.n_control = 1  # type: ignore[misc]


def test_to_dict_serialises_the_bracket_as_a_list():
    payload = required_sample_size(P0, relative_lift=0.05).to_dict()

    assert payload["solver_log_bracket"] == [0.0, math.log(1_000_000_000)]
    assert json.loads(json.dumps(payload, allow_nan=False))["n_control"] == payload["n_control"]


def test_numpy_scalars_are_accepted():
    result = required_sample_size(np.float64(P0), relative_lift=np.float64(0.05))

    assert result.n_control > 0


# -------------------------------------------------------------------- validation


def test_both_effects_are_rejected():
    with pytest.raises(ValueError, match="exactly one of relative_lift or absolute_effect"):
        required_sample_size(P0, relative_lift=0.05, absolute_effect=0.001)


def test_no_effect_is_rejected():
    with pytest.raises(ValueError, match="exactly one of relative_lift or absolute_effect"):
        required_sample_size(P0)


@pytest.mark.parametrize("bad", [0.0, -0.05])
def test_non_positive_relative_lift_is_rejected(bad):
    with pytest.raises(ValueError, match="relative_lift must be positive"):
        required_sample_size(P0, relative_lift=bad)


@pytest.mark.parametrize("bad", [0.0, -0.001])
def test_non_positive_absolute_effect_is_rejected(bad):
    with pytest.raises(ValueError, match="absolute_effect must be positive"):
        required_sample_size(P0, absolute_effect=bad)


def test_effect_larger_than_the_remaining_probability_is_rejected():
    with pytest.raises(ValueError, match="must stay"):
        required_sample_size(P0, absolute_effect=1.0)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_non_positive_allocation_ratio_is_rejected(bad):
    with pytest.raises(ValueError, match="allocation_ratio must be positive"):
        required_sample_size(P0, relative_lift=0.05, allocation_ratio=bad)


def test_target_power_at_or_below_alpha_is_rejected():
    with pytest.raises(ValueError, match="must exceed alpha"):
        required_sample_size(P0, relative_lift=0.05, alpha=0.05, power=0.05)


def test_unreachable_target_within_max_sample_size_is_rejected():
    with pytest.raises(ValueError, match="cannot be reached within max_sample_size"):
        required_sample_size(P0, relative_lift=0.01, max_sample_size=10_000)


def test_non_integer_max_sample_size_is_rejected():
    with pytest.raises(TypeError, match="max_sample_size must be an integer"):
        required_sample_size(P0, relative_lift=0.05, max_sample_size=1e6)  # type: ignore[arg-type]


def test_probability_outside_the_unit_interval_is_rejected():
    with pytest.raises(ValueError, match="p_control must be strictly between 0 and 1"):
        required_sample_size(0.0, relative_lift=0.05)


# ------------------------------------------------------------------------- table


def test_table_rows_cover_every_lift_and_shrink_with_it():
    rows = sample_size_for_relative_lifts(P0, CONTROL_N, TREATMENT_N)

    assert [row["relative_lift"] for row in rows] == [0.01, 0.05, 0.10, 0.20]
    sizes = [row["total_n"] for row in rows]
    assert sizes == sorted(sizes, reverse=True)
    for row in rows:
        assert row["n_treatment"] == math.ceil(RATIO * row["n_control"])
        assert row["achieved_power"] >= 0.80
        assert row["allocation_ratio_treatment_over_control"] == pytest.approx(RATIO)


def test_table_matches_direct_calls():
    rows = sample_size_for_relative_lifts(P0, CONTROL_N, TREATMENT_N, relative_lifts=(0.05,))

    direct = required_sample_size(P0, relative_lift=0.05, allocation_ratio=RATIO)
    assert rows[0]["n_control"] == direct.n_control
    assert rows[0]["n_treatment"] == direct.n_treatment
    assert rows[0]["absolute_effect"] == pytest.approx(direct.absolute_effect)


def test_table_validates_the_group_sizes():
    with pytest.raises(TypeError, match="n_control must be an integer"):
        sample_size_for_relative_lifts(P0, CONTROL_N + 0.5, TREATMENT_N)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="n_treatment must be positive"):
        sample_size_for_relative_lifts(P0, CONTROL_N, 0)


# ------------------------------------------------------------------------- report


def test_report_includes_the_planning_table():
    report = build_report()
    rows = report["results"]["required_sample_size_by_relative_lift"]

    assert [row["relative_lift_percent"] for row in rows] == [1.0, 5.0, 10.0, 20.0]
    assert all(row["n_control"] > 0 and row["n_treatment"] > 0 for row in rows)
    assert report["design_assumptions"]["allocation_ratio_treatment_over_control"] == pytest.approx(
        RATIO
    )
    assert json.dumps(report, allow_nan=False)


def test_report_limits_mention_the_planning_table():
    limitations = build_report()["limitations"]

    assert any("sample-size table" in line for line in limitations)
