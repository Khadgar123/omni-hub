# quant sub-instance — runs in its OWN venv (duckdb/pyarrow/polars), NEVER the
# stdlib-only main repo.  Defaults to the `quant` conda env (like researchflow's
# Makefile); override PYTHON to use a plain venv:  make test PYTHON=python
PYTHON ?= conda run --no-capture-output -n quant python
REPO_ROOT := $(shell git rev-parse --show-toplevel)

.PHONY: install test test-quant test-finance sample smoke clean

install:
	$(PYTHON) -m pip install -e ".[dev]"

# Everything this layer owns: the quant store tests + the finance ingestion tests.
test: test-quant test-finance

test-quant:
	$(PYTHON) -m pytest

test-finance:
	$(PYTHON) -m pytest \
	  "$(REPO_ROOT)/tests/test_binance_market_data.py" \
	  "$(REPO_ROOT)/tests/test_binance_spot_live.py"

# Regenerate the committed sample artifact (sample/trades.ndjson).
sample:
	$(PYTHON) -m quant.sample

# Materialize the sample into a throwaway store and read it back (no network).
smoke:
	$(PYTHON) -m quant.market_store --root /tmp/quant-smoke ingest-sample
	$(PYTHON) -m quant.market_store --root /tmp/quant-smoke bars-from-trades \
	  --symbol DEMO --freq 1d --start 2026-01-01 --end 2026-01-10 --persist

clean:
	rm -rf /tmp/quant-smoke build dist *.egg-info quant/__pycache__ tests/__pycache__
