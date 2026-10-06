.PHONY: setup explore clean

setup:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

# Filled in as stages are built:
# ingest:   download -> Parquet -> validate
# dbt-build: dbt build
# analyze:  experiment analysis
# uplift:   model + evaluation

clean:
	rm -rf .venv __pycache__
