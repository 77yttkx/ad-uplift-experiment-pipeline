"""Shared fixtures for the test suite.

The dataset used by the project is 3 GiB and is not in the repository, so the
tests that touch DuckDB write their own small CSV with the same 16 columns.
"""

import csv
from pathlib import Path
from typing import Any

import pytest

HEADER = [f"f{i}" for i in range(12)] + ["treatment", "conversion", "visit", "exposure"]


def criteo_row(
    treatment: Any, conversion: Any = 0, visit: Any = 0, exposure: Any = 0, features: Any = None
) -> list[Any]:
    """One CSV row: 12 features, then treatment, conversion, visit, exposure.

    Values are not type-checked on purpose: several tests pass a fractional value
    (0.5) or an empty string to check that the readers reject it.
    """
    features = [0.5] * 12 if features is None else list(features)
    if len(features) != 12:
        raise ValueError(f"features must have 12 values, got {len(features)}")
    return features + [treatment, conversion, visit, exposure]


#: A small valid dataset: 4 control users, 8 treatment users, one conversion per
#: arm, exposure only in treatment, and one conversion that is also a visit.
SAMPLE_ROWS = [
    criteo_row(0),
    criteo_row(0, visit=1),
    criteo_row(0, conversion=1, visit=1),
    criteo_row(0),
    criteo_row(1),
    criteo_row(1, visit=1),
    criteo_row(1, conversion=1, visit=1),
    criteo_row(1, exposure=1),
    criteo_row(1, exposure=1, visit=1),
    criteo_row(1),
    criteo_row(1, exposure=1, conversion=1, visit=1),
    criteo_row(1),
]


def write_criteo_csv(path: Path, rows: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


@pytest.fixture
def make_criteo_csv(tmp_path):
    """Factory: write a CSV with the Criteo columns and return its path."""

    def _make(rows=None, name="criteo.csv"):
        return write_criteo_csv(tmp_path / name, SAMPLE_ROWS if rows is None else rows)

    return _make


@pytest.fixture
def criteo_csv(make_criteo_csv):
    """A small valid dataset on disk."""
    return make_criteo_csv()
