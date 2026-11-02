# psycopg and psycopg_pool

## What it is

psycopg (version 3) is the PostgreSQL driver for Python: the library that opens a connection to a Postgres server and lets Python send SQL and read rows. `psycopg_pool` is its companion package that provides a **connection pool**. The `[binary]` extra installs a prebuilt copy of the C client library so you need no compiler. This repository uses psycopg 3.3 and psycopg_pool 3.3.

## Why this repository uses it

The back service reads the order and its items from PostgreSQL. The lesson's slow span is a database span, so the driver matters twice: it must be instrumented (the OpenTelemetry psycopg instrumentation turns each `execute` into a span with the SQL text in `db.statement`), and the planted slow query runs inside the database through `pg_sleep`, so the span that is slow is the real SELECT span.

## Where it appears

| Path | What it does with psycopg |
| --- | --- |
| `src/three_signals/infrastructure/postgres_orders.py` | The two queries behind an order. |
| `src/three_signals/interfaces/back_main.py` | Creates the connection pool, the repository and the health check. |
| `tests/unit/infrastructure/test_postgres_orders.py` | Tests the repository with a fake pool (no database needed). |
| `pyproject.toml` | Depends on `psycopg[binary]` and `psycopg-pool`. |

## How it works underneath

`psycopg.connect(url)` opens a TCP connection, authenticates and returns a **connection**. `connection.cursor()` returns a **cursor**, the object that sends a statement (`cursor.execute(sql, params)`) and reads the result (`fetchone()` for one row, `fetchall()` for all rows as a list of tuples). Leaving a `with connection:` block commits the work if it ended normally and rolls it back if an error escaped. A **transaction** is a group of statements that all take effect together or not at all.

Parameters (`%(name)s` in the SQL text with a dict) are sent separately from the SQL, so the server never confuses data with code.

`ConnectionPool(url, min_size, max_size, timeout, kwargs=..., open=False)` keeps between `min_size` and `max_size` connections. `pool.connection()` is a context manager that lends one and returns it at the end of the `with` block. If all are in use, the caller waits up to `timeout` seconds and then gets an error. `open=False` plus `pool.open(wait=True, timeout=30)` starts the pool explicitly and waits until the first connection works. `kwargs` are passed to every `connect` (here `connect_timeout` and server `options`).

`options="-c statement_timeout=10000"` makes the server cancel any statement that runs longer than 10 000 ms.

The OpenTelemetry psycopg instrumentation wraps the cursor so each `execute` becomes a span whose attributes include `db.statement`.

## Walkthrough of this repository's use

### `infrastructure/postgres_orders.py`

```python
"""PostgreSQL adapter: the two queries behind an order."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from three_signals.domain.orders import OrderHeader, OrderId, OrderItem

_log = logging.getLogger(__name__)
```

The imports are standard library and the domain types. Note that `psycopg` itself is not imported: the repository depends on a small protocol (below), so it can be tested without a database, and the import-linter contract that forbids `psycopg` in the application layer stays simple.

```python
_HEADER_SQL = "SELECT id, customer_name FROM orders WHERE id = %(order_id)s"
```

`_HEADER_SQL` selects the id and customer name of one order. `%(order_id)s` is a **named parameter**: the value is sent separately from the text.

```python
# The CROSS JOIN with pg_sleep(0) costs nothing on a normal request; with a planned delay it makes the
# database itself spend those seconds inside this one statement, so the slow span is the SELECT span.
_ITEMS_SQL = (
    "SELECT oi.id, oi.sku, oi.quantity, oi.unit_price_cents "
    "FROM order_items AS oi CROSS JOIN pg_sleep(%(delay_seconds)s) "
    "WHERE oi.order_id = %(order_id)s ORDER BY oi.id"
)
```

`_ITEMS_SQL` joins `order_items` with `pg_sleep(%(delay_seconds)s)` using `CROSS JOIN`. `pg_sleep` is a function that returns one row after waiting. A cross join of a row set with a one-row set keeps every row, so the result is the same as without the join, but the statement now takes `delay_seconds` longer. With a delay of 0 it costs nothing; with 1.5 the database itself spends 1.5 seconds inside this one statement, so the trace shows one `SELECT` span far wider than its siblings. Adjacent Python strings in parentheses are joined into one string. `ORDER BY oi.id` makes the order of the items stable.

```python
class ConnectionSource(Protocol):
    """What the repository needs from a connection pool: a connection that closes (or returns) on exit."""

    def connection(self) -> AbstractContextManager[Any]:
        """Borrow a connection."""
        ...
```

`ConnectionSource` is a `Protocol` with one method, `connection()`, that returns something usable in a `with` block. `psycopg_pool.ConnectionPool` has exactly that, and the unit tests use a fake with the same method.

```python
class PostgresOrderRepository:
    """Reads orders through a connection pool."""

    def __init__(
        self,
        pool: ConnectionSource,
        slow_warn_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Take the pool, the duration above which a query is logged as slow, and an injectable clock."""
        self._pool = pool
        self._slow_warn_seconds = slow_warn_seconds
        self._clock = clock
```

The repository receives the pool, the number of seconds above which a query counts as slow (for logging), and a clock (default `time.monotonic`).

```python
    def find_header(self, order_id: OrderId) -> OrderHeader | None:
        """Return the order row, or ``None``."""
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(_HEADER_SQL, {"order_id": order_id.value})
            row = cursor.fetchone()
        return None if row is None else OrderHeader(OrderId(row[0]), row[1])
```

`find_header`: `with self._pool.connection() as connection, connection.cursor() as cursor:` borrows a connection and opens a cursor in one statement; both are released when the block ends. `cursor.execute(sql, {"order_id": value})` runs the statement with the named parameter. `cursor.fetchone()` returns one row as a tuple, or `None` if there is none. The row is turned into an `OrderHeader`; `OrderId(row[0])` re-checks the id.

```python
    def find_items(self, order_id: OrderId, planned_delay_seconds: float) -> tuple[OrderItem, ...]:
        """Return the order's lines, logging when the query starts and how long it took."""
        _log.info("order_items query started", extra={"order_id": order_id.value})
        started = self._clock()
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(_ITEMS_SQL, {"order_id": order_id.value, "delay_seconds": float(planned_delay_seconds)})
            rows = cursor.fetchall()
        elapsed = self._clock() - started
        slow = elapsed >= self._slow_warn_seconds
        _log.log(
            logging.WARNING if slow else logging.INFO,
            "slow query finished" if slow else "order_items query finished",
            extra={"order_id": order_id.value, "duration_ms": round(elapsed * 1000)},
        )
        return tuple(OrderItem(row[0], row[1], row[2], row[3]) for row in rows)
```

`find_items` writes an `INFO` line "order_items query started", notes the time, runs the statement with both parameters (`float(planned_delay_seconds)` makes sure the delay is a number), fetches all rows, and computes the elapsed time. If it was at least `slow_warn_seconds`, it logs a `WARNING` "slow query finished" instead of the `INFO` "order_items query finished", with `duration_ms` in the extra fields. `_log.log(level, message, extra=...)` is the generic form of `info` and `warning`. The two log lines are written inside the request's span, so each carries the trace id; those are the lines the click path finds in Loki. The method returns a tuple of `OrderItem`.

### `interfaces/back_main.py`: the composition root of the back service

```python
"""Composition root of the back service: read settings, build the adapters, serve."""

from __future__ import annotations

import os
import random

from opentelemetry import metrics, trace
from psycopg_pool import ConnectionPool

from three_signals.application.assemble_order import AssembleOrder
from three_signals.infrastructure.fault import RandomSlowQueryPlanner
from three_signals.infrastructure.logging_setup import configure_logging
from three_signals.infrastructure.otel_telemetry import OtelAssemblyTelemetry
from three_signals.infrastructure.postgres_orders import PostgresOrderRepository
from three_signals.infrastructure.settings import BackSettings
from three_signals.interfaces.back_app import create_back_app
from three_signals.interfaces.server import run_server


def main() -> None:
    """Start the back service."""
    settings = BackSettings.from_env(os.environ)
```

Imports the real `ConnectionPool` and every adapter, and builds the settings and logging first.

```python
    configure_logging(settings.log_level, settings.service_name)
    pool = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=settings.pool_max_size,
        timeout=5.0,
        kwargs={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        open=False,
    )
    pool.open(wait=True, timeout=30.0)
```

The pool: `settings.database_url` is the connection string (user, password, host, database), `min_size=1` keeps at least one connection ready, `max_size` (default 8, matching the eight server threads) caps the number, `timeout=5.0` is how long a request waits for a free connection. `kwargs` are passed to every connection: `connect_timeout: 5` limits how long opening a connection may take, and `options: "-c statement_timeout=10000"` makes the server cancel any statement that runs more than 10 seconds, so a stuck query cannot hold a connection forever. `open=False` creates the pool without opening it; `pool.open(wait=True, timeout=30.0)` then opens it and waits up to 30 seconds until the first connection works, so a database that is not ready makes the service fail at start-up with a clear error instead of at the first request.

```python
    repository = PostgresOrderRepository(pool, settings.slow_query_warn_seconds)
    planner = RandomSlowQueryPlanner(
        settings.slow_query_probability, settings.slow_query_seconds, random.Random(settings.slow_query_seed)
    )
    telemetry = OtelAssemblyTelemetry(trace.get_tracer("three_signals.back"), metrics.get_meter("three_signals.back"))
```

The repository gets the pool. The planner gets the probability, the delay and a `random.Random(settings.slow_query_seed)`: a seed of `None` is random every run; setting `SLOW_QUERY_SEED` makes the slow requests repeat exactly. The telemetry adapter gets a tracer and a meter named `three_signals.back` from the OpenTelemetry API (see `opentelemetry.md`).

```python
    def check_database() -> None:
        with pool.connection(timeout=2.0) as connection:
            connection.execute("SELECT 1")

    app = create_back_app(AssembleOrder(repository, planner, telemetry), check_database)
    run_server(app, settings.bind_host, settings.port, on_close=[pool.close])
```

`check_database` is a small function defined inside `main`: it borrows a connection with a 2-second wait and runs `SELECT 1`, the cheapest statement that proves the database answers. The Flask app gets the use case and this function; `run_server` gets `on_close=[pool.close]` so the pool is closed on shutdown.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/infrastructure/test_postgres_orders.py -q
```

Expected: `5 passed`.

```bash
docker compose exec postgres psql -U postgres -d orders -c "SELECT oi.id FROM order_items AS oi CROSS JOIN pg_sleep(1) WHERE oi.order_id = 3"
```

Expected (needs `make up`): two rows, after about one second.

```bash
docker compose logs back | grep "slow query finished" | head -2
```

Expected (after `make run`): JSON lines with `"level": "warning"` and a `duration_ms` of about 1500.

```bash
docker compose exec back python -c "import psycopg; print(psycopg.__version__)"
```

Expected: a 3.x version number such as `3.3.6`.

## Mistakes people make

- **Formatting values into the SQL string** (f-strings). Always pass parameters.
- **Not returning connections.** Use `with pool.connection()`.
- **No statement timeout.** A runaway query can hold a connection forever.
- **A pool bigger than the server allows** (`max_connections`, default 100).

## Check yourself

1. What is a cursor? *The object that sends a statement and reads its result rows.*
2. What happens when a `with connection:` block ends with an error? *The transaction is rolled back.*
3. Why use `%(name)s` parameters? *The values travel separately from the SQL, preventing injection.*
4. What does the pool's `timeout` mean? *How long a caller waits for a free connection.*
5. What does `statement_timeout` do? *The server cancels statements that run longer than the limit.*

## Further reading

- psycopg 3 documentation: https://www.psycopg.org/psycopg3/docs/
- Basic usage: https://www.psycopg.org/psycopg3/docs/basic/usage.html
- Connection pools: https://www.psycopg.org/psycopg3/docs/advanced/pool.html
- Parameter passing: https://www.psycopg.org/psycopg3/docs/basic/params.html
