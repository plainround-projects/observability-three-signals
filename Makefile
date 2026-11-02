# make up | run | follow | test | lint | down      (make help lists everything)
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
VENV := .venv
PY := $(VENV)/bin/python
STAMP := $(VENV)/.installed
PROMETHEUS_IMAGE := prom/prometheus:v2.54.1
COLLECTOR_IMAGE := otel/opentelemetry-collector-contrib:0.111.0

-include .env
export

.PHONY: help install up db queries run follow health lint format typecheck lint-config test test-rules \
        test-integration test-e2e audit check docker-build down clean

help: ## list the targets
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort

.env:
	cp .env.example .env
	@echo "created .env from .env.example"

$(STAMP): requirements-dev.lock pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --require-hashes -r requirements-dev.lock
	$(VENV)/bin/pip install -q --no-deps -e .
	touch $@

install: $(STAMP) ## create .venv from the lock file

# The database first (the services connect as a role that make db creates), then everything else.
up: .env $(STAMP) ## build and start the whole stack, load the schema and seed data, wait until healthy
	docker compose up -d --wait postgres
	$(MAKE) --no-print-directory db
	docker compose up -d --build --wait --wait-timeout 1800
	$(MAKE) --no-print-directory health

db: .env ## run sql/001_schema.sql and sql/002_seed.sql against the running database
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 \
	  -v reader_password="$(ORDERS_READER_PASSWORD)" -q -f - < sql/001_schema.sql
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 -q -f - < sql/002_seed.sql

queries: ## run sql/003_queries.sql and print the results
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 -f - < sql/003_queries.sql

health: $(STAMP) ## every container, every backend and Grafana's three data sources answer
	$(PY) -m three_signals.lab.health_cli

# The scenario: 600 requests at 10 per second (one minute) with about 1 in 20 meeting the planted slow query,
# then walk the click path from the spike to the slow span's logs, retrying while the telemetry reaches the stores.
run: $(STAMP) ## send traffic, then follow the slowest request from the p95 panel to its trace to its logs
	$(PY) -m three_signals.lab.scenario_cli --requests 600 --rate 10
	$(MAKE) --no-print-directory follow

follow: $(STAMP) ## print the click path: spike, exemplar, trace, slow span, log lines
	$(PY) -m three_signals.lab.follow_cli

lint: $(STAMP) lint-config ## ruff, format check, mypy, import-linter, and the observability configs
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/mypy
	$(VENV)/bin/lint-imports

format: $(STAMP) ## apply ruff's formatting and safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

# promtool and the Collector validate their own files, so a typo fails here and not at 3 a.m.
lint-config: ## promtool check on the Prometheus config and rules; the Collector validates its config
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  check config /etc/prometheus/prometheus.yml
	docker run --rm -v "$(CURDIR)/observability/collector/config.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml

# Unit tests need no containers. Coverage is reported; the domain and application must stay at 95% or more.
test: $(STAMP) ## unit and dashboard-lint tests with coverage (no stack needed)
	$(PY) -m pytest tests/unit tests/observability --cov=three_signals --cov-report=term
	$(PY) -m coverage report --include='src/three_signals/domain/*,src/three_signals/application/*' --fail-under=95

test-rules: ## replay a scenario through promtool and assert the alert fires (and stays silent when it should)
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  test rules /etc/prometheus/tests/order_assembly.rules.test.yml

test-integration: $(STAMP) ## adapters against the running stack (make up first)
	$(PY) -m pytest tests/integration -m integration

test-e2e: $(STAMP) ## the lesson's scenario, then queries of Tempo, Prometheus and Loki (make up first)
	$(PY) -m pytest tests/e2e -m e2e

audit: $(STAMP) ## check the locked dependencies for known vulnerabilities
	$(VENV)/bin/pip-audit --no-deps --disable-pip -r requirements-dev.lock

check: lint test test-rules ## everything that needs no running stack

docker-build: ## build the service image
	docker build -t observability-three-signals:dev .

down: ## stop the stack and delete its data
	docker compose down -v

clean: down
	rm -rf $(VENV) logs .pytest_cache .mypy_cache .ruff_cache .coverage .import_linter_cache
