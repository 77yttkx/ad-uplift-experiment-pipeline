
from pathlib import Path

import duckdb

# 1. Locate the dataset relative to the project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = PROJECT_ROOT / "data" / "criteo-uplift-v2.1.csv"

if not DATA_PATH.exists():
    raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")

# 2. Connect to an in-memory DuckDB database
con = duckdb.connect(":memory:")


# 3. Load CSV into a DuckDB view
csv_path = DATA_PATH.as_posix().replace("'", "''")

con.execute(
    f"""
    CREATE VIEW criteo AS
    SELECT *
    FROM read_csv_auto('{csv_path}', header=true)
    """
)


# 4. Inspect dataset dimensions
print("\n=== DATASET SIZE ===")
print(con.execute("SELECT COUNT(*) AS total_rows FROM criteo").df())

# 5. Inspect columns and data types
print("\n=== COLUMN SCHEMA ===")
print(con.execute("DESCRIBE criteo").df().to_string(index=False))

# 6. Preview the data
print("\n=== FIRST 5 ROWS ===")
print(con.execute("SELECT * FROM criteo LIMIT 5").df().to_string(index=False))

# 7. Check missing values
print("\n=== MISSING VALUES ===")

columns = [
    row[0]
    for row in con.execute("DESCRIBE criteo").fetchall()
]

for col in columns:
    missing = con.execute(
        f'SELECT COUNT(*) FROM criteo WHERE "{col}" IS NULL'
    ).fetchone()[0]

    print(f"{col}: {missing:,} missing")

# 8. Inspect treatment and conversion distribution
print("\n=== TREATMENT DISTRIBUTION ===")

print(
    con.execute(
        """
        SELECT
            treatment,
            COUNT(*) AS users,
            AVG(CAST(conversion AS DOUBLE)) AS conversion_rate
        FROM criteo
        GROUP BY treatment
        ORDER BY treatment
        """
    ).df().to_string(index=False)
)

# 9. Calculate the raw difference in conversion rates
print("\n=== RAW CONVERSION RATE DIFFERENCE ===")

print(
    con.execute(
        """
        SELECT
            AVG(CASE WHEN treatment = 1
                THEN CAST(conversion AS DOUBLE) END)
            -
            AVG(CASE WHEN treatment = 0
                THEN CAST(conversion AS DOUBLE) END)
            AS absolute_lift
        FROM criteo
        """
    ).df().to_string(index=False)
)

con.close()

print("\nExploration completed successfully.")
