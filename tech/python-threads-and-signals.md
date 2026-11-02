# Python threads, futures and signals (standard library)

## What it is

Three pieces of the standard library, all about doing more than one thing at a time and about stopping cleanly.

- A **thread** is a path of execution inside a process. A process can have many; they share memory. `concurrent.futures.ThreadPoolExecutor` keeps a pool of worker threads. You hand it a function with `pool.submit(function, arguments)` and it returns a **Future**: a ticket for a result that is not ready yet. `future.result()` waits until the work is done and returns the value (or raises the error the function raised).
- A **signal** is a small message the operating system sends to a process. `SIGTERM` means "please stop"; it is what `docker stop` sends. `SIGINT` is what Ctrl-C sends, and Python turns it into a `KeyboardInterrupt` exception. `SIGKILL` cannot be caught.
- **Clocks.** `time.monotonic()` returns seconds from an arbitrary starting point and only ever moves forward, so subtracting two readings gives a reliable duration. `time.time()` returns the wall-clock Unix time (seconds since 1 January 1970 UTC) and can jump. `time.sleep(s)` pauses the calling thread.

## Why this repository uses it

The load scenario sends 10 requests a second while earlier ones may still be waiting for a slow answer, so it needs several requests in flight at once: a thread pool does that. The services must also stop cleanly when Docker asks (SIGTERM), so in-flight requests finish before the process exits and the database connections are closed.

## Where it appears

| Path | What it does |
| --- | --- |
| `src/three_signals/lab/scenario.py` | A `ThreadPoolExecutor` sends the requests; `time.monotonic` paces them. |
| `src/three_signals/infrastructure/shutdown.py` | Turns SIGTERM into `KeyboardInterrupt`. |
| `src/three_signals/interfaces/server.py` | Catches that exception and shuts the server down (see `waitress.md`). |
| `docker-compose.yml` | `stop_grace_period: 15s` gives the services time to finish (see `docker-compose.md`). |

## How it works underneath

A thread pool starts its workers lazily and keeps them alive. `submit` puts a job on an internal queue; a free worker takes it. Because of the global interpreter lock, only one thread runs Python code at any instant, but a thread that waits for network data lets the others run. The scenario is almost all waiting, so threads give real concurrency.

Python delivers signals only to the **main thread**, between bytecode instructions. A handler installed with `signal.signal(signal.SIGTERM, handler)` is a function Python calls then. Raising an exception from the handler interrupts whatever the main thread was doing, which is how `waitress`'s `server.run()` loop (running in the main thread) is stopped.

A `with ThreadPoolExecutor(...) as pool:` block waits for all submitted jobs to finish when it ends.

## Walkthrough of this repository's use

### `infrastructure/shutdown.py`

```python
"""Turn SIGTERM into the same clean exit as Ctrl-C so in-flight work finishes before the process stops."""

from __future__ import annotations

import signal
from types import FrameType


def _raise_keyboard_interrupt(signal_number: int, frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def install_termination_handler() -> None:
    """Make SIGTERM raise ``KeyboardInterrupt`` in the main thread, where the server loop handles it."""
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
```

`from types import FrameType` is only a type hint: a signal handler receives the signal number and the **frame**, the interpreter's record of the code that was running. `_raise_keyboard_interrupt` ignores both and raises `KeyboardInterrupt`. `install_termination_handler` registers it for `SIGTERM`. After this call, `docker stop` behaves like pressing Ctrl-C: the same `except KeyboardInterrupt` path runs in `server.py`, so there is only one shutdown path to test and trust.

### `lab/scenario.py`

```python
"""The load scenario: steady traffic to the front service, so some requests meet the planted slow query."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import requests

ORDER_COUNT = 1000
SLOW_REQUEST_SECONDS = 1.0
```

The imports: `random` for a seeded generator, `time`, `Callable` for the injectable clock and sleep, `Future` and `ThreadPoolExecutor`, `dataclass`, `requests`. `ORDER_COUNT = 1000` is the number of orders in the seed data; `SLOW_REQUEST_SECONDS = 1.0` is what the scenario calls "slow". Module constants are written in capitals by convention.

```python
@dataclass(frozen=True, slots=True)
class RequestRecord:
    """The outcome of one request as the client saw it."""

    order_id: int
    status: int
    trace_id: str
    duration_seconds: float
```

`RequestRecord` is one finished request as the client saw it: the order id asked for, the HTTP status (0 when the request never got an answer), the trace id the front service returned in the `X-Trace-Id` header, and how long it took.

```python
@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Everything the scenario sent and got back."""

    records: tuple[RequestRecord, ...]
    started_at: float
    finished_at: float

    @property
    def slow(self) -> tuple[RequestRecord, ...]:
        """Requests that took longer than ``SLOW_REQUEST_SECONDS``."""
        return tuple(record for record in self.records if record.duration_seconds >= SLOW_REQUEST_SECONDS)

    @property
    def failed(self) -> tuple[RequestRecord, ...]:
        """Requests that did not get a 200."""
        return tuple(record for record in self.records if record.status != 200)
```

`ScenarioResult` holds all the records and when the run started and finished. `slow` and `failed` are properties that filter the records: slow ones took at least a second, failed ones did not get status 200. `tuple(record for record in ... if ...)` is a generator expression with a filter.

```python
def _send(session: requests.Session, base_url: str, order_id: int, timeout_seconds: float) -> RequestRecord:
    started = time.monotonic()
    try:
        response = session.get(f"{base_url}/order/{order_id}", timeout=timeout_seconds)
        status, trace_id = response.status_code, response.headers.get("X-Trace-Id", "")
    except requests.RequestException:
        status, trace_id = 0, ""
    return RequestRecord(order_id, status, trace_id, time.monotonic() - started)
```

`_send` sends one request. `time.monotonic()` marks the start. `session.get(url, timeout=...)` makes the call. `response.headers.get("X-Trace-Id", "")` reads a header, or an empty string when it is missing. `status, trace_id = a, b` is **tuple unpacking**: it assigns two names in one line. If the request raises any `requests.RequestException` (refused connection, timeout), the status is recorded as 0 and no exception escapes, so one bad request does not stop the whole run. It returns a `RequestRecord` with the measured duration.

```python
def run_scenario(
    base_url: str,
    request_count: int,
    rate_per_second: float,
    seed: int,
    concurrency: int = 8,
    timeout_seconds: float = 15.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ScenarioResult:
    """Send ``request_count`` ``GET /order/<id>`` requests at ``rate_per_second``, ids drawn from a seeded generator."""
    rng = random.Random(seed)
    order_ids = [rng.randint(1, ORDER_COUNT) for _ in range(request_count)]
```

`run_scenario` takes the base URL, how many requests, the rate per second, a **seed**, the concurrency (default 8 workers), a per-request timeout (15 s) and two injectable functions: `clock` and `sleep`. A test passes a fake `sleep` that returns at once, so a test run does not take real minutes. The seed makes the list of order ids repeatable.

```python
    rng = random.Random(seed)
    order_ids = [rng.randint(1, ORDER_COUNT) for _ in range(request_count)]
    started_at = time.time()
    begin = clock()
    futures: list[Future[RequestRecord]] = []
```

`random.Random(seed)` is a private generator. `[rng.randint(1, ORDER_COUNT) for _ in range(request_count)]` draws `request_count` ids between 1 and 1000 inclusive; the underscore `_` is the conventional name for a loop variable you do not use. `started_at` is wall-clock time (to compare with timestamps in the stores); `begin` is the monotonic start.

```python
    with requests.Session() as session, ThreadPoolExecutor(max_workers=concurrency) as pool:
        for index, order_id in enumerate(order_ids):
            wait = begin + index / rate_per_second - clock()
            if wait > 0:
                sleep(wait)
            futures.append(pool.submit(_send, session, base_url, order_id, timeout_seconds))
        records = tuple(future.result() for future in futures)
    return ScenarioResult(records, started_at, time.time())
```

`with requests.Session() as session, ThreadPoolExecutor(max_workers=concurrency) as pool:` opens both resources and closes both at the end. The loop schedules request number `index` at `begin + index / rate_per_second`, an even spacing: at 10 per second, one every 0.1 s. `wait` is how long to pause until that moment; if the loop is behind schedule `wait` is negative and nothing sleeps. `pool.submit(_send, session, base_url, order_id, timeout_seconds)` starts the request in a worker and does not wait. After all are scheduled, `tuple(future.result() for future in futures)` waits for each one and collects the records in the order they were sent. The function returns a `ScenarioResult`.

The pacing is **open-loop**: the next request is sent on time even if earlier ones are still waiting for a slow answer. That is how real users behave, and it is why one slow request does not hide the others.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/lab/test_scenario.py -q
```

Expected: `2 passed`. The tests start a tiny HTTP server on your machine and run the scenario against it with a fake `sleep`.

```bash
.venv/bin/python -c "
import signal, os
from three_signals.infrastructure.shutdown import install_termination_handler
install_termination_handler()
try:
    os.kill(os.getpid(), signal.SIGTERM)
except KeyboardInterrupt:
    print('SIGTERM became KeyboardInterrupt')"
```

Expected: `SIGTERM became KeyboardInterrupt`.

```bash
time .venv/bin/python -c "
import time
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(4) as pool:
    list(pool.map(time.sleep, [1, 1, 1, 1]))"
```

Expected: about one second in total, not four, because the four sleeps overlap in four threads.

## Mistakes people make

- **Starting threads for CPU-heavy work** and expecting speed-up. The global interpreter lock prevents it. Waiting work (network, database) is what threads are good at.
- **Forgetting that `future.result()` re-raises** the worker's exception. `_send` catches the expected ones itself.
- **A signal handler that does real work.** Handlers run in the middle of other code. This one only raises.
- **Closed-loop load generators** (wait for each answer before sending the next). They slow down exactly when the system slows down and hide the problem.
- **Using `time.time()` for durations.**

## Check yourself

1. What is a Future? *A placeholder for a result a worker thread is still computing.*
2. Why does the scenario use threads? *Requests spend most of their time waiting, and several must be in flight at once.*
3. What does SIGTERM become in this repository? *A `KeyboardInterrupt`, handled where the server loop runs.*
4. Why pass `clock` and `sleep` as parameters? *So tests can fake time and run instantly.*
5. What is the difference between `time.time()` and `time.monotonic()`? *The first is the wall clock and can jump; the second only moves forward and is right for durations.*

## Further reading

- `concurrent.futures`: https://docs.python.org/3.12/library/concurrent.futures.html
- `signal`: https://docs.python.org/3.12/library/signal.html
- `time`: https://docs.python.org/3.12/library/time.html
- `random`: https://docs.python.org/3.12/library/random.html
- `threading` (background): https://docs.python.org/3.12/library/threading.html
