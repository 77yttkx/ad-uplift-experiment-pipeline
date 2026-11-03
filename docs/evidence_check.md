# Stage Evidence Check

Tracks the "Stage Evidence Check" gate from the Project A checklist. One table per stage, added when that stage closes. Only A1 is filled in here.
`[x]` = backed by a repository artifact or a verified result named in the Evidence column. `[ ]` = not done yet. `[ ] (owner)` = needs the project owner's own confirmation; it is deliberately left unchecked.

## A1 Data Foundation (checked 2026-11-03)

| Check | Status | Evidence / note |
|---|---|---|
| All A1 tasks pass validation | [x] | See "A1 task acceptance" below |
| Meaningful commits completed | [ ] | Earlier work is committed (latest: `47598cd` CSV-to-Parquet pipeline). The S3 and AWS documentation, `docs/aws_iam_policy.json`, the resume draft and the README status update are not committed yet |
| No credentials or raw data committed | [x] | On 2026-11-03: a scan of all 9 commits and of the working-tree docs found no AWS key patterns; `git ls-files` lists no `.csv`, `.parquet`, `.env` or credentials file; `.gitignore` excludes `data/`, `*.csv`, `*.parquet`, `.env`, `outputs/`. Re-run the scan before tagging |
| `RESULTS.md` updated | [x] | "Data Pipeline", "Parquet conversion validation", "S3 raw storage" |
| `PROJECT_LOG.md` records A1 decisions and problems | [x] | Entries on the Parquet conversion, the DuckDB binary-value coercion bug, S3 storage, and the IAM policy and budget verification |
| `docs/resume_drafts.md` updated | [x] | A1 section, marked as draft |
| `INTERVIEW_NOTES.md` updated with A1 questions | [x] | Four A1 questions with talking points, plus one optional debugging story. The file is private and git-ignored, so it is not in the repository |
| I can explain every key technical choice for A1 out loud | [ ] (owner) | Only you can confirm this |
| Git tag `v0.1-data-foundation` created | [ ] | Not created. Wait for the open items below |

### A1 task acceptance

| Checklist task | Acceptance criterion | Status | Evidence / note |
|---|---|---|---|
| Repo, directory structure, README skeleton, `.gitignore` | Repo clonable; `.gitignore` has data and `.env` | [x] | `.gitignore` verified with `git check-ignore`. A fresh `git clone` was not tested by me; `origin` is set to the GitHub repo |
| Dataset access, original file, source and licence in README | Size, rows, checksum recorded; licence checked | [x] | README "Dataset" and "License" sections; file size, row count and SHA-256 match `outputs/parquet_conversion_report.json` |
| DuckDB exploration, 1-page data dictionary | Dictionary saved; group proportions recorded | [x] | `src/validation/explore.py`, `checks.py`, `docs/data_dictionary.md`, `RESULTS.md` "Data Validation" |
| 1-page brief | Every metric has a numerator and denominator | [x] | `docs/project_brief.md` metric table |
| Convert to Parquet, check schema and rows | Parquet rows equal raw rows | [x] | 13,979,592 = 13,979,592; 28 of 28 checks passed (`outputs/parquet_conversion_report.json`, git-ignored) |
| AWS: MFA, budget alert, dedicated IAM role, S3 bucket, upload raw Parquet | File visible; budget alert set; no secret in the repo | [x] | CLI checks: object size and SHA-256 match (`RESULTS.md`). Policy: `docs/aws_iam_policy.json`. Budget: $5 monthly, 3 alerts. Root MFA and the bucket settings were reported by the owner; they are not stored in the repository. File visibility in the console itself: [ ] (owner) |
| Fill in directories and template files per the final structure | `git status` shows no private files; structure matches | [~] | Private files are ignored. Present: `src/{ingestion,validation,experimentation,uplift,simulation}`, `dbt/models/{staging,intermediate,marts}`, `sql/`, `configs/`, `LICENSE`, `Makefile`, `requirements.txt`, the five `docs/` files. Missing from the target structure: the notebook files `01_eda.ipynb`, `02_experiment_analysis.ipynb`, `03_uplift_analysis.ipynb` (`notebooks/` has only `.gitkeep`) and `pyproject.toml` (the structure lists it as an alternative to `requirements.txt`). `docs/architecture.png`, `decision_memo.pdf` and `presentation.pdf` belong to A5 |
| A1 close-out: RESULTS, resume draft, interview questions, Evidence Check | Every number reproducible by a script | [x] | This file, `docs/resume_drafts.md`, `INTERVIEW_NOTES.md`, `RESULTS.md` |

## A2 to A5

Not started. Add a table here when each stage closes.
