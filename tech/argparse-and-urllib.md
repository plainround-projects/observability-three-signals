# argparse and urllib (standard library command-line tools)

## What it is

Two standard-library modules that the command-line tools of this repository are built on.

- `argparse` reads the **command-line arguments** a program was started with (for example `--requests 600 --rate 10`), converts them to the right types, supplies defaults, and prints a help text for `--help`.
- `urllib.request` is a small HTTP client that comes with Python. `urllib.request.urlopen(url, timeout=5)` makes a GET request and returns a response you can use in a `with` block.

A program's **exit code** is the number it returns to the operating system when it ends: `0` for success, anything else for failure. `sys.exit(main())` passes the return value of `main()` to the system, so `make` and Docker can see success or failure.

## Why this repository uses it

Four small commands run outside the services: a **health probe** the containers use as their health check, the **scenario**, the **follow** command that prints the click path, and the **stack health** command. They must work in the slim container image (where the health probe runs) with nothing extra installed, which is why the probe uses `urllib` and not `requests`.

## Where it appears

| Path | What it is |
| --- | --- |
| `src/three_signals/interfaces/health_cli.py` | `three-signals-health`: probes one URL. Docker runs it as the services' health check. |
| `src/three_signals/lab/scenario_cli.py` | `three-signals-scenario`: sends traffic and prints a summary. |
| `src/three_signals/lab/follow_cli.py` | `three-signals-follow`: prints the click path. |
| `src/three_signals/lab/health_cli.py` | `three-signals-stack-health`: checks every part of the stack. |
| `pyproject.toml` | Maps each command name to its `main` function (see `pyproject-toml.md`). |

## How it works underneath

At start-up the operating system hands the program a list of strings (`sys.argv`). `argparse.ArgumentParser()` builds a parser; `add_argument("--rate", type=float, default=10.0, help=...)` declares one option; `parse_args(argv)` reads the list, converts each value with `type`, checks that required options exist, and returns an object whose attributes are the values (`args.rate`). On an error or `--help` it prints a message and exits. `parse_args(None)` reads `sys.argv`; a test passes an explicit list instead.

`urlopen` opens a TCP connection, sends `GET <path> HTTP/1.1`, and waits up to `timeout` seconds for each step. A non-success status (such as 503) makes it raise `urllib.error.HTTPError`, a subclass of `URLError`. A refused or timed-out connection raises `URLError` or `TimeoutError`; both are subclasses of `OSError`.

## Walkthrough of this repository's use

### `interfaces/health_cli.py`: the container health probe

```python
"""``three-signals-health``: exit 0 when a service's /healthz answers 200; used by the Docker health checks."""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request


def main(argv: list[str] | None = None) -> int:
    """Probe the URL and return the process exit code."""
    parser = argparse.ArgumentParser(description="Probe a service's health endpoint.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        with urllib.request.urlopen(args.url, timeout=args.timeout) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, TimeoutError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

Imports are all standard library, so the probe adds nothing to the image. `main(argv=None) -> int` returns the exit code instead of exiting itself, which makes it easy to test. `--url` is required; `--timeout` is a float with default 5.0. `with urllib.request.urlopen(args.url, timeout=args.timeout) as response:` makes the request and closes it afterwards. `return 0 if response.status == 200 else 1` is the whole decision: only a 200 answer is healthy. Any error from the network (`URLError`, `TimeoutError`, `OSError`) means unhealthy, exit code 1, with no traceback. The last two lines run `main()` only when the file is executed as a program and pass its return value to `sys.exit`.

In `docker-compose.yml` the health check runs `three-signals-health --url http://localhost:8081/healthz --timeout 10` inside the container (see `docker-compose.md`). The back service's `/healthz` also checks the database, so "healthy" really means "can serve an order".

### `lab/scenario_cli.py`: send traffic

```python
"""``three-signals-scenario``: send steady traffic to the front service and summarize what came back."""

from __future__ import annotations

import argparse
import os
import statistics
import sys

from three_signals.lab.config import LabSettings
from three_signals.lab.scenario import run_scenario


```

Besides `argparse` and `sys`, it imports `os` (to read the environment), `statistics` (median) and the two lab modules.

```python
def main(argv: list[str] | None = None) -> int:
    """Run the scenario; exit 1 when more than the allowed share of requests failed."""
    parser = argparse.ArgumentParser(description="Send steady traffic to the front service.")
    parser.add_argument("--requests", type=int, default=600)
    parser.add_argument("--rate", type=float, default=10.0, help="requests per second")
    parser.add_argument("--seed", type=int, default=7, help="seed for the order ids requested")
    parser.add_argument("--url", default=LabSettings.from_env(os.environ).front_url)
    parser.add_argument(
        "--max-failure-share", type=float, default=0.05, help="exit 1 when more than this share of requests failed"
    )
    args = parser.parse_args(argv)
```

Five options. `--requests` (default 600), `--rate` (default 10 per second), `--seed` (default 7; the same seed asks for the same order ids), `--url` (default: the front service's address from the environment, computed once when the parser is built) and `--max-failure-share`, the fraction of failed requests above which the command exits with 1 (default 0.05, five percent). The `help=` texts appear in `--help`.

```python
    result = run_scenario(args.url, args.requests, args.rate, args.seed)
    durations = sorted(record.duration_seconds for record in result.records)
    p95 = durations[int(0.95 * (len(durations) - 1))]
    print(
        f"sent {len(result.records)} requests in {result.finished_at - result.started_at:.1f}s: "
        f"{len(result.failed)} failed, {len(result.slow)} slow (>= 1 s), "
        f"median {statistics.median(durations) * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms"
    )
    return 1 if len(result.failed) > args.max_failure_share * len(result.records) else 0


if __name__ == "__main__":
    sys.exit(main())
```

It runs the scenario, sorts the durations, and takes the **p95**: the value below which 95 percent of the sorted durations lie (`durations[int(0.95 * (len(durations) - 1))]`). It prints one summary line: how many requests, how long it took, how many failed and how many were slow, the median and the p95. `statistics.median(...) * 1000:.0f` converts seconds to milliseconds with no decimals. The command returns 1 when more than the allowed share failed; otherwise 0. This is what lets `make run` stop when the stack is broken instead of printing a nice summary of failures.

### `lab/follow_cli.py`: print the click path

```python
"""``three-signals-follow``: walk from the latency spike to the slow span to its log lines and print each click."""

from __future__ import annotations

import argparse
import os
import sys

from three_signals.lab.config import LabSettings
from three_signals.lab.follow import follow_with_patience, render
from three_signals.lab.stores import LokiClient, PrometheusClient, StoreError, TempoClient


def main(argv: list[str] | None = None) -> int:
    """Print the investigation; exit 1 when a click leads nowhere."""
    parser = argparse.ArgumentParser(description="Follow a slow request from the p95 panel to its logs.")
    parser.add_argument("--wait", type=float, default=180.0, help="seconds to keep retrying while telemetry arrives")
    args = parser.parse_args(argv)
    settings = LabSettings.from_env(os.environ)
    try:
        investigation = follow_with_patience(
            PrometheusClient(settings.prometheus_url),
            TempoClient(settings.tempo_url),
            LokiClient(settings.loki_url),
            patience_seconds=args.wait,
        )
    except StoreError as error:
        print(f"the trail went cold: {error}", file=sys.stderr)
        return 1
    print(render(investigation))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`--wait` is how many seconds to keep retrying (default 180) while the telemetry reaches Prometheus, Tempo and Loki. The command builds the three store clients from the lab settings, calls `follow_with_patience`, and prints the rendered click path. A `StoreError` (a click that leads nowhere) is printed to `stderr` as "the trail went cold" and the command returns 1. `print(..., file=sys.stderr)` writes to the error stream, which keeps error text out of redirected output.

### `lab/health_cli.py`: check everything

```python
"""``three-signals-stack-health``: every backend answers, and Grafana can query each of its three data sources."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

from three_signals.lab.config import LabSettings
from three_signals.lab.stores import GrafanaClient, LokiClient, PrometheusClient, StoreError, TempoClient

DATASOURCES = ("prometheus", "tempo", "loki")


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> int:
    """Print one line per check and exit 1 when any fails."""
    settings = LabSettings.from_env(os.environ)
    collector_port = os.environ.get("COLLECTOR_HEALTH_HOST_PORT", "23133")
    checks: list[tuple[str, bool]] = [
        ("front /healthz", _http_ok(f"{settings.front_url}/healthz")),
        ("collector health_check", _http_ok(f"http://127.0.0.1:{collector_port}/")),
        ("tempo /ready", TempoClient(settings.tempo_url).ready()),
        ("prometheus /-/healthy", PrometheusClient(settings.prometheus_url).ready()),
        ("loki /ready", LokiClient(settings.loki_url).ready()),
    ]
    grafana = GrafanaClient(settings.grafana_url, settings.grafana_user, settings.grafana_password)
    for uid in DATASOURCES:
        try:
            ok, message = grafana.datasource_health(uid)
        except StoreError as error:
            ok, message = False, str(error)
        checks.append((f"grafana queries {uid}: {message}", ok))
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
```

`DATASOURCES` names the three Grafana data sources. `_http_ok` is the same idea as the container probe but returns a bool; `bool(response.status == 200)` makes sure the return type is exactly `bool`. `main` collects a list of `(name, ok)` pairs: the front service, the Collector's health extension on its published port, Tempo, Prometheus and Loki; then asks Grafana to query each data source (see `grafana.md`). If the Grafana call raises `StoreError`, the check is recorded as failed with the error text as message. Finally every pair is printed as `ok   name` or `FAIL name`, and the exit code is 0 only if `all(ok for _, ok in checks)`. `make up` ends by running this command.

## Try it yourself

```bash
.venv/bin/three-signals-scenario --help
```

Expected: a usage line and the five options with their help texts.

```bash
.venv/bin/three-signals-health --url http://127.0.0.1:9/healthz --timeout 1; echo "exit code $?"
```

Expected: `exit code 1` (nothing listens on port 9).

```bash
.venv/bin/three-signals-health --url https://example.com --timeout 10; echo "exit code $?"
```

Expected (needs internet): `exit code 0`.

```bash
.venv/bin/python -m pytest tests/unit/interfaces/test_health_cli.py -q
```

Expected: `2 passed`.

```bash
make health
```

Expected after `make up`: eight lines starting with `ok` (see `README.md`).

## Mistakes people make

- **Calling `sys.exit` deep inside a function.** Hard to test. Here only the last two lines exit.
- **Ignoring the exit code** of a health probe: Docker looks at it.
- **No timeout on `urlopen`.** It waits forever. The probe sets one.
- **Treating any non-error HTTP status as healthy.** A `3xx` or `204` is not `200`; the probe asks for exactly 200.
- **Using a heavy library for a tiny probe**, enlarging the attack surface of the image.

## Check yourself

1. What does `parse_args(argv)` return? *An object whose attributes are the option values, converted to their declared types.*
2. What does an exit code of 1 from the health probe tell Docker? *The container is unhealthy.*
3. Why is `main` returning a number instead of calling `sys.exit`? *Tests can call it and check the number.*
4. What is a p95? *The value below which 95 percent of the measurements lie.*
5. Which exceptions can `urlopen` raise for a refused connection? *`URLError` (and `TimeoutError`/`OSError` for timeouts), all handled here.*

## Further reading

- `argparse`: https://docs.python.org/3.12/library/argparse.html
- `urllib.request`: https://docs.python.org/3.12/library/urllib.request.html
- `statistics`: https://docs.python.org/3.12/library/statistics.html
- `sys.exit`: https://docs.python.org/3.12/library/sys.html#sys.exit
