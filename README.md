# ad-uplift-experiment-pipeline

Status: In Progress

- **A1 Data Foundation:** technically complete (raw CSV -> validated Parquet -> private S3 object, integrity verified). Documentation and acceptance are pending final review.
- **Next:** A2 Cloud Warehouse + dbt. Not started: no Snowflake or dbt work exists yet.
- The A/B test, sample-ratio diagnostics and power / MDE analysis were built ahead of schedule on the local CSV and will be reconciled with the dbt marts later.

## Dataset

- Source: [Criteo Uplift Prediction Dataset](https://ailab.criteo.com/?p=859)
- License: CC BY-NC-SA 4.0 (non-commercial). The dataset is not included in this repo and is subject to its own license.
- Version used: v2.1 (13,979,592-row version on the Criteo page)
- File: `criteo-uplift-v2.1.csv`, 3,248,115,221 bytes (~3.0 GiB), SHA-256 `e4d7c710ca1f38e523309d0f8a0745d1b53e7392d51f20d1088b6cfeaef222ef`
- Rows: 13,979,592 (13,979,593 lines including header); columns: 16 (f0–f11, treatment, conversion, visit, exposure)

## Data layers and storage

| Layer | Location | Notes |
|---|---|---|
| Raw CSV | `data/criteo-uplift-v2.1.csv` (local, git-ignored) | Downloaded from Criteo; checksum above |
| Validated Parquet | `data/criteo-uplift-v2.1.parquet` (local, git-ignored) | Made by `src/ingestion/csv_to_parquet.py`; validation report in `outputs/parquet_conversion_report.json` |
| Raw layer in S3 | private bucket, key `raw/criteo-uplift-v2.1.parquet` | Same bytes as the local Parquet file; verified results are in `RESULTS.md` |

No data and no credentials are stored in this repository. To rebuild the Parquet layer from the CSV:

```bash
python src/ingestion/csv_to_parquet.py
```

To store it in your own S3 bucket, create a private bucket with Block Public Access on and configure the AWS CLI with your own named profile (never commit keys or `.env` files). `docs/aws_iam_policy.json` is the bucket-scoped IAM policy used for the uploader (list, read and write only; replace the bucket name with yours). The commands below are the standard AWS CLI calls; replace the placeholders with your own values.

```bash
aws s3 cp data/criteo-uplift-v2.1.parquet s3://<your-bucket>/raw/criteo-uplift-v2.1.parquet --profile <your-profile>
aws s3api head-object --bucket <your-bucket> --key raw/criteo-uplift-v2.1.parquet --profile <your-profile>   # compare ContentLength
aws s3 cp s3://<your-bucket>/raw/criteo-uplift-v2.1.parquet /tmp/check.parquet --profile <your-profile>
shasum -a 256 data/criteo-uplift-v2.1.parquet /tmp/check.parquet                                            # the two must match
```

A Parquet file you rebuild may differ byte-for-byte from the one recorded in `RESULTS.md`, so compare against your own `outputs/parquet_conversion_report.json`, not against my checksum.

## Running the analysis stages

Every stage reads `data/criteo-uplift-v2.1.csv` and writes a JSON report to `outputs/`
(both are git-ignored). Run them from the project root; `make <stage>` does the same
inside `.venv`.

| Stage | Command | Writes |
|---|---|---|
| Explore | `python -m src.validation.explore` | `outputs/explore_results.json` |
| Checks | `python -m src.validation.checks` | `outputs/checks_results.json` |
| SRM diagnostics | `python -m src.validation.srm_check` | `outputs/srm_results.json` |
| A/B test | `python -m src.experimentation.ab_test` | `outputs/ab_test_results.json` |
| Power / MDE | `python -m src.experimentation.power_analysis` | `outputs/power_analysis_results.json` |
| Ingest | `python -m src.ingestion.csv_to_parquet` | Parquet + `outputs/parquet_conversion_report.json` |

`--data` points a stage at another CSV and `--output` moves the report. `checks` exits
1 when a binary column holds a value other than 0 or 1 (the printed line names the
column) and 2 when the CSV is missing, so it can gate a pipeline step.

The exploratory stages are importable as a library: nothing runs at import time and
no function prints or reads the file unless it is asked to.

```python
from src.validation.checks import run_checks
report = run_checks("data/criteo-uplift-v2.1.csv")  # dict, no printing, no files
```

### Planning a sample size

`power_analysis` covers both directions of the normal approximation: the effect a
sample can detect (MDE) and the sample a chosen effect needs.

```python
from src.experimentation.power_analysis import required_sample_size

required_sample_size(0.0019, relative_lift=0.05, allocation_ratio=1.0)
# -> n_control / n_treatment per arm, the smallest sizes that reach 80% power
```

The answer is verified, not just solved: power at the returned size reaches the
target and power one control user lower does not. The saved report includes the same
table for the 1%, 5%, 10% and 20% lifts at the observed allocation ratio.

## License and Data Use

Code in this repository is released under the MIT License (see `LICENSE`). The raw dataset and any user-level tables derived from it are not redistributed here. Published results are aggregates only, and any assumed ad cost, conversion value or profit is labeled as simulation / assumption.

## Citation

Data: Criteo Uplift Prediction Dataset, Criteo AI Lab.

```bibtex
@inproceedings{Diemert2018,
  author    = {Diemert, Eustache and Betlei, Artem and Renaudin, Christophe and Amini, Massih-Reza},
  title     = {A Large Scale Benchmark for Uplift Modeling},
  booktitle = {Proceedings of the AdKDD and TargetAd Workshop, KDD},
  address   = {London, United Kingdom},
  year      = {2018},
  publisher = {ACM}
}
```
