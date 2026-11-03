.PHONY: setup test ingest explore checks srm ab power clean

setup:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# Stages that exist. `make test` runs the suite; the rest read
# data/criteo-uplift-v2.1.csv and write a JSON report to outputs/.
test:
	. .venv/bin/activate && python -m pytest

explore:
	. .venv/bin/activate && python -m src.validation.explore

checks:
	. .venv/bin/activate && python -m src.validation.checks

srm:
	. .venv/bin/activate && python -m src.validation.srm_check

ab:
	. .venv/bin/activate && python -m src.experimentation.ab_test

power:
	. .venv/bin/activate && python -m src.experimentation.power_analysis

# ingest:   download -> Parquet -> validate
ingest:
	. .venv/bin/activate && python -m src.ingestion.csv_to_parquet

# Still to come with A2: dbt-build (dbt build) and uplift (model + evaluation).

clean:
	rm -rf .venv __pycache__
