# Make (the Makefile)

## What it is

Make is a command-line tool that runs named recipes from a file called `Makefile`. Each recipe is a **target**: a name, optionally the files or other targets it needs (its **prerequisites**), and the shell commands that produce it. Make was designed to rebuild only what is out of date, by comparing file modification times. Teams also use it as a simple "task runner": `make test` is shorter and more memorable than the long command it hides.

## Why this repository uses it

The lesson's promise is "clone, run `make up`, then `make run`". The Makefile is the front door: every command a learner needs is a short target with a one-line description, and CI calls the same targets, so what passes on a laptop is what passes in CI.

## Where it appears

| Path | What it does |
| --- | --- |
| `Makefile` | All targets. |
| `.editorconfig` | Sets `indent_style = tab` for the Makefile so the editor keeps recipe tabs. |
| `.github/workflows/ci.yml` | Calls `make check`, `make audit`, `make up`, `make test-integration test-e2e`, `make down`. |

## How it works underneath

You type `make up`. Make reads `Makefile` in the current folder, finds the target `up`, and first makes each of its prerequisites (right of the colon). A prerequisite that is a file is rebuilt only if it does not exist or is older than the files it depends on. A prerequisite that is only a name is always run. Then Make runs the target's **recipe**: each line indented with one **tab** character is handed to a shell (`/bin/sh`) as a separate command. If any command exits with a non-zero code, Make stops and reports the error.

Things to know:

- A **variable** is set with `NAME := value` (evaluated once, right away) or `NAME ?= value` (set only if not already set, so you can override it from the shell). `$(NAME)` reads it. `$@` is the name of the current target. `$(MAKE)` is the Make program itself, used to call Make again.
- `.PHONY` lists targets that are *not* files. Without it, a file called `test` in the folder would make `make test` say "nothing to do".
- A line starting with `@` is run without printing the command first.
- `-include .env` reads another Makefile-style file and does not fail if it is missing. `export` with no names passes every Make variable to the commands as environment variables.
- Each recipe line runs in its own shell, so `cd` or a variable set on one line is gone on the next. Joining lines with `\` makes them one command.
- `##` comments after a target are a convention: the `help` target greps them to print a menu.

## Walkthrough of this repository's use

### Variables and settings

```makefile
# make up | run | follow | test | lint | down      (make help lists everything)
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
VENV := .venv
PY := $(VENV)/bin/python
STAMP := $(VENV)/.installed
PROMETHEUS_IMAGE := prom/prometheus:v2.54.1
COLLECTOR_IMAGE := otel/opentelemetry-collector-contrib:0.111.0
```

Line 1 is a comment listing the main commands. `PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)` picks the interpreter: `$(shell ...)` runs a shell command and uses its output; `command -v NAME` prints the path of `NAME` if it exists (and fails if not); `||` tries the next one. Python 3.13 is preferred, then 3.12, then any `python3`. `?=` lets you override it: `make PYTHON=python3.12 install`. `VENV := .venv` is the virtual-environment folder, `PY` the interpreter inside it and `STAMP` a marker file inside it (explained below). The last two variables pin the Prometheus and Collector images that `lint-config` and `test-rules` run, the same versions as in the Compose file.

```makefile
-include .env
export
```

`-include .env` reads `.env` if it exists (the leading `-` ignores a missing file). `.env` is a list of `NAME=value` lines, which are valid Make assignments, so they become Make variables. `export` with no arguments makes all variables environment variables for the commands Make runs. That is how `$(ORDERS_READER_PASSWORD)` in the `db` recipe works, and how the Python lab tools see `FRONT_HOST_PORT`.

```makefile
.PHONY: help install up db queries run follow health lint format typecheck lint-config test test-rules \
        test-integration test-e2e audit check docker-build down clean
```

`.PHONY` declares the target names that are not files. The backslash continues the list on the next line.

### `help`

```makefile
help: ## list the targets
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort
```

The `## text` after a target is a convention. The recipe `grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST)` finds every line that looks like `name: ... ## text` (`-E` enables extended regular expressions; `^` is the start of the line; `[a-z-]+` is one or more lowercase letters or hyphens; `-h` leaves out the file name that `grep` would otherwise put in front of each line when it reads more than one file, which it does here because `-include .env` adds `.env` to the list). `$(MAKEFILE_LIST)` is the list of Makefiles read. The output goes through a pipe to `sed -E 's/:.*## /\t/'`, which replaces everything from the colon to the `##` with a tab, and then `sort` orders the lines. The leading `@` stops Make from printing the command itself.

### The environment file and the virtual environment

```makefile
.env:
	cp .env.example .env
	@echo "created .env from .env.example"
```

`.env:` is a **file target**: Make runs it only if `.env` does not exist. `cp .env.example .env` copies the template; `@echo` prints a message. `.env` is in `.gitignore`.

```makefile
$(STAMP): requirements-dev.lock pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --require-hashes -r requirements-dev.lock
	$(VENV)/bin/pip install -q --no-deps -e .
	touch $@

install: $(STAMP) ## create .venv from the lock file
```

`$(STAMP)` is `.venv/.installed`, a file that exists only after a successful install. It depends on `requirements-dev.lock` and `pyproject.toml`: if either is newer than the stamp, the recipe runs again. The recipe creates the virtual environment (`$(PYTHON) -m venv $(VENV)`), installs the locked packages with hash checking (`--require-hashes`, `-q` for quiet), installs the project in editable mode without dependencies (`-e .`), and `touch $@` creates or refreshes the stamp (`$@` is the target's name). `install` is only a friendly name for that file target.

### Starting and using the stack

```makefile
# The database first (the services connect as a role that make db creates), then everything else.
up: .env $(STAMP) ## build and start the whole stack, load the schema and seed data, wait until healthy
	docker compose up -d --wait postgres
	$(MAKE) --no-print-directory db
	docker compose up -d --build --wait --wait-timeout 1800
	$(MAKE) --no-print-directory health
```

`up` needs `.env` and the installed environment first (its prerequisites). The recipe is explained in `docker-compose.md`.

```makefile
db: .env ## run sql/001_schema.sql and sql/002_seed.sql against the running database
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 \
	  -v reader_password="$(ORDERS_READER_PASSWORD)" -q -f - < sql/001_schema.sql
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 -q -f - < sql/002_seed.sql

queries: ## run sql/003_queries.sql and print the results
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 -f - < sql/003_queries.sql
```

`db` and `queries` pipe the SQL files into `psql` (see `postgresql.md`). The trailing `\` joins a long command over two lines; Make passes it to the shell as one command.

```makefile
health: $(STAMP) ## every container, every backend and Grafana's three data sources answer
	$(PY) -m three_signals.lab.health_cli
```

`health` runs the stack-health command with the virtual environment's Python: `-m three_signals.lab.health_cli` runs a module as a program.

```makefile
# The scenario: 600 requests at 10 per second (one minute) with about 1 in 20 meeting the planted slow query,
# then walk the click path from the spike to the slow span's logs, retrying while the telemetry reaches the stores.
run: $(STAMP) ## send traffic, then follow the slowest request from the p95 panel to its trace to its logs
	$(PY) -m three_signals.lab.scenario_cli --requests 600 --rate 10
	$(MAKE) --no-print-directory follow

follow: $(STAMP) ## print the click path: spike, exemplar, trace, slow span, log lines
	$(PY) -m three_signals.lab.follow_cli
```

`run` first sends the traffic (`scenario_cli --requests 600 --rate 10`: one minute of traffic at ten requests a second), and only if that exits with 0, `$(MAKE) --no-print-directory follow` runs the next target (Make calls itself). `follow` prints the click path (see `argparse-and-urllib.md`).

### Checks

```makefile
lint: $(STAMP) lint-config ## ruff, format check, mypy, import-linter, and the observability configs
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/mypy
	$(VENV)/bin/lint-imports

format: $(STAMP) ## apply ruff's formatting and safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .
```

`lint` depends on `lint-config` as well as the install, then runs, one per line: `ruff check .` (lint), `ruff format --check .` (formatting check), `mypy` (types, using the `files` setting in `pyproject.toml`) and `lint-imports` (the import rules). Make stops at the first failing line. `format` applies the fixes: `ruff check --fix .` and `ruff format .`.

```makefile
# promtool and the Collector validate their own files, so a typo fails here and not at 3 a.m.
lint-config: ## promtool check on the Prometheus config and rules; the Collector validates its config
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  check config /etc/prometheus/prometheus.yml
	docker run --rm -v "$(CURDIR)/observability/collector/config.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml
```

`lint-config` is explained in `opentelemetry-collector.md` and `prometheus.md`: it runs two throw-away containers (`docker run --rm`) to validate the configuration files. `-v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro"` mounts a folder read-only inside the container; `$(CURDIR)` is the current directory.

```makefile
# Unit tests need no containers. Coverage is reported; the domain and application must stay at 95% or more.
test: $(STAMP) ## unit and dashboard-lint tests with coverage (no stack needed)
	$(PY) -m pytest tests/unit tests/observability --cov=three_signals --cov-report=term
	$(PY) -m coverage report --include='src/three_signals/domain/*,src/three_signals/application/*' --fail-under=95
```

`test` runs pytest over the unit and observability tests with coverage for the `three_signals` package, then `coverage report --include='...domain/*,...application/*' --fail-under=95` fails if the domain and application layers are covered less than 95 percent. The single quotes protect the `*` from the shell.

```makefile
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
```

`test-rules` replays the alert test with `promtool` (see `prometheus.md`). `test-integration` and `test-e2e` run pytest on the folders `tests/integration` and `tests/e2e`; `-m integration` and `-m e2e` select tests carrying that marker (see `pytest.md`). `audit` runs `pip-audit` on the dev lock (see `uv-and-lock-files.md`). `check` has no recipe, only prerequisites: it means "run `lint`, `test` and `test-rules`", everything that needs no running stack.

```makefile
docker-build: ## build the service image
	docker build -t observability-three-signals:dev .

down: ## stop the stack and delete its data
	docker compose down -v

clean: down
	rm -rf $(VENV) logs .pytest_cache .mypy_cache .ruff_cache .coverage .import_linter_cache
```

`docker-build` builds the image. `down` stops the stack and deletes its volumes. `clean` depends on `down` and then removes the virtual environment and every cache folder (`rm -rf` deletes recursively without asking).

## Try it yourself

```bash
make help
```

Expected: one line per target with its description, sorted.

```bash
make -n up
```

Expected: `-n` prints the commands without running them: the `docker compose` calls in order.

```bash
make install && ls .venv/.installed
```

Expected: the stamp file exists; running `make install` again prints nothing because nothing changed.

```bash
touch pyproject.toml && make -n install
```

Expected: the venv recipe is listed again, because `pyproject.toml` is now newer than the stamp.

```bash
make check
```

Expected: ruff, mypy and import-linter pass, pytest reports the tests passing with coverage, and `promtool` prints `SUCCESS`.

## Mistakes people make

- **Spaces instead of a tab** at the start of a recipe line: Make stops with "missing separator". The `.editorconfig` in this repository sets `indent_style = tab` for the Makefile.
- **Forgetting `.PHONY`** for a target that is not a file.
- **Assuming `cd` carries over** to the next line.
- **Using `$VAR` for a Make variable.** Make needs `$(VAR)`. A single `$` is for Make; to give the shell a `$`, write `$$`.
- **Silent failure in a pipeline.** In `a | b`, the exit code is that of `b`. Recipes here avoid hiding failures in pipes.

## Check yourself

1. What does `.PHONY` do? *It marks targets that are names, not files, so they always run.*
2. Why must recipe lines start with a tab? *Make uses the tab to tell a recipe command from other lines.*
3. What is the difference between `:=` and `?=`? *`:=` always sets; `?=` sets only if the variable has no value yet, so the environment can override it.*
4. What does a leading `@` do? *It hides the command line itself; only its output is shown.*
5. If a prerequisite file is newer than the target file, what does Make do? *It runs the recipe again.*

## Further reading

- GNU Make manual: https://www.gnu.org/software/make/manual/make.html (this repository works with GNU Make 3.81, the version macOS ships)
