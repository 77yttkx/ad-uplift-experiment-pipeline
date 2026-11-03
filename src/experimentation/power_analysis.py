"""Statistical power and minimum detectable effect (MDE) for two independent proportions.

Everything here is a theoretical calculation under an explicit normal-approximation
model. No dataset is read; the observed Criteo counts are constants (cross-checked
against ``outputs/ab_test_results.json`` when that file exists).

Run from the project root (either form works):

    python src/experimentation/power_analysis.py
    python -m src.experimentation.power_analysis

Results are printed and saved to ``outputs/power_analysis_results.json``.

Method
------
Let p0 be the control probability, p1 the treatment probability, n0 and n1 the
group sizes, delta = p1 - p0, and z = Phi^-1(1 - alpha/2) for a two-sided test.

* Null standard error (pooled, the standard error used by the z-test in
  ``proportion_tests.py``), evaluated at the *expected* pooled probability:
      pbar = (n0*p0 + n1*p1) / (n0 + n1)
      SE0  = sqrt(pbar*(1-pbar) * (1/n0 + 1/n1))
* Alternative standard error (unpooled):
      SE1  = sqrt(p0*(1-p0)/n0 + p1*(1-p1)/n1)
* Power = P(|Z| > critical) when the true difference is delta:
      power = SF((z*SE0 - delta)/SE1) + CDF((-z*SE0 - delta)/SE1)
  Both rejection tails are included.
* MDE: the positive delta for which power equals the target, found with
  ``scipy.optimize.brentq``. No equal-allocation or fixed effect-size shortcut.
* Sample size: the inverse question. For one effect and one allocation ratio, the
  smallest ``n_control`` whose power reaches the target, solved with
  ``scipy.optimize.brentq`` over ``log(n)`` and then rounded up. The returned
  sizes are verified: power at ``n_control`` reaches the target and power at
  ``n_control - 1`` does not.

Interpretation limits
---------------------
* The Criteo public data is a released, non-uniformly sub-sampled research sample.
  Its original assignment probability is unverified (SRM status: not_assessable),
  so the numbers here are NOT claims about the original experimental design.
* Power for the observed lift is post-hoc and descriptive only.
* Allocation scenarios are hypothetical.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import platform  # noqa: E402
from dataclasses import asdict, dataclass  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from numbers import Integral, Real  # noqa: E402
from typing import Any  # noqa: E402

import numpy  # noqa: E402
import scipy  # noqa: E402
from scipy.optimize import brentq  # noqa: E402
from scipy.stats import norm  # noqa: E402

DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "outputs" / "power_analysis_results.json"
AB_TEST_RESULTS_PATH = PROJECT_ROOT / "outputs" / "ab_test_results.json"

# Observed conversion counts (Criteo Uplift v2.1, as reported by ab_test.py).
CONTROL_N = 2_096_937
CONTROL_CONVERSIONS = 4_063
TREATMENT_N = 11_882_655
TREATMENT_CONVERSIONS = 36_711

DEFAULT_ALPHA = 0.05
DEFAULT_TARGET_POWER = 0.80
RELATIVE_LIFTS = (0.01, 0.05, 0.10, 0.20)
ALLOCATION_TREATMENT_SHARES = (0.05, 0.10, 0.15, 0.30, 0.50, 0.70, 0.85, 0.95)
ALLOCATION_REFERENCE_RELATIVE_LIFT = 0.05

SOLVER_XTOL = 1e-15
SOLVER_MAXITER = 200
#: Upper bound for the sample-size search; beyond this the required size is not
#: a useful planning answer, so the search reports failure instead of guessing.
MAX_SAMPLE_SIZE = 1_000_000_000

LIMITATIONS = [
    "The Criteo public dataset is a released, non-uniformly sub-sampled research sample "
    "whose original assignment probability is unverified (SRM status: not_assessable). "
    "These calculations do not claim that it represents the original experimental design.",
    "Power and MDE come from a normal approximation. The null standard error uses the "
    "expected pooled probability implied by the assumed rates, not a sample-specific value.",
    "The baseline rate is taken from the control group of the same data, so it carries "
    "sampling error that is not propagated into the MDE.",
    "Users are assumed independent and simple-randomized; clustering, interference and "
    "non-compliance are not modelled.",
    "Only the conversion outcome is covered. Exposure reached only a small share of "
    "treated users, so an intent-to-treat effect is diluted relative to an exposed-user effect.",
    "The MDE is computed for an increase in conversion probability (treatment above control).",
    "Power at the observed lift is post-hoc and descriptive. It is a function of the "
    "observed effect and sample size, adds no information beyond the p-value, and is "
    "not evidence that the study is valid.",
    "Allocation-ratio scenarios are hypothetical: they hold total sample size and the "
    "baseline rate fixed and say nothing about how users were actually assigned.",
    "The sample-size table is a planning calculation at the observed allocation ratio. "
    "It inherits every assumption above and is not a claim about how many users the "
    "original experiment had or should have had.",
]


# ------------------------------------------------------------------ validation


def _count(name: str, value: Any) -> int:
    """Return a positive integer count; reject bools, floats and strings."""
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return int(value)


def _real(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}")
    value = float(value)
    if math.isnan(value):
        raise ValueError(f"{name} must not be NaN")
    return value


def _probability(name: str, value: Any) -> float:
    """Return a probability strictly inside (0, 1)."""
    value = _real(name, value)
    if not 0.0 < value < 1.0:
        raise ValueError(f"{name} must be strictly between 0 and 1, got {value}")
    return value


# --------------------------------------------------------------------- results


@dataclass(frozen=True)
class PowerResult:
    """Theoretical power of the two-sided two-proportion z-test."""

    p_control: float
    p_treatment: float
    n_control: int
    n_treatment: int
    alpha: float
    absolute_effect: float
    relative_effect: float
    critical_z: float
    pooled_probability: float
    se_null: float
    se_alternative: float
    power: float
    type_ii_error: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MdeResult:
    """Smallest positive effect reaching the target power, with solver diagnostics."""

    p_control: float
    p_treatment: float
    n_control: int
    n_treatment: int
    alpha: float
    target_power: float
    absolute_mde: float
    absolute_mde_percentage_points: float
    relative_mde: float
    relative_mde_percent: float
    achieved_power: float
    solver_iterations: int
    solver_function_calls: int
    solver_converged: bool
    solver_bracket: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["solver_bracket"] = list(self.solver_bracket)
        return data


@dataclass(frozen=True)
class SampleSizeResult:
    """Smallest per-arm sizes that reach the target power for one effect."""

    p_control: float
    p_treatment: float
    absolute_effect: float
    relative_effect: float
    alpha: float
    target_power: float
    allocation_ratio: float
    n_control: int
    n_treatment: int
    total_n: int
    achieved_power: float
    power_at_one_fewer_control: float
    solver_iterations: int
    solver_function_calls: int
    solver_converged: bool
    solver_log_bracket: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["solver_log_bracket"] = list(self.solver_log_bracket)
        return data


# ----------------------------------------------------------------- calculations


def _power_terms(
    p0: float, p1: float, n0: float, n1: float, alpha: float
) -> tuple[float, float, float, float, float, float]:
    """Critical z, pooled rate, both standard errors and both rejection bounds.

    ``n0``/``n1`` are floats so the sample-size solver can work on the same
    formula the integer calculation uses.
    """
    delta = p1 - p0
    z = float(norm.isf(alpha / 2))
    pooled = (n0 * p0 + n1 * p1) / (n0 + n1)
    se_null = math.sqrt(pooled * (1 - pooled) * (1 / n0 + 1 / n1))
    se_alt = math.sqrt(p0 * (1 - p0) / n0 + p1 * (1 - p1) / n1)
    upper = (z * se_null - delta) / se_alt
    lower = (-z * se_null - delta) / se_alt
    return z, pooled, se_null, se_alt, upper, lower


def _power_float(p0: float, p1: float, n0: float, n1: float, alpha: float) -> float:
    """Power of the two-sided test for real-valued sizes (solver use only)."""
    _, _, _, _, upper, lower = _power_terms(p0, p1, n0, n1, alpha)
    return float(norm.sf(upper) + norm.cdf(lower))


def power_two_proportions(
    p_control: float,
    p_treatment: float,
    n_control: int,
    n_treatment: int,
    alpha: float = DEFAULT_ALPHA,
) -> PowerResult:
    """Theoretical power of a two-sided two-proportion z-test (unequal n allowed).

    Raises:
        TypeError: a probability or alpha is not a real number, or a size is not an integer.
        ValueError: a probability or alpha is outside (0, 1), or a size is not positive.
    """
    p0 = _probability("p_control", p_control)
    p1 = _probability("p_treatment", p_treatment)
    n0 = _count("n_control", n_control)
    n1 = _count("n_treatment", n_treatment)
    alpha = _probability("alpha", alpha)

    delta = p1 - p0
    z, pooled, se_null, se_alt, upper, lower = _power_terms(p0, p1, n0, n1, alpha)
    power = float(norm.sf(upper) + norm.cdf(lower))
    # P(not reject) = CDF(upper) - CDF(lower); use the tail that avoids cancellation.
    if delta >= 0:
        type_ii = float(norm.cdf(upper) - norm.cdf(lower))
    else:
        type_ii = float(norm.sf(lower) - norm.sf(upper))

    return PowerResult(
        p_control=p0,
        p_treatment=p1,
        n_control=n0,
        n_treatment=n1,
        alpha=alpha,
        absolute_effect=delta,
        relative_effect=delta / p0,
        critical_z=z,
        pooled_probability=pooled,
        se_null=se_null,
        se_alternative=se_alt,
        power=power,
        type_ii_error=type_ii,
    )


def minimum_detectable_effect(
    p_control: float,
    n_control: int,
    n_treatment: int,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_TARGET_POWER,
    *,
    xtol: float = SOLVER_XTOL,
    maxiter: int = SOLVER_MAXITER,
) -> MdeResult:
    """Smallest increase in conversion probability detected with the target power.

    Solves ``power(p_control + delta) = target`` for delta > 0 with Brent's method.

    Raises:
        ValueError: inputs out of range, target power not above alpha, or the target
            power cannot be reached by any admissible effect at these sample sizes.
        RuntimeError: the root solver did not converge.
    """
    p0 = _probability("p_control", p_control)
    n0 = _count("n_control", n_control)
    n1 = _count("n_treatment", n_treatment)
    alpha = _probability("alpha", alpha)
    target = _probability("power", power)
    if target <= alpha:
        raise ValueError(
            f"target power ({target}) must exceed alpha ({alpha}); a two-sided test "
            "has power alpha at zero effect"
        )

    def gap(delta: float) -> float:
        return power_two_proportions(p0, p0 + delta, n0, n1, alpha).power - target

    low = 1e-12
    high = (1 - p0) * (1 - 1e-9)
    if gap(low) >= 0:
        raise ValueError("target power is already reached at a vanishing effect; check inputs")
    if gap(high) < 0:
        raise ValueError(
            "target power cannot be reached by any effect with these sample sizes "
            f"(maximum attainable power is about {gap(high) + target:.4f})"
        )

    delta, info = brentq(
        gap, low, high, xtol=xtol, rtol=4 * numpy.finfo(float).eps, maxiter=maxiter,
        full_output=True, disp=False,
    )
    if not info.converged:
        raise RuntimeError(f"MDE root solver did not converge: {info.flag}")

    achieved = power_two_proportions(p0, p0 + delta, n0, n1, alpha).power
    return MdeResult(
        p_control=p0,
        p_treatment=p0 + delta,
        n_control=n0,
        n_treatment=n1,
        alpha=alpha,
        target_power=target,
        absolute_mde=float(delta),
        absolute_mde_percentage_points=float(delta) * 100,
        relative_mde=float(delta) / p0,
        relative_mde_percent=float(delta) / p0 * 100,
        achieved_power=achieved,
        solver_iterations=int(info.iterations),
        solver_function_calls=int(info.function_calls),
        solver_converged=bool(info.converged),
        solver_bracket=(low, high),
    )


def required_sample_size(
    p_control: float,
    *,
    relative_lift: float | None = None,
    absolute_effect: float | None = None,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_TARGET_POWER,
    allocation_ratio: float = 1.0,
    xtol: float = SOLVER_XTOL,
    maxiter: int = SOLVER_MAXITER,
    max_sample_size: int = MAX_SAMPLE_SIZE,
) -> SampleSizeResult:
    """Smallest per-arm sizes that detect one effect with the target power.

    The inverse of :func:`minimum_detectable_effect`: instead of asking what effect
    a fixed sample can detect, ask how much sample a chosen effect needs. Give
    exactly one of ``relative_lift`` (treatment = control * (1 + lift)) or
    ``absolute_effect`` (treatment = control + effect); ``allocation_ratio`` is
    ``n_treatment / n_control``.

    The continuous solution is found with Brent's method over ``log(n_control)``
    and then rounded up. The returned sizes are checked: power at ``n_control``
    reaches the target and power at ``n_control - 1`` does not, so the answer is
    the smallest sample that does the job rather than a solver artefact.

    Raises:
        TypeError: a probability, effect or ratio is not a real number, or
            ``max_sample_size`` is not an integer.
        ValueError: a probability or alpha is outside (0, 1), the effect is not
            positive or pushes the treatment probability to 1, the target power is
            not above alpha, the allocation ratio is not positive, both or neither
            effect was given, or the target cannot be reached within
            ``max_sample_size`` per arm.
        RuntimeError: the root solver did not converge.
    """
    p0 = _probability("p_control", p_control)
    alpha = _probability("alpha", alpha)
    target = _probability("power", power)
    if target <= alpha:
        raise ValueError(
            f"target power ({target}) must exceed alpha ({alpha}); a two-sided test "
            "has power alpha at zero effect"
        )
    ratio = _real("allocation_ratio", allocation_ratio)
    if ratio <= 0:
        raise ValueError(f"allocation_ratio must be positive, got {ratio}")
    ceiling = _count("max_sample_size", max_sample_size)

    if (relative_lift is None) == (absolute_effect is None):
        raise ValueError(
            "provide exactly one of relative_lift or absolute_effect; "
            f"got relative_lift={relative_lift}, absolute_effect={absolute_effect}"
        )
    if relative_lift is not None:
        lift = _real("relative_lift", relative_lift)
        if lift <= 0:
            raise ValueError(f"relative_lift must be positive, got {lift}")
        delta = p0 * lift
    else:
        delta = _real("absolute_effect", absolute_effect)
        if delta <= 0:
            raise ValueError(f"absolute_effect must be positive, got {delta}")
    p1 = p0 + delta
    if p1 >= 1.0:
        raise ValueError(
            f"the effect {delta} puts the treatment probability at {p1}, which must stay "
            f"below 1 (control probability {p0})"
        )

    def continuous_gap(log_n: float) -> float:
        n0 = math.exp(log_n)
        return _power_float(p0, p1, n0, ratio * n0, alpha) - target

    def integer_power(n_control: int) -> float:
        n_treatment = max(1, math.ceil(ratio * n_control))
        return power_two_proportions(p0, p1, n_control, n_treatment, alpha).power

    low = 0.0
    high = math.log(ceiling)
    if continuous_gap(high) < 0:
        raise ValueError(
            "target power cannot be reached within max_sample_size="
            f"{ceiling} control users (power there is about {continuous_gap(high) + target:.4f})"
        )

    if continuous_gap(low) >= 0:
        n_control = 1
        info = None
    else:
        solution, info = brentq(
            continuous_gap, low, high, xtol=xtol, rtol=4 * numpy.finfo(float).eps,
            maxiter=maxiter, full_output=True, disp=False,
        )
        if not info.converged:
            raise RuntimeError(f"sample-size root solver did not converge: {info.flag}")
        n_control = max(1, math.ceil(math.exp(solution)))

    # Rounding and floating-point noise can leave the integer answer just short of
    # (or just above) the boundary: walk to the smallest n that reaches the target.
    while integer_power(n_control) < target and n_control < ceiling:
        n_control += 1
    while n_control > 1 and integer_power(n_control - 1) >= target:
        n_control -= 1

    n_treatment = max(1, math.ceil(ratio * n_control))
    achieved = integer_power(n_control)
    if achieved < target:
        raise RuntimeError(
            f"sample-size search stopped at n_control={n_control} without reaching the "
            f"target power ({achieved:.6f} < {target})"
        )
    return SampleSizeResult(
        p_control=p0,
        p_treatment=p1,
        absolute_effect=delta,
        relative_effect=delta / p0,
        alpha=alpha,
        target_power=target,
        allocation_ratio=ratio,
        n_control=n_control,
        n_treatment=n_treatment,
        total_n=n_control + n_treatment,
        achieved_power=achieved,
        power_at_one_fewer_control=integer_power(n_control - 1) if n_control > 1 else 0.0,
        solver_iterations=0 if info is None else int(info.iterations),
        solver_function_calls=0 if info is None else int(info.function_calls),
        solver_converged=True if info is None else bool(info.converged),
        solver_log_bracket=(low, high),
    )


def sample_size_for_relative_lifts(
    p_control: float,
    n_control: int,
    n_treatment: int,
    relative_lifts: tuple[float, ...] = RELATIVE_LIFTS,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_TARGET_POWER,
) -> list[dict[str, Any]]:
    """Per-arm sample size for each relative lift, at the given allocation ratio."""
    n0 = _count("n_control", n_control)
    n1 = _count("n_treatment", n_treatment)
    ratio = n1 / n0
    rows = []
    for lift in relative_lifts:
        lift = _real("relative_lift", lift)
        result = required_sample_size(
            p_control,
            relative_lift=lift,
            alpha=alpha,
            power=power,
            allocation_ratio=ratio,
        )
        rows.append(
            {
                "relative_lift": lift,
                "relative_lift_percent": lift * 100,
                "absolute_effect": result.absolute_effect,
                "absolute_effect_percentage_points": result.absolute_effect * 100,
                "treatment_probability": result.p_treatment,
                "allocation_ratio_treatment_over_control": result.allocation_ratio,
                "n_control": result.n_control,
                "n_treatment": result.n_treatment,
                "total_n": result.total_n,
                "achieved_power": result.achieved_power,
            }
        )
    return rows


def power_for_relative_lifts(
    p_control: float,
    n_control: int,
    n_treatment: int,
    relative_lifts: tuple[float, ...] = RELATIVE_LIFTS,
    alpha: float = DEFAULT_ALPHA,
) -> list[dict[str, Any]]:
    """Power at each relative lift (treatment = control * (1 + lift))."""
    rows = []
    for lift in relative_lifts:
        lift = _real("relative_lift", lift)
        result = power_two_proportions(
            p_control, p_control * (1 + lift), n_control, n_treatment, alpha
        )
        rows.append(
            {
                "relative_lift": lift,
                "relative_lift_percent": lift * 100,
                "absolute_effect": result.absolute_effect,
                "absolute_effect_percentage_points": result.absolute_effect * 100,
                "treatment_probability": result.p_treatment,
                "power": result.power,
                "type_ii_error": result.type_ii_error,
            }
        )
    return rows


def allocation_sensitivity(
    p_control: float,
    total_n: int,
    treatment_shares: tuple[float, ...] = ALLOCATION_TREATMENT_SHARES,
    alpha: float = DEFAULT_ALPHA,
    power: float = DEFAULT_TARGET_POWER,
    reference_relative_lift: float = ALLOCATION_REFERENCE_RELATIVE_LIFT,
) -> list[dict[str, Any]]:
    """HYPOTHETICAL: MDE and power for other treatment shares at fixed total N."""
    total = _count("total_n", total_n)
    reference_relative_lift = _real("reference_relative_lift", reference_relative_lift)
    rows = []
    for share in treatment_shares:
        share = _probability("treatment_share", share)
        n1 = round(total * share)
        n0 = total - n1
        if n0 < 1 or n1 < 1:
            raise ValueError(f"treatment_share {share} leaves an empty group at total_n={total}")
        mde = minimum_detectable_effect(p_control, n0, n1, alpha, power)
        ref = power_two_proportions(
            p_control, p_control * (1 + reference_relative_lift), n0, n1, alpha
        )
        rows.append(
            {
                "treatment_share": share,
                "n_control": n0,
                "n_treatment": n1,
                "absolute_mde": mde.absolute_mde,
                "absolute_mde_percentage_points": mde.absolute_mde_percentage_points,
                "relative_mde_percent": mde.relative_mde_percent,
                "power_at_reference_relative_lift": ref.power,
            }
        )
    return rows


def check_against_ab_test_results(path: Path = AB_TEST_RESULTS_PATH) -> dict[str, Any]:
    """Compare the constants above with ``ab_test_results.json`` (record, never fail)."""
    if not path.exists():
        return {"status": "not_available", "detail": f"{path.name} not found"}
    try:
        with open(path, encoding="utf-8") as handle:
            conversion = json.load(handle)["results"]["conversion"]
        found = {
            "control_n": conversion["control"]["n"],
            "control_conversions": conversion["control"]["successes"],
            "treatment_n": conversion["treatment"]["n"],
            "treatment_conversions": conversion["treatment"]["successes"],
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"status": "not_available", "detail": f"could not read counts: {error!r}"}
    expected = {
        "control_n": CONTROL_N,
        "control_conversions": CONTROL_CONVERSIONS,
        "treatment_n": TREATMENT_N,
        "treatment_conversions": TREATMENT_CONVERSIONS,
    }
    return {
        "status": "consistent" if found == expected else "inconsistent",
        "file": path.name,
        "expected": expected,
        "found": found,
    }


# ----------------------------------------------------------------------- report


def build_report(
    alpha: float = DEFAULT_ALPHA,
    target_power: float = DEFAULT_TARGET_POWER,
    consistency_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the full JSON-ready report."""
    p0 = CONTROL_CONVERSIONS / CONTROL_N
    p1 = TREATMENT_CONVERSIONS / TREATMENT_N
    if consistency_check is None:
        consistency_check = check_against_ab_test_results()

    mde = minimum_detectable_effect(p0, CONTROL_N, TREATMENT_N, alpha, target_power)
    by_lift = power_for_relative_lifts(p0, CONTROL_N, TREATMENT_N, RELATIVE_LIFTS, alpha)
    planning = sample_size_for_relative_lifts(
        p0, CONTROL_N, TREATMENT_N, RELATIVE_LIFTS, alpha, target_power
    )
    observed = power_two_proportions(p0, p1, CONTROL_N, TREATMENT_N, alpha)
    allocation = allocation_sensitivity(
        p0, CONTROL_N + TREATMENT_N, ALLOCATION_TREATMENT_SHARES, alpha, target_power
    )

    return {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "script": "src/experimentation/power_analysis.py",
            "environment": {
                "python": platform.python_version(),
                "numpy": numpy.__version__,
                "scipy": scipy.__version__,
            },
        },
        "methodology": {
            "test": "Two-sided two-proportion z-test, independent groups, unequal n allowed",
            "approximation": "Normal approximation to the sampling distribution of the difference",
            "pooled_probability": "(n_control*p_control + n_treatment*p_treatment) / (n_control + n_treatment)",
            "se_null": "sqrt(pbar*(1-pbar) * (1/n_control + 1/n_treatment))",
            "se_alternative": "sqrt(p_control*(1-p_control)/n_control + p_treatment*(1-p_treatment)/n_treatment)",
            "critical_value": "z = norm.isf(alpha/2)",
            "power": "SF((z*SE0 - delta)/SE1) + CDF((-z*SE0 - delta)/SE1), both rejection tails",
            "mde_solver": "scipy.optimize.brentq on power(p_control + delta) - target_power, delta > 0",
            "relative_effect": "absolute effect divided by the control baseline probability",
        },
        "design_assumptions": {
            "scope_note": (
                "Assumptions applied to the released public sample's group sizes. They are "
                "not claims about the original Criteo experimental design."
            ),
            "alternative": "two-sided",
            "alpha": alpha,
            "target_power": target_power,
            "baseline_conversion_probability": p0,
            "baseline_source": "control conversions / control n in this dataset",
            "n_control": CONTROL_N,
            "n_treatment": TREATMENT_N,
            "treatment_share": TREATMENT_N / (CONTROL_N + TREATMENT_N),
            "allocation_ratio_treatment_over_control": TREATMENT_N / CONTROL_N,
        },
        "observed_data": {
            "control": {"n": CONTROL_N, "conversions": CONTROL_CONVERSIONS, "rate": p0},
            "treatment": {"n": TREATMENT_N, "conversions": TREATMENT_CONVERSIONS, "rate": p1},
            "absolute_difference": p1 - p0,
            "absolute_difference_percentage_points": (p1 - p0) * 100,
            "relative_difference_percent": (p1 - p0) / p0 * 100,
            "consistency_with_ab_test_results": consistency_check,
        },
        "results": {
            "mde": mde.to_dict(),
            "power_by_relative_lift": by_lift,
            "required_sample_size_by_relative_lift": planning,
        },
        "post_hoc_descriptive": {
            "label": "POST-HOC DESCRIPTIVE: power evaluated at the observed lift",
            "caveat": (
                "Post-hoc power is a function of the observed effect and sample size. It "
                "adds no information beyond the p-value and is not evidence of study validity."
            ),
            "result": observed.to_dict(),
        },
        "hypothetical_scenarios": {
            "label": "HYPOTHETICAL: not how users were actually assigned",
            "description": (
                "MDE and power under other treatment shares, holding total sample size and "
                "the baseline rate fixed."
            ),
            "total_n": CONTROL_N + TREATMENT_N,
            "reference_relative_lift": ALLOCATION_REFERENCE_RELATIVE_LIFT,
            "allocation_sensitivity": allocation,
        },
        "limitations": LIMITATIONS,
    }


def print_report(report: dict[str, Any]) -> None:
    mde = report["results"]["mde"]
    design = report["design_assumptions"]
    print("\n=== POWER ANALYSIS (theoretical, normal approximation) ===")
    print(
        f"Design assumptions: two-sided, alpha={design['alpha']}, target power="
        f"{design['target_power']}, baseline={design['baseline_conversion_probability']:.6%}"
    )
    print(f"Group sizes: control {design['n_control']:,}, treatment {design['n_treatment']:,}")
    print(
        f"\nMDE: {mde['absolute_mde']:.6e} absolute "
        f"({mde['absolute_mde_percentage_points']:.5f} pp), "
        f"{mde['relative_mde_percent']:.3f}% relative"
    )
    print(
        f"  solver: converged={mde['solver_converged']}, iterations={mde['solver_iterations']}, "
        f"achieved power={mde['achieved_power']:.10f}"
    )
    print("\nPower by assumed relative lift:")
    for row in report["results"]["power_by_relative_lift"]:
        print(f"  {row['relative_lift_percent']:>5.1f}%  power = {row['power']:.4f}")
    print("\nSample size per arm for that target power (observed allocation ratio "
          f"{report['design_assumptions']['allocation_ratio_treatment_over_control']:.2f}:1):")
    for row in report["results"]["required_sample_size_by_relative_lift"]:
        print(
            f"  {row['relative_lift_percent']:>5.1f}%  control {row['n_control']:,} + "
            f"treatment {row['n_treatment']:,} = {row['total_n']:,} users "
            f"(power {row['achieved_power']:.4f})"
        )
    post = report["post_hoc_descriptive"]["result"]
    print(
        f"\n[POST-HOC, descriptive only] power at observed lift "
        f"({post['relative_effect']:.2%}): {post['power']:.6f}"
    )
    print("\n[HYPOTHETICAL] treatment share, MDE (relative %), power at "
          f"{report['hypothetical_scenarios']['reference_relative_lift']:.0%} lift:")
    for row in report["hypothetical_scenarios"]["allocation_sensitivity"]:
        print(
            f"  {row['treatment_share']:>5.0%}  {row['relative_mde_percent']:>7.3f}%  "
            f"{row['power_at_reference_relative_lift']:.4f}"
        )
    check = report["observed_data"]["consistency_with_ab_test_results"]
    print(f"\nConsistency with ab_test_results.json: {check['status']}")
    print("\n--- Interpretation limits ---")
    for line in LIMITATIONS[:2]:
        print(f"* {line}")


def run(
    output_path: Path = DEFAULT_OUTPUT_PATH,
    alpha: float = DEFAULT_ALPHA,
    target_power: float = DEFAULT_TARGET_POWER,
) -> dict[str, Any]:
    report = build_report(alpha, target_power)
    print_report(report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(f"\nSaved: {output_path}")
    return report


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="JSON results path"
    )
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA, help="two-sided alpha")
    parser.add_argument(
        "--power", type=float, default=DEFAULT_TARGET_POWER, help="target power for the MDE"
    )
    args = parser.parse_args(argv)
    run(args.output, args.alpha, args.power)


if __name__ == "__main__":
    main()
