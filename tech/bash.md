# Bash and shell commands

## What it is

A **shell** is a program that reads commands you type (or that a file contains) and runs them. Bash and `sh` are shells. On Linux and macOS, almost every command-line task goes through a shell: it finds the program, starts it as a process, connects its input and output, and reports its **exit code**.

This repository has no standalone `.sh` file. Shell appears in four places: in the recipes of the `Makefile`, in the `test:` lines of Docker health checks, in the `run:` lines of the CI workflow, and in the commands you type in your terminal. All of them are plain shell, so this note teaches the shell features they use.

## Why this repository uses it

Shell commands glue the pieces together: the Makefile recipes call `docker`, `psql`, `grep` and `sed`; Docker health checks run `wget` and `grep` inside containers; the CI workflow runs `make`. There are no separate shell script files, so every command is explained here where it appears.

## Where it appears

| Path | The shell it contains |
| --- | --- |
| `Makefile` | Recipe lines, run by `/bin/sh`. |
| `docker-compose.yml` | The `test:` health-check commands of postgres, tempo, prometheus, loki and grafana (`CMD-SHELL`). |
| `.github/workflows/ci.yml` | `run:` steps. |
| `tests/integration/test_stack.py` | `docker compose exec ... psql` called from Python. |

## How it works underneath

- A **command** is a program name followed by **arguments**: `ls -l /tmp`. The shell splits the line into words at spaces, then starts the program with those words.
- A **flag** (or option) is an argument that starts with `-` or `--` and changes what the program does.
- **Exit code.** Every program ends with a number. `0` means success; anything else means failure. `$?` holds the last exit code. `&&` runs the next command only if the previous succeeded; `||` only if it failed.
- **Streams.** Each process has standard input (stdin), standard output (stdout) and standard error (stderr). `>` sends stdout to a file; `<` reads stdin from a file; `|` (a **pipe**) connects the stdout of the left command to the stdin of the right one; `2>&1` sends stderr to the same place as stdout.
- **Quoting.** Inside double quotes `"..."` variables are still replaced (`"$HOME"`); inside single quotes `'...'` nothing is replaced. Quoting protects spaces and special characters from being split or interpreted.
- **Environment variables.** Named strings that every process inherits from its parent. `NAME=value command` sets one only for that command.
- **Command substitution.** `$(command)` is replaced by the output of the command.
- **Globs.** `*.rules.yml` is expanded by the shell into every matching file name.
- **`set -euo pipefail`** (not used in this repository because there are no script files, but you will see it everywhere): `-e` stops at the first failing command, `-u` makes using an unset variable an error, `-o pipefail` makes a pipeline fail if any part of it fails.

## Walkthrough of this repository's use

### Health checks in `docker-compose.yml`

`CMD-SHELL` means: join the following string and run it with the container's `/bin/sh`. Exit code 0 = healthy.

```yaml
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U postgres -d orders"]
```

`pg_isready -h 127.0.0.1 -U postgres -d orders` has no pipe; its exit code is the answer. The flags: `-h` host, `-U` user, `-d` database.

```yaml
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3200/ready | grep -q ready"]
```

`wget -q -O - http://localhost:3200/ready | grep -q ready`. `wget` downloads a URL: `-q` suppresses its progress text, `-O -` writes the body to standard output instead of a file. The `|` connects that output to `grep -q ready`, which searches for the word `ready` and, with `-q`, prints nothing and just exits 0 if found, 1 if not. If `wget` itself fails (connection refused) there is no input and `grep` exits 1. On the Alpine-based images `wget` is the BusyBox version, which has these flags.

```yaml
      test: ["CMD-SHELL", "wget -q -O - http://localhost:9090/-/healthy"]
```

For Prometheus, `wget -q -O - http://localhost:9090/-/healthy` has no `grep`: `wget` exits non-zero on any HTTP error status, so its exit code is enough.

```yaml
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3100/ready | grep -q ready"]
```

Loki: the same as Tempo, on port 3100.

```yaml
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3000/api/health | grep -q ok"]
```

Grafana: `wget -q -O - http://localhost:3000/api/health | grep -q ok` looks for `ok` in the JSON that `/api/health` returns (`"database": "ok"`).

### Makefile recipes

```makefile
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort
```

`grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort`: three commands in a **pipeline**. `grep` prints the matching lines (`-h` drops the file-name prefix, so a line starts with the target name), `sed` (a stream editor) replaces with `s/old/new/` (`\t` is a tab; `.*` is any characters), and `sort` orders the lines alphabetically.

```makefile
PYTHON ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3)
```

`$(shell command -v python3.13 || command -v python3.12 || command -v python3)`: `command -v` prints where a command is; `||` runs the next command only when the previous one failed. The first that exists wins.

```makefile
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 \
	  -v reader_password="$(ORDERS_READER_PASSWORD)" -q -f - < sql/001_schema.sql
	docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 -q -f - < sql/002_seed.sql
```

`docker compose exec -T postgres psql -U postgres -d orders -v ON_ERROR_STOP=1 -v reader_password="$(ORDERS_READER_PASSWORD)" -q -f - < sql/001_schema.sql`. `-T` disables the terminal, `-f -` reads the script from standard input and `< file` is **input redirection**: the shell opens the file on your machine and feeds it to the command's standard input. The double quotes around the password protect special characters. `$(ORDERS_READER_PASSWORD)` is expanded by Make before the shell runs.

```makefile
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  check config /etc/prometheus/prometheus.yml
	docker run --rm -v "$(CURDIR)/observability/collector/config.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml
```

`docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" IMAGE check config /etc/prometheus/prometheus.yml`: `--rm` removes the container afterwards, `--entrypoint promtool` replaces the image's default program, `-v host:container:ro` mounts a folder read-only, and everything after the image name is the arguments of `promtool`.

```makefile
clean: down
	rm -rf $(VENV) logs .pytest_cache .mypy_cache .ruff_cache .coverage .import_linter_cache
```

`rm -rf $(VENV) logs .pytest_cache ...` removes files and folders: `-r` recursive, `-f` no questions and no error if absent. Never run `rm -rf` with an unchecked variable.

### CI

```yaml
      - run: make up
      - run: make test-integration test-e2e
      - if: failure()
        run: docker compose logs --no-color --tail=200
      - if: always()
        run: make down
```

`docker compose logs --no-color --tail=200` prints the last 200 lines of each container's log without color codes, for when a CI run fails. See `github-actions.md`.

## Try it yourself

```bash
echo "hello" | grep -q hello; echo "exit $?"
```

Expected: `exit 0`.

```bash
echo "hello" | grep -q world; echo "exit $?"
```

Expected: `exit 1`.

```bash
docker compose exec tempo wget -q -O - http://localhost:3200/ready; echo " exit $?"
```

Expected (needs `make up`): `ready` and `exit 0`.

```bash
sh -c 'command -v python3.13 || command -v python3.12 || command -v python3'
```

Expected: the path of the first interpreter found on your machine.

```bash
make help | head -3
```

Expected: the first three lines of the sorted target list, produced by the pipeline above.

## Mistakes people make

- **Unquoted variables.** `rm -rf $DIR/` with an empty `DIR` deletes `/`. Quote: `"$DIR"`.
- **Ignoring exit codes.** A step that fails but is followed by another command hides the error. Chain with `&&` or stop on error.
- **Pipes that hide failures.** `curl ... | grep ...` returns grep's code, not curl's.
- **Assuming Bash features in `sh`.** The `CMD-SHELL` health checks run in `/bin/sh`, which on Alpine is BusyBox ash, not Bash. Use only portable features there.
- **`cd` in a script without checking it worked.**

## Check yourself

1. What does an exit code of 0 mean? *Success.*
2. What does `a | b` do? *It connects a's standard output to b's standard input.*
3. What is the difference between `'$X'` and `"$X"`? *Single quotes keep `$X` as text; double quotes replace it with the value.*
4. What does `a && b` do? *Runs b only if a exited with 0.*
5. What does `2>&1` do? *Sends standard error to wherever standard output goes.*

## Further reading

- GNU Bash reference manual: https://www.gnu.org/software/bash/manual/bash.html
- POSIX shell command language: https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html
