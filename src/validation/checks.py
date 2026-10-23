"""Day 1 follow-up checks on the Criteo uplift dataset.

1. Binary validation: treatment, conversion, visit, exposure contain only 0/1.
2. Outcome consistency: relationship between visit and conversion.
3. Exposure vs. treatment: exposure is a post-assignment variable, not the randomized treatment.
"""

from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = PROJECT_ROOT / "data" / "criteo-uplift-v2.1.csv"

if not DATA_PATH.exists():
    raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")

con = duckdb.connect(":memory:")
csv_path = DATA_PATH.as_posix().replace("'", "''")
con.execute(
    f"""
    CREATE VIEW criteo AS
    SELECT * FROM read_csv_auto('{csv_path}', header=true)
    """
)

BINARY_COLS = ["treatment", "conversion", "visit", "exposure"]

# 1. Binary validation (one scan)
print("\n=== 1. BINARY VALIDATION ===")
select_parts = []
for c in BINARY_COLS:
    select_parts.append(f"MIN({c}) AS {c}_min")
    select_parts.append(f"MAX({c}) AS {c}_max")
    select_parts.append(f"COUNT(DISTINCT {c}) AS {c}_distinct")
    select_parts.append(f"SUM(CASE WHEN {c} NOT IN (0, 1) THEN 1 ELSE 0 END) AS {c}_non_binary")
row = con.execute(f"SELECT {', '.join(select_parts)} FROM criteo").fetchone()
cols = [d[0] for d in con.description]
stats = dict(zip(cols, row))
all_ok = True
for c in BINARY_COLS:
    ok = stats[f"{c}_non_binary"] == 0
    all_ok = all_ok and ok
    print(
        f"{c}: min={stats[f'{c}_min']}, max={stats[f'{c}_max']}, "
        f"distinct={stats[f'{c}_distinct']}, non-binary rows={stats[f'{c}_non_binary']:,} "
        f"-> {'OK' if ok else 'PROBLEM'}"
    )
print("All four columns binary:", all_ok)

# 2. Outcome consistency: visit x conversion
print("\n=== 2. VISIT x CONVERSION ===")
print(
    con.execute(
        """
        SELECT visit, conversion, COUNT(*) AS rows
        FROM criteo
        GROUP BY visit, conversion
        ORDER BY visit, conversion
        """
    ).df().to_string(index=False)
)
conv_without_visit = con.execute(
    "SELECT COUNT(*) FROM criteo WHERE conversion = 1 AND visit = 0"
).fetchone()[0]
print(f"\nconversion = 1 AND visit = 0: {conv_without_visit:,} rows")

# 3. Exposure vs. treatment
print("\n=== 3. EXPOSURE BY TREATMENT ===")
print(
    con.execute(
        """
        SELECT
            treatment,
            COUNT(*) AS users,
            SUM(exposure) AS exposed_users,
            AVG(CAST(exposure AS DOUBLE)) AS exposure_rate,
            AVG(CAST(visit AS DOUBLE)) AS visit_rate,
            AVG(CAST(conversion AS DOUBLE)) AS conversion_rate
        FROM criteo
        GROUP BY treatment
        ORDER BY treatment
        """
    ).df().to_string(index=False)
)

print("\n=== 3b. TREATMENT x EXPOSURE ===")
print(
    con.execute(
        """
        SELECT
            treatment,
            exposure,
            COUNT(*) AS users,
            AVG(CAST(visit AS DOUBLE)) AS visit_rate,
            AVG(CAST(conversion AS DOUBLE)) AS conversion_rate
        FROM criteo
        GROUP BY treatment, exposure
        ORDER BY treatment, exposure
        """
    ).df().to_string(index=False)
)

control_exposed = con.execute(
    "SELECT COUNT(*) FROM criteo WHERE treatment = 0 AND exposure = 1"
).fetchone()[0]
print(f"\ntreatment = 0 AND exposure = 1: {control_exposed:,} rows")

con.close()
print("\nChecks completed.")
