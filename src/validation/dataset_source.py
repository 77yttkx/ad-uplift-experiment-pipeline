"""DuckDB access to the Criteo CSV, shared by the exploratory validation scripts.

Both ``explore.py`` and ``checks.py`` need the same two things: an in-memory
DuckDB connection and a view over the CSV that keeps the original column order.
Keeping that in one place means the path quoting, the view name and the "dataset
missing" message cannot drift between the two scripts.

Nothing here runs at import time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "criteo-uplift-v2.1.csv"

#: Column names of the Criteo Uplift v2.1 CSV, in file order.
CRITEO_COLUMNS = tuple(f"f{i}" for i in range(12)) + (
    "treatment",
    "conversion",
    "visit",
    "exposure",
)

#: Columns that may only hold 0 or 1.
BINARY_COLUMNS = ("treatment", "conversion", "visit", "exposure")

VIEW_NAME = "criteo"


def sql_string(value: str | Path) -> str:
    """Quote ``value`` as a SQL string literal, doubling any single quote."""
    return "'" + str(value).replace("'", "''") + "'"


def quote(name: str) -> str:
    """Quote ``name`` as a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def require_dataset(path: str | Path = DEFAULT_DATA_PATH) -> Path:
    """Return ``path`` as a ``Path``, or raise ``FileNotFoundError`` naming it."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    return path


def as_number(value: Any) -> Any:
    """Keep a parsed value as it was read: whole numbers as ``int``, rest as ``float``.

    Reports must not truncate: a 0.5 in a column and a 0 must stay two different
    values, or two groups silently collapse into one row.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    number = float(value)
    return int(number) if number.is_integer() else number


def connect(data_path: str | Path = DEFAULT_DATA_PATH) -> duckdb.DuckDBPyConnection:
    """Open an in-memory connection with the CSV exposed as a view.

    Raises:
        FileNotFoundError: ``data_path`` does not exist.
    """
    path = require_dataset(data_path)
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            f"CREATE VIEW {quote(VIEW_NAME)} AS "
            f"SELECT * FROM read_csv_auto({sql_string(path)}, header=true)"
        )
    except Exception:
        con.close()
        raise
    return con
