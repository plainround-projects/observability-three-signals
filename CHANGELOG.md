# Changelog

All notable changes to this project are recorded here. The format follows Keep a Changelog and the project uses
semantic versioning.

## [0.1.0] - 2026-10-27

### Added

- Two services (`front`, `back`) and a PostgreSQL database, instrumented with OpenTelemetry for traces, metrics and
  logs, with a trace id and span id on every JSON log line.
- A Collector (memory limiter, batch) that routes traces to Tempo, metrics to Prometheus (with exemplars) and logs to
  Loki, and Grafana with all three as provisioned data sources and a dashboard as code.
- A planted slow query (one `order_items` lookup in twenty) and the scenario and walk-through commands that follow it
  from the p95 panel to the slow span to its log lines.
- An alert rule on the share of slow assemblies, with a promtool replay test.
- Unit, integration and end-to-end tests, ruff, mypy and import-linter checks, and a CI workflow.
