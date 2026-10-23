"""Pure statistical helpers for comparing two proportions in a randomized experiment.

Nothing in this module reads files, prints, or touches global state, so every
function can be tested with plain numbers.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from numbers import Integral
from typing import Any

from scipy.stats import norm

METHOD_SIGNIFICANCE_TEST = (
    "Two-sided two-proportion z-test; standard error pooled under H0: p_treatment = p_control"
)
METHOD_CONFIDENCE_INTERVAL = (
    "Unpooled Wald interval for the absolute difference (treatment minus control)"
)


@dataclass(frozen=True)
class TwoProportionResult:
    """Outcome of comparing a treatment proportion against a control proportion.

    ``absolute_lift`` is ``treatment_rate - control_rate`` as a proportion
    (0.001 means 0.1 percentage points). ``relative_lift`` is the absolute lift
    divided by ``control_rate``.

    A field is ``None`` when the quantity is mathematically undefined for the
    input, for example ``relative_lift`` when the control rate is 0. ``notes``
    explains every such case and warns when the Wald interval is unreliable.
    """

    control_successes: int
    control_n: int
    treatment_successes: int
    treatment_n: int
    control_rate: float
    treatment_rate: float
    absolute_lift: float
    relative_lift: float | None
    z_statistic: float | None
    p_value: float | None
    ci_lower: float
    ci_upper: float
    confidence_level: float
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict (``notes`` as a list) that ``json.dumps`` accepts."""
        data = asdict(self)
        data["notes"] = list(self.notes)
        return data


def _as_count(name: str, value: Any) -> int:
    """Return ``value`` as an ``int``, rejecting bools, floats and strings."""
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer, got {type(value).__name__}")
    return int(value)


def two_proportion_ztest(
    control_successes: int,
    control_n: int,
    treatment_successes: int,
    treatment_n: int,
    *,
    confidence_level: float = 0.95,
) -> TwoProportionResult:
    """Compare two proportions with a two-sided z-test and an unpooled Wald interval.

    The z-statistic and p-value test H0: p_treatment = p_control using the
    pooled standard error. The confidence interval for the absolute difference
    uses the unpooled standard error, so the interval and the test can differ
    slightly near the decision boundary. That is expected.

    Args:
        control_successes: Successes (for example conversions) in the control group.
        control_n: Number of users in the control group. Must be positive.
        treatment_successes: Successes in the treatment group.
        treatment_n: Number of users in the treatment group. Must be positive.
        confidence_level: Coverage of the interval, strictly between 0 and 1.

    Returns:
        A ``TwoProportionResult``.

    Raises:
        TypeError: A count is not an integer (floats and bools are rejected).
        ValueError: A sample size is not positive, a success count is negative or
            larger than its sample size, or ``confidence_level`` is not in (0, 1).
    """
    x0 = _as_count("control_successes", control_successes)
    n0 = _as_count("control_n", control_n)
    x1 = _as_count("treatment_successes", treatment_successes)
    n1 = _as_count("treatment_n", treatment_n)

    for label, successes, n in (("control", x0, n0), ("treatment", x1, n1)):
        if n <= 0:
            raise ValueError(f"{label}_n must be positive, got {n}")
        if successes < 0:
            raise ValueError(f"{label}_successes must be non-negative, got {successes}")
        if successes > n:
            raise ValueError(
                f"{label}_successes ({successes}) cannot exceed {label}_n ({n})"
            )

    if not 0 < confidence_level < 1:
        raise ValueError(
            f"confidence_level must be strictly between 0 and 1, got {confidence_level}"
        )

    p0 = x0 / n0
    p1 = x1 / n1
    absolute_lift = p1 - p0

    notes: list[str] = []

    relative_lift: float | None = None
    if p0 > 0:
        relative_lift = absolute_lift / p0
    else:
        notes.append("relative_lift is undefined because the control rate is 0.")

    # Test statistic: variance pooled under H0.
    pooled = (x0 + x1) / (n0 + n1)
    se_null = math.sqrt(pooled * (1 - pooled) * (1 / n0 + 1 / n1))
    z_statistic: float | None = None
    p_value: float | None = None
    if se_null > 0:
        z_statistic = float(absolute_lift / se_null)
        p_value = float(2 * norm.sf(abs(z_statistic)))
    else:
        notes.append(
            "z_statistic and p_value are undefined because the pooled rate is 0 or 1."
        )

    # Confidence interval: unpooled Wald.
    se_ci = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    z_critical = float(norm.ppf(1 - (1 - confidence_level) / 2))
    ci_lower = absolute_lift - z_critical * se_ci
    ci_upper = absolute_lift + z_critical * se_ci
    if p0 in (0.0, 1.0) or p1 in (0.0, 1.0):
        notes.append(
            "The Wald interval is unreliable because a group rate is exactly 0 or 1: "
            "that group adds no variance, so the interval is too narrow."
        )

    return TwoProportionResult(
        control_successes=x0,
        control_n=n0,
        treatment_successes=x1,
        treatment_n=n1,
        control_rate=p0,
        treatment_rate=p1,
        absolute_lift=absolute_lift,
        relative_lift=relative_lift,
        z_statistic=z_statistic,
        p_value=p_value,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        confidence_level=confidence_level,
        notes=tuple(notes),
    )
