# Resume Drafts

Drafts only. Not the final resume. Do not write unverified numbers.
Every number below traces to `RESULTS.md` (sections "Data Pipeline", "Parquet conversion validation" and "S3 raw storage") and to `outputs/parquet_conversion_report.json`. Pick the final 2-3 bullets in A5, after re-checking each number against the repository.

## A1 (DRAFT, not final)

Candidate bullets:

- Built a reproducible DuckDB pipeline that streams a 3.25 GB CSV of 13,979,592 Criteo advertising-experiment records into ZSTD-compressed Parquet (185,895,062 bytes, 94.28% smaller) without loading the data into pandas, on an 8 GB laptop.
- Added automated validation and row-count reconciliation (28 checks covering schema, missing values, binary-value domains, group counts and a CSV-to-Parquet content fingerprint) that withholds the output when any check fails; covered by a 217-test automated suite (pytest).
- Stored the validated file in a private, encrypted AWS S3 bucket (Block Public Access, SSE-S3, bucket-scoped IAM policy, budget alerts) and confirmed integrity with a matching SHA-256 on the object downloaded back.

Scope notes (keep these true if the bullets are edited):

- This is data engineering and data-quality work on a public research dataset. It is not a production deployment, and it has no business impact or performance improvement to claim.
- The 94.28% figure is the file-size difference between the CSV and the Parquet output, not a speed or cost result.
- The 217 tests cover the whole project (statistics, SRM, power analysis, ingestion), not only ingestion; the ingestion module has 64 of them.
- Snowflake, dbt and the later stages are not done and must not appear in A1 bullets.
