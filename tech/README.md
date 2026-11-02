# Technology notes

These notes teach every technology used in `observability-three-signals`, from zero, through this repository's own files. They assume you have never used any of them. Every source file, configuration file, dashboard, alert rule, compose file and Makefile line in the repository is quoted and explained in one of them. Each note has the same nine parts: what it is, why this repository uses it, where it appears, how it works underneath, a walkthrough of the repository's use, commands to try, common mistakes, five questions to check yourself, and links to the official documentation for the pinned version.

## If you have ten minutes

Read these three, in this order:

1. [opentelemetry.md](opentelemetry.md): the three signals and how they are produced and linked.
2. [opentelemetry-collector.md](opentelemetry-collector.md): where the signals go and how they are split.
3. [grafana.md](grafana.md): how the clicks between metric, trace and log are wired.

## Reading order

**The language and its standard library**

1. [python.md](python.md): the language, the layered design, the model, the use cases, the settings and the investigation walk.
2. [python-logging.md](python-logging.md): JSON log lines with trace ids.
3. [python-threads-and-signals.md](python-threads-and-signals.md): the load scenario and clean shutdown.
4. [argparse-and-urllib.md](argparse-and-urllib.md): the command-line tools and the health probe.

**Web and database libraries**

5. [http.md](http.md): the protocol everything speaks.
6. [flask.md](flask.md): the two web applications.
7. [waitress.md](waitress.md): the server that runs them.
8. [requests.md](requests.md): the front's call to the back, and the clients of the stores.
9. [psycopg.md](psycopg.md): the database driver and pool.
10. [sql.md](sql.md): the schema, the seed and the queries.
11. [postgresql.md](postgresql.md): the database server.

**The telemetry pipeline**

12. [opentelemetry.md](opentelemetry.md): the API, SDK and instrumentations.
13. [opentelemetry-collector.md](opentelemetry-collector.md): the Collector.
14. [promql.md](promql.md) and [prometheus.md](prometheus.md): metrics, rules and promtool.
15. [tempo.md](tempo.md): traces.
16. [loki.md](loki.md): logs.
17. [grafana.md](grafana.md): provisioning and the dashboard.

**Running everything**

18. [docker.md](docker.md): the image.
19. [docker-compose.md](docker-compose.md): the stack.
20. [make.md](make.md): the commands you type.
21. [bash.md](bash.md): the shell commands inside them.
22. [dotenv.md](dotenv.md): the `.env` files.

**Tests and quality**

23. [pytest.md](pytest.md): all tests and coverage.
24. [ruff.md](ruff.md), [mypy.md](mypy.md), [import-linter.md](import-linter.md): lint, types, layers.
25. [uv-and-lock-files.md](uv-and-lock-files.md): locked dependencies and the audit.
26. [pyproject-toml.md](pyproject-toml.md): packaging and tool settings.
27. [github-actions.md](github-actions.md): CI.

**File formats and housekeeping**

28. [yaml.md](yaml.md) and [json.md](json.md).
29. [git-and-editorconfig.md](git-and-editorconfig.md).

## The files and their roles

| File | Role |
| --- | --- |
| [python.md](python.md) | Python 3.12: the code under `domain`, `application`, most of `infrastructure` and the investigation walk. |
| [python-logging.md](python-logging.md) | The standard `logging` module and the JSON formatter. |
| [python-threads-and-signals.md](python-threads-and-signals.md) | Threads, futures, signals and clocks in the scenario and the shutdown handler. |
| [argparse-and-urllib.md](argparse-and-urllib.md) | The four command-line tools and the container health probe. |
| [http.md](http.md) | HTTP methods, statuses, headers and the `traceparent` header. |
| [flask.md](flask.md) | The front and back web applications. |
| [waitress.md](waitress.md) | The production WSGI server. |
| [requests.md](requests.md) | HTTP clients: the retrying back client and the store clients. |
| [psycopg.md](psycopg.md) | The PostgreSQL driver, the pool and the repository. |
| [sql.md](sql.md) | The three SQL files. |
| [postgresql.md](postgresql.md) | The database service, its roles and its health check. |
| [opentelemetry.md](opentelemetry.md) | The manual span, the histogram, the exemplars and the `OTEL_*` settings. |
| [opentelemetry-collector.md](opentelemetry-collector.md) | The Collector configuration. |
| [prometheus.md](prometheus.md) | Prometheus configuration, the alert rule and the promtool test. |
| [promql.md](promql.md) | Every PromQL expression in the repository. |
| [tempo.md](tempo.md) | Tempo configuration and TraceQL. |
| [loki.md](loki.md) | Loki configuration and LogQL. |
| [grafana.md](grafana.md) | Data sources, links between them and the dashboard JSON. |
| [docker.md](docker.md) | The Dockerfile and `.dockerignore`. |
| [docker-compose.md](docker-compose.md) | The Compose file, key by key. |
| [make.md](make.md) | The Makefile, target by target. |
| [bash.md](bash.md) | The shell commands in Makefile recipes, health checks and CI. |
| [dotenv.md](dotenv.md) | `.env` and `.env.example`. |
| [pytest.md](pytest.md) | Unit, observability, integration and end-to-end tests; coverage. |
| [ruff.md](ruff.md) | Linting and formatting. |
| [mypy.md](mypy.md) | Static type checking. |
| [import-linter.md](import-linter.md) | The layer and forbidden-import contracts. |
| [uv-and-lock-files.md](uv-and-lock-files.md) | The hash-pinned lock files and `pip-audit`. |
| [pyproject-toml.md](pyproject-toml.md) | Package metadata, commands and TOML. |
| [github-actions.md](github-actions.md) | The CI workflow. |
| [yaml.md](yaml.md) | The YAML features used by the configuration files. |
| [json.md](json.md) | JSON in the dashboard, the services and the logs. |
| [git-and-editorconfig.md](git-and-editorconfig.md) | `.gitignore` and `.editorconfig`. |
