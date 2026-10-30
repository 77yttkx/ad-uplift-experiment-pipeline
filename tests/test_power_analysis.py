"""Unit tests for src/experimentation/power_analysis.py.

Reference values come from two independent sources:

* ``reference_power`` below, a stdlib-only implementation (``math.erfc``) written
  separately from the module under test.
* Constants computed once with statsmodels 0.15.0
  (``statsmodels.stats.proportion.power_proportions_2indep``) and hard-coded here.
  statsmodels is not a project dependency.

These are checks of the arithmetic of a theoretical model. They say nothing about
the Criteo data or the validity of any experiment.
"""

import json
import math
from statistics import NormalDist

import numpy as np
import pytest

from src.experimentation.power_analysis import (
    CONTROL_CONVERSIONS,
    CONTROL_N,
    TREATMENT_CONVERSIONS,
    TREATMENT_N,
    MdeResult,
    PowerResult,
    allocation_sensitivity,
    build_report,
    check_against_ab_test_results,
    minimum_detectable_effect,
    power_for_relative_lifts,
    power_two_proportions,
    run,
)

P0 = CONTROL_CONVERSIONS / CONTROL_N
P1 = TREATMENT_CONVERSIONS / TREATMENT_N
_NORMAL = NormalDist()


def reference_power(p0, p1, n0, n1, alpha=0.05):
    """Independent stdlib implementation of the documented power formula."""
    z = _NORMAL.inv_cdf(1 - alpha / 2)
    pbar = (n0 * p0 + n1 * p1) / (n0 + n1)
    se0 = math.sqrt(pbar * (1 - pbar) * (1 / n0 + 1 / n1))
    se1 = math.sqrt(p0 * (1 - p0) / n0 + p1 * (1 - p1) / n1)
    d = p1 - p0
    return (1 - _NORMAL.cdf((z * se0 - d) / se1)) + _NORMAL.cdf((-z * se0 - d) / se1)


def all_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from all_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from all_keys(item)


# ------------------------------------------------------- reference numerical cases


@pytest.mark.parametrize(
    "p0, p1, n0, n1, expected",
    [
        # statsmodels 0.15.0 power_proportions_2indep (group 1 = treatment).
        (0.10, 0.15, 685, 685, 0.7996585689341483),
        (0.10, 0.15, 400, 800, 0.680560402425116),
        (0.10, 0.15, 800, 400, 0.7096484258399829),
        (0.02, 0.03, 3000, 6000, 0.8081723256935753),
        (P0, P0 * 1.01, CONTROL_N, TREATMENT_N, 0.0892647819818652),
        (P0, P0 * 1.05, CONTROL_N, TREATMENT_N, 0.8255184485958739),
        (P0, P0 * 1.10, CONTROL_N, TREATMENT_N, 0.9999312355613136),
    ],
)
def test_power_matches_statsmodels_reference(p0, p1, n0, n1, expected):
    assert power_two_proportions(p0, p1, n0, n1).power == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize(
    "p0, p1, n0, n1",
    [(0.10, 0.15, 685, 685), (0.3, 0.2, 50, 400), (P0, P1, CONTROL_N, TREATMENT_N)],
)
def test_power_matches_stdlib_reference(p0, p1, n0, n1):
    assert power_two_proportions(p0, p1, n0, n1).power == pytest.approx(
        reference_power(p0, p1, n0, n1), rel=1e-9, abs=1e-12
    )


def test_textbook_case_needs_686_per_group():
    # Detecting 10% vs 15% with 80% power: 685 per group falls just short, 686 clears it.
    assert power_two_proportions(0.10, 0.15, 685, 685).power < 0.80
    assert power_two_proportions(0.10, 0.15, 686, 686).power >= 0.80


def test_regression_mde_for_the_project_sizes():
    # Hard-coded from the first run; statsmodels returns power 0.8000000000003 at this effect.
    mde = minimum_detectable_effect(P0, CONTROL_N, TREATMENT_N)

    assert mde.absolute_mde == pytest.approx(9.369158694117545e-05, rel=1e-9)
    assert mde.relative_mde_percent == pytest.approx(4.835475147567502, rel=1e-9)
    assert mde.absolute_mde_percentage_points == pytest.approx(mde.absolute_mde * 100)


def test_mde_matches_an_independent_stdlib_search():
    # Plain bisection on the stdlib reference, sharing no code with the module.
    n0, n1, p0 = 500, 1500, 0.10
    low, high = 1e-9, 0.5
    for _ in range(200):
        mid = (low + high) / 2
        if reference_power(p0, p0 + mid, n0, n1) < 0.80:
            low = mid
        else:
            high = mid
    assert minimum_detectable_effect(p0, n0, n1).absolute_mde == pytest.approx(high, rel=1e-8)


def test_hand_checkable_zero_effect_power_equals_alpha():
    r = power_two_proportions(0.1, 0.1, 1000, 3000, alpha=0.05)
    assert r.power == pytest.approx(0.05, abs=1e-12)
    assert r.type_ii_error == pytest.approx(0.95, abs=1e-12)


def test_standard_errors_follow_the_documented_formulas():
    r = power_two_proportions(0.10, 0.15, 400, 800)

    pbar = (400 * 0.10 + 800 * 0.15) / 1200
    assert r.pooled_probability == pytest.approx(pbar)
    assert r.se_null == pytest.approx(math.sqrt(pbar * (1 - pbar) * (1 / 400 + 1 / 800)))
    assert r.se_alternative == pytest.approx(math.sqrt(0.1 * 0.9 / 400 + 0.15 * 0.85 / 800))
    assert r.critical_z == pytest.approx(1.959963984540054)
    assert r.absolute_effect == pytest.approx(0.05)
    assert r.relative_effect == pytest.approx(0.5)
    assert r.power + r.type_ii_error == pytest.approx(1.0, abs=1e-12)


# --------------------------------------------------------------- input validation


@pytest.mark.parametrize("bad", [0, 1, -0.1, 1.5, float("nan")])
def test_probabilities_out_of_range_raise_value_error(bad):
    with pytest.raises(ValueError):
        power_two_proportions(bad, 0.2, 100, 100)
    with pytest.raises(ValueError):
        power_two_proportions(0.1, bad, 100, 100)
    with pytest.raises(ValueError):
        minimum_detectable_effect(bad, 100, 100)


@pytest.mark.parametrize("bad", [True, "0.1", None, [0.1]])
def test_non_numeric_probabilities_raise_type_error(bad):
    with pytest.raises(TypeError):
        power_two_proportions(bad, 0.2, 100, 100)


@pytest.mark.parametrize("bad", [0, -5])
def test_non_positive_sample_sizes_raise_value_error(bad):
    with pytest.raises(ValueError):
        power_two_proportions(0.1, 0.2, bad, 100)
    with pytest.raises(ValueError):
        power_two_proportions(0.1, 0.2, 100, bad)
    with pytest.raises(ValueError):
        minimum_detectable_effect(0.1, bad, 100)


@pytest.mark.parametrize("bad", [100.0, 10.5, True, "100", None])
def test_non_integer_sample_sizes_raise_type_error(bad):
    with pytest.raises(TypeError):
        power_two_proportions(0.1, 0.2, bad, 100)
    with pytest.raises(TypeError):
        minimum_detectable_effect(0.1, 100, bad)


@pytest.mark.parametrize("bad", [0, 1, -0.05, 2, float("nan")])
def test_invalid_alpha_raises_value_error(bad):
    with pytest.raises(ValueError):
        power_two_proportions(0.1, 0.2, 100, 100, alpha=bad)
    with pytest.raises(ValueError):
        minimum_detectable_effect(0.1, 100, 100, alpha=bad)


@pytest.mark.parametrize("bad", [0, 1, -0.2, 1.2, float("nan")])
def test_invalid_target_power_raises_value_error(bad):
    with pytest.raises(ValueError):
        minimum_detectable_effect(0.1, 1000, 1000, power=bad)


@pytest.mark.parametrize("target", [0.05, 0.01])
def test_target_power_not_above_alpha_raises_value_error(target):
    with pytest.raises(ValueError, match="exceed alpha"):
        minimum_detectable_effect(0.1, 1000, 1000, alpha=0.05, power=target)


def test_unattainable_power_raises_value_error():
    # One user per group cannot reach 99% power for any admissible effect.
    with pytest.raises(ValueError, match="cannot be reached"):
        minimum_detectable_effect(0.5, 1, 1, power=0.99)


def test_numpy_integers_are_accepted():
    r = power_two_proportions(0.1, 0.15, np.int64(500), np.int64(500))
    assert type(r.n_control) is int


def test_result_objects_are_immutable():
    with pytest.raises(Exception):
        power_two_proportions(0.1, 0.15, 100, 100).power = 1.0  # type: ignore[misc]
    with pytest.raises(Exception):
        minimum_detectable_effect(0.1, 1000, 1000).absolute_mde = 0  # type: ignore[misc]


# ----------------------------------------------------------------- monotonicity


def test_power_increases_with_effect_size():
    powers = [power_two_proportions(0.05, 0.05 + d, 2000, 4000).power for d in (0, 0.005, 0.01, 0.02, 0.04)]
    assert powers == sorted(powers)
    assert powers[0] < powers[-1]


def test_power_increases_with_either_sample_size():
    base = power_two_proportions(0.05, 0.06, 2000, 4000).power
    assert power_two_proportions(0.05, 0.06, 4000, 4000).power > base
    assert power_two_proportions(0.05, 0.06, 2000, 8000).power > base


def test_power_increases_when_alpha_is_relaxed():
    strict = power_two_proportions(0.05, 0.06, 2000, 4000, alpha=0.01).power
    default = power_two_proportions(0.05, 0.06, 2000, 4000, alpha=0.05).power
    loose = power_two_proportions(0.05, 0.06, 2000, 4000, alpha=0.20).power
    assert strict < default < loose


def test_power_for_a_decrease_uses_both_tails_and_matches_the_reference():
    down = power_two_proportions(0.10, 0.08, 3000, 3000).power
    assert 0 < down < 1
    assert down == pytest.approx(reference_power(0.10, 0.08, 3000, 3000), rel=1e-9)
    assert down > 0.05  # exceeds the zero-effect power of alpha


def test_type_ii_error_stays_accurate_when_power_is_near_one():
    r = power_two_proportions(P0, P0 * 1.5, CONTROL_N, TREATMENT_N)
    assert 0 < r.type_ii_error < 1e-12
    assert r.power == pytest.approx(1.0)


def test_mde_shrinks_with_more_data_and_grows_with_stricter_requirements():
    base = minimum_detectable_effect(0.05, 2000, 4000).absolute_mde
    assert minimum_detectable_effect(0.05, 4000, 8000).absolute_mde < base
    assert minimum_detectable_effect(0.05, 2000, 4000, power=0.90).absolute_mde > base
    assert minimum_detectable_effect(0.05, 2000, 4000, alpha=0.01).absolute_mde > base
    assert minimum_detectable_effect(0.05, 2000, 4000, alpha=0.10).absolute_mde < base


# ---------------------------------------------------------------- unequal allocation


def test_balanced_allocation_beats_unbalanced_at_the_same_total():
    balanced = power_two_proportions(0.05, 0.06, 5000, 5000).power
    unbalanced = power_two_proportions(0.05, 0.06, 1500, 8500).power
    assert balanced > unbalanced


def test_swapping_group_sizes_changes_the_result():
    a = power_two_proportions(0.10, 0.15, 400, 800).power
    b = power_two_proportions(0.10, 0.15, 800, 400).power
    assert a != pytest.approx(b, abs=1e-3)


def test_equal_sizes_are_symmetric_in_the_group_roles():
    # With n0 == n1 the null SE is symmetric, and for equal n the alternative SE is too.
    a = power_two_proportions(0.10, 0.15, 500, 500)
    b = power_two_proportions(0.15, 0.10, 500, 500)
    assert a.power == pytest.approx(b.power, rel=1e-9)


def test_unequal_mde_is_not_the_equal_allocation_shortcut():
    unequal = minimum_detectable_effect(0.002, 2_000_000, 12_000_000).absolute_mde
    # Equal groups holding the same total would give a different (smaller) MDE.
    equal = minimum_detectable_effect(0.002, 7_000_000, 7_000_000).absolute_mde
    assert equal < unequal


# --------------------------------------------------------------- solver convergence


@pytest.mark.parametrize(
    "p0, n0, n1, alpha, target",
    [
        (P0, CONTROL_N, TREATMENT_N, 0.05, 0.80),
        (0.5, 100, 100, 0.05, 0.80),
        (0.001, 10_000_000, 1_000_000, 0.01, 0.90),
        (0.9, 50, 5000, 0.10, 0.60),
    ],
)
def test_solver_converges_and_hits_the_target_power(p0, n0, n1, alpha, target):
    mde = minimum_detectable_effect(p0, n0, n1, alpha, target)

    assert isinstance(mde, MdeResult)
    assert mde.solver_converged is True
    assert 0 < mde.solver_iterations < 100
    assert mde.achieved_power == pytest.approx(target, abs=1e-9)
    assert power_two_proportions(p0, p0 + mde.absolute_mde, n0, n1, alpha).power == pytest.approx(
        target, abs=1e-9
    )
    assert mde.relative_mde == pytest.approx(mde.absolute_mde / p0)
    assert mde.p_treatment == pytest.approx(p0 + mde.absolute_mde)
    low, high = mde.solver_bracket
    assert low < mde.absolute_mde < high


def test_solver_raises_runtime_error_when_it_cannot_converge():
    with pytest.raises(RuntimeError):
        minimum_detectable_effect(0.1, 1000, 1000, maxiter=2)


def test_mde_is_a_boundary_power_below_is_under_target_and_above_is_over():
    mde = minimum_detectable_effect(0.05, 2000, 4000).absolute_mde
    assert power_two_proportions(0.05, 0.05 + mde * 0.99, 2000, 4000).power < 0.80
    assert power_two_proportions(0.05, 0.05 + mde * 1.01, 2000, 4000).power > 0.80


# -------------------------------------------------------------- scenario helpers


def test_power_for_relative_lifts_rows():
    rows = power_for_relative_lifts(P0, CONTROL_N, TREATMENT_N)

    assert [row["relative_lift_percent"] for row in rows] == pytest.approx([1, 5, 10, 20])
    powers = [row["power"] for row in rows]
    assert powers == sorted(powers)
    assert rows[1]["power"] == pytest.approx(0.8255184485958739, rel=1e-9)
    assert rows[0]["treatment_probability"] == pytest.approx(P0 * 1.01)


def test_allocation_sensitivity_holds_total_and_baseline_fixed():
    total = 10_000
    rows = allocation_sensitivity(0.05, total, (0.1, 0.5, 0.9), power=0.8)

    assert [row["n_control"] + row["n_treatment"] for row in rows] == [total] * 3
    assert rows[1]["n_treatment"] == 5000
    # The 50/50 split is the most efficient, so it has the smallest MDE.
    assert rows[1]["absolute_mde"] < rows[0]["absolute_mde"]
    assert rows[1]["absolute_mde"] < rows[2]["absolute_mde"]
    assert rows[1]["absolute_mde"] == pytest.approx(
        minimum_detectable_effect(0.05, 5000, 5000).absolute_mde
    )


def test_allocation_sensitivity_rejects_bad_shares():
    with pytest.raises(ValueError):
        allocation_sensitivity(0.05, 1000, (0.0,))
    with pytest.raises(ValueError):
        allocation_sensitivity(0.05, 1000, (1.0,))
    with pytest.raises(ValueError):
        allocation_sensitivity(0.05, 10, (0.01,))  # rounds to an empty treatment group


# --------------------------------------------------------- consistency check, report


def write_ab_results(path, control=(CONTROL_N, CONTROL_CONVERSIONS), treatment=(TREATMENT_N, TREATMENT_CONVERSIONS)):
    path.write_text(
        json.dumps(
            {
                "results": {
                    "conversion": {
                        "control": {"n": control[0], "successes": control[1]},
                        "treatment": {"n": treatment[0], "successes": treatment[1]},
                    }
                }
            }
        )
    )
    return path


def test_consistency_check_statuses(tmp_path):
    assert check_against_ab_test_results(write_ab_results(tmp_path / "ok.json"))["status"] == "consistent"
    changed = write_ab_results(tmp_path / "bad.json", control=(CONTROL_N, CONTROL_CONVERSIONS + 1))
    assert check_against_ab_test_results(changed)["status"] == "inconsistent"
    assert check_against_ab_test_results(tmp_path / "missing.json")["status"] == "not_available"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert check_against_ab_test_results(broken)["status"] == "not_available"
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    assert check_against_ab_test_results(empty)["status"] == "not_available"


def make_report():
    return build_report(consistency_check={"status": "consistent"})


def test_report_separates_assumptions_observed_hypothetical_and_post_hoc():
    report = make_report()

    assert set(report) >= {
        "metadata", "methodology", "design_assumptions", "observed_data",
        "results", "post_hoc_descriptive", "hypothetical_scenarios", "limitations",
    }
    design = report["design_assumptions"]
    assert design["alternative"] == "two-sided"
    assert design["alpha"] == 0.05
    assert design["target_power"] == 0.80
    assert design["baseline_conversion_probability"] == pytest.approx(P0)
    assert (design["n_control"], design["n_treatment"]) == (CONTROL_N, TREATMENT_N)
    assert report["observed_data"]["control"]["conversions"] == 4063
    assert report["observed_data"]["absolute_difference_percentage_points"] == pytest.approx(0.115187, abs=5e-7)
    assert report["observed_data"]["relative_difference_percent"] == pytest.approx(59.45, abs=5e-3)
    assert "POST-HOC" in report["post_hoc_descriptive"]["label"]
    assert "HYPOTHETICAL" in report["hypothetical_scenarios"]["label"]
    assert len(report["hypothetical_scenarios"]["allocation_sensitivity"]) == 8
    assert [r["relative_lift_percent"] for r in report["results"]["power_by_relative_lift"]] == pytest.approx([1, 5, 10, 20])
    assert any("not_assessable" in text for text in report["limitations"])
    assert any("original experimental design" in text for text in report["limitations"])


def test_report_post_hoc_result_uses_the_observed_rates():
    post = make_report()["post_hoc_descriptive"]["result"]

    assert post["p_treatment"] == pytest.approx(P1)
    assert post["relative_effect"] == pytest.approx((P1 - P0) / P0)
    assert post["power"] == pytest.approx(1.0)
    assert PowerResult(**post).power == post["power"]


def test_report_is_strict_json_with_no_nan_or_infinity():
    encoded = json.dumps(make_report(), allow_nan=False)
    assert json.loads(encoded)["results"]["mde"]["solver_converged"] is True


def test_report_makes_no_srm_or_validity_claim():
    forbidden = {"srm_status", "p_value", "z_statistic"}
    assert not forbidden & set(all_keys(make_report()))


def test_run_writes_the_json_file(tmp_path):
    output = tmp_path / "nested" / "power_analysis_results.json"

    run(output)

    report = json.loads(output.read_text())
    assert report["metadata"]["script"] == "src/experimentation/power_analysis.py"
    assert report["results"]["mde"]["absolute_mde"] == pytest.approx(9.369158694117545e-05, rel=1e-9)
    assert output.read_text().endswith("\n")


def test_run_honours_custom_alpha_and_power(tmp_path):
    output = tmp_path / "out.json"

    report = run(output, alpha=0.01, target_power=0.90)

    assert report["design_assumptions"]["alpha"] == 0.01
    assert report["results"]["mde"]["target_power"] == 0.90
    assert report["results"]["mde"]["absolute_mde"] > 9.369158694117545e-05
