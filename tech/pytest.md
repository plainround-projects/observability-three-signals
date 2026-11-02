# pytest (with pytest-cov and coverage)

## What it is

pytest is a Python library and command that finds and runs **tests**. A test is a function whose name starts with `test_`. It sets up some data, calls the code being tested, and uses `assert` to state what must be true. If an `assert` is false, or any error escapes, the test is reported as failed; otherwise it passes. pytest also gives you **fixtures** (reusable setup code that tests receive by naming it as a parameter), **parametrization** (one test run with many inputs) and **markers** (labels on tests so you can run a subset).

## Why this repository uses it

The lesson says "the claims in the README are proven by tests". Unit tests prove the logic with fakes in under a second. Observability tests lint the configuration files as code. Integration and end-to-end tests query the real Tempo, Prometheus and Loki and prove that one request is one trace with its metric sample and its log lines.

## Where it appears

| Path | What it holds |
| --- | --- |
| `tests/conftest.py` | The shared in-memory OpenTelemetry fixture. |
| `tests/unit/domain`, `application`, `infrastructure`, `interfaces`, `lab` | Unit tests, one folder per layer (no containers needed). |
| `tests/observability/test_configuration_as_code.py` | Lints the compose file, the Collector, the rules and the dashboard. |
| `tests/integration/test_stack.py` | Parts of the running stack (marker `integration`). |
| `tests/e2e/test_lesson_scenario.py` | The lesson's steps against the stores (marker `e2e`). |
| `pyproject.toml` | `[tool.pytest.ini_options]` and `[tool.coverage.*]`. |
| `Makefile` | `test`, `test-integration`, `test-e2e`. |

## How it works underneath

`pytest` starts one process. It reads the configuration in `pyproject.toml`, walks the folders listed in `testpaths`, imports every file named `test_*.py` and collects every function named `test_*`. Before it runs a test it looks at the parameter names: for each name it finds a fixture function of that name (in the test file, or in `conftest.py` files in parent folders), runs it, and passes the result in. If a fixture uses `yield`, the code after the `yield` runs after the test, as cleanup.

pytest rewrites `assert` statements while it imports test files, so a failed `assert a == b` prints both values and where they differ.

`monkeypatch` is a built-in fixture that changes an attribute or environment variable for one test and puts it back afterwards. `caplog` captures log records. `tmp_path` is a fresh temporary folder. `pytest.raises(SomeError)` is a context manager that passes only if the block raises that error.

**Coverage** measures which lines of your code ran during the tests. `pytest-cov` plugs the `coverage` tool into pytest.

**Unit tests** test one piece without the outside world (fakes stand in for the database and network). **Integration tests** test pieces against real running services. **End-to-end tests** run the whole scenario the way a user would. This repository keeps the three kinds in three folders and uses markers so that `make test` runs only the first kind.

## Walkthrough of this repository's use

### The configuration in `pyproject.toml`

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
    "integration: needs the stack from docker-compose.yml (make up)",
    "e2e: runs the lesson's scenario against the stack and queries the three stores",
]

[tool.coverage.run]
source = ["three_signals"]
branch = true

[tool.coverage.report]
show_missing = true
skip_covered = false

```

`testpaths = ["tests"]` is where pytest looks. `addopts = "-ra --strict-markers"` adds options to every run: `-ra` prints a summary line for every test that did not simply pass (skipped, failed), and `--strict-markers` makes an unknown marker an error. `markers` declares the two markers with descriptions: `integration` (needs the stack from `docker-compose.yml`) and `e2e`. A test file opts in with `pytestmark = pytest.mark.integration` at module level. `make test` runs `tests/unit tests/observability`; the other two are run by their own targets.

`[tool.coverage.run]` sets `source = ["three_signals"]` (measure only this package) and `branch = true` (count both outcomes of every `if`, not only lines). `[tool.coverage.report]` prints the line numbers that were not run (`show_missing`). The `Makefile` fails if the domain and application layers fall under 95 percent.

### `tests/conftest.py`: an OpenTelemetry pipeline in memory

A `conftest.py` file holds fixtures that every test below it can use without importing them.

```python
"""Shared fixtures: an in-memory OpenTelemetry pipeline that tests can read back."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

```

The imports are from the OpenTelemetry SDK: a tracer provider, a processor that exports each span as soon as it ends (`SimpleSpanProcessor`), an exporter that keeps spans in a list (`InMemorySpanExporter`), a meter provider and a metric reader that keeps measurements in memory (`InMemoryMetricReader`).

```python
@dataclass
class InMemoryTelemetry:
    """Providers whose spans and metrics stay in memory."""

    tracer_provider: TracerProvider
    span_exporter: InMemorySpanExporter
    meter_provider: MeterProvider
    metric_reader: InMemoryMetricReader
```

A plain `@dataclass` (not frozen, since it only bundles four objects) so a test can reach `memory_telemetry.span_exporter` and the others.

```python
@pytest.fixture
def memory_telemetry() -> Iterator[InMemoryTelemetry]:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    yield InMemoryTelemetry(tracer_provider, exporter, meter_provider, reader)
    tracer_provider.shutdown()
    meter_provider.shutdown()
```

`@pytest.fixture` marks a function as a fixture. This one builds the exporter, a tracer provider that sends spans to it, a reader and a meter provider that reads into it, and `yield`s the bundle to the test. Code after `yield` is the clean-up: shut both providers down. A test asks for it by writing `memory_telemetry` as a parameter. No network, no Collector.

### `tests/unit/domain/test_orders.py`

```python
import pytest

from three_signals.domain.errors import InvalidOrderIdError
from three_signals.domain.orders import ItemCountBucket, Order, OrderHeader, OrderId, OrderItem
```

```python
@pytest.mark.parametrize(
    ("count", "bucket"),
    [
        (0, ItemCountBucket.NONE),
        (1, ItemCountBucket.ONE_TO_TWO),
        (2, ItemCountBucket.ONE_TO_TWO),
        (3, ItemCountBucket.THREE_TO_FIVE),
        (5, ItemCountBucket.THREE_TO_FIVE),
        (6, ItemCountBucket.SIX_OR_MORE),
        (400, ItemCountBucket.SIX_OR_MORE),
    ],
)
def test_item_count_falls_in_the_documented_bucket(count, bucket):
    assert ItemCountBucket.for_count(count) is bucket
```

`@pytest.mark.parametrize(("count", "bucket"), [...])` runs the same test function once per pair. The names in the first argument become the parameters of the function. The seven pairs include the edges (0, 1, 2, 3, 5, 6) and a large number (400). `is` checks that the value is the very same enum member.

```python
def test_bucket_values_are_the_strings_used_as_attribute_values():
    assert [str(bucket) for bucket in ItemCountBucket] == ["0", "1-2", "3-5", "6+"]
```

The bucket values are the strings that become span and metric attribute values: `["0", "1-2", "3-5", "6+"]`.

```python
@pytest.mark.parametrize("bad", [0, -1, True])
def test_order_id_rejects_non_positive_and_boolean_values(bad):
    with pytest.raises(InvalidOrderIdError):
        OrderId(bad)


def test_order_id_rejects_a_string():
    with pytest.raises(InvalidOrderIdError):
        OrderId("7")  # type: ignore[arg-type]
```

`with pytest.raises(InvalidOrderIdError):` passes only if the block raises that error. Zero, a negative number and `True` are rejected, and so is the string `"7"`. `# type: ignore[arg-type]` tells mypy that passing a string here is deliberate.

```python
def test_order_total_is_the_sum_of_its_lines_and_its_bucket_follows_the_item_count():
    items = (OrderItem(1, "SKU-001", 2, 500), OrderItem(2, "SKU-002", 1, 1250), OrderItem(3, "SKU-003", 3, 100))
    order = Order(OrderHeader(OrderId(3), "customer-0003"), items)
    assert order.total_cents == 2 * 500 + 1250 + 3 * 100
    assert order.item_count_bucket is ItemCountBucket.THREE_TO_FIVE


def test_orders_are_immutable():
    header = OrderHeader(OrderId(1), "customer-0001")
    with pytest.raises(AttributeError):
        header.customer_name = "other"  # type: ignore[misc]
```

The total is the sum of the lines (`2 * 500 + 1250 + 3 * 100`), the bucket for three items is `3-5`, and a frozen dataclass cannot be changed (`AttributeError`).

### `tests/unit/application/test_use_cases.py`: fakes, not mocks

```python
from contextlib import contextmanager

import pytest

from three_signals.application.assemble_order import AssembleOrder
from three_signals.application.fetch_order import FetchOrder
from three_signals.domain.errors import OrderNotFoundError
from three_signals.domain.orders import ItemCountBucket, Order, OrderHeader, OrderId, OrderItem

MISSING = object()
ITEMS = (OrderItem(1, "SKU-001", 1, 500), OrderItem(2, "SKU-002", 2, 700))
```

`MISSING = object()` is a unique marker that cannot be confused with `None`, used to tell "no header given" (use a default) from "header is `None`" (order not found).

```python
class FakeRepository:
    def __init__(self, header=MISSING, items=ITEMS):
        self.header = OrderHeader(OrderId(5), "customer-0005") if header is MISSING else header
        self.items = items
        self.delays = []

    def find_header(self, order_id):
        return self.header

    def find_items(self, order_id, planned_delay_seconds):
        self.delays.append(planned_delay_seconds)
        return self.items


class FakePlanner:
    def __init__(self, delay):
        self.delay = delay

    def next_delay_seconds(self):
        return self.delay


class FakeTelemetry:
    def __init__(self):
        self.buckets, self.entered, self.exited = [], 0, 0

    @contextmanager
    def measure(self):
        self.entered += 1
        try:
            yield self
        finally:
            self.exited += 1

    def record_bucket(self, bucket):
        self.buckets.append(bucket)

```

Three hand-written fakes, each a small class with the same methods as a port. `FakeRepository` returns canned data and records the delays it was asked for. `FakePlanner` returns a fixed delay. `FakeTelemetry` counts how many times `measure()` was entered and exited and records the buckets; its `measure` is a `@contextmanager` that uses `try/finally`, and it yields itself as the scope.

```python
def test_assembling_an_order_returns_header_and_items_and_records_the_bucket():
    telemetry = FakeTelemetry()
    order = AssembleOrder(FakeRepository(), FakePlanner(0.0), telemetry).execute(OrderId(5))
    assert order.items == ITEMS
    assert telemetry.buckets == [ItemCountBucket.ONE_TO_TWO]
    assert (telemetry.entered, telemetry.exited) == (1, 1)


def test_the_planned_delay_reaches_the_items_query():
    repository = FakeRepository()
    AssembleOrder(repository, FakePlanner(1.5), FakeTelemetry()).execute(OrderId(5))
    assert repository.delays == [1.5]


def test_a_missing_order_raises_not_found_and_still_closes_the_measurement():
    telemetry = FakeTelemetry()
    use_case = AssembleOrder(FakeRepository(header=None), FakePlanner(0.0), telemetry)
    with pytest.raises(OrderNotFoundError) as raised:
        use_case.execute(OrderId(9))
    assert raised.value.order_id == 9
    assert (telemetry.entered, telemetry.exited) == (1, 1)
    assert telemetry.buckets == []
```

Test 1 checks the result, that exactly one bucket was recorded and that the measurement was opened and closed once. Test 2 checks that the planner's `1.5` reached the repository. Test 3 checks that a missing order raises `OrderNotFoundError` carrying the id, that the measurement was still closed, and that no bucket was recorded.

```python
def test_assembling_logs_one_line_with_the_order_size(caplog):
    with caplog.at_level("INFO"):
        AssembleOrder(FakeRepository(), FakePlanner(0.0), FakeTelemetry()).execute(OrderId(5))
    record = next(entry for entry in caplog.records if entry.getMessage() == "order assembled")
    assert (record.order_id, record.item_count, record.bucket) == (5, 2, "1-2")


class FakeBack:
    def __init__(self):
        self.asked = []

    def fetch_order(self, order_id):
        self.asked.append(order_id)
        return Order(OrderHeader(order_id, "customer"), ITEMS)


def test_fetching_an_order_asks_the_back_service_once():
    back = FakeBack()
    order = FetchOrder(back).execute(OrderId(2))
    assert back.asked == [OrderId(2)]
    assert order.items == ITEMS
```

`caplog` is pytest's log-capturing fixture; `caplog.at_level("INFO")` makes sure INFO records are captured. The test finds the record "order assembled" and checks the extra fields (`record.order_id`, `item_count`, `bucket`). The last test uses `FakeBack` to check `FetchOrder` asks once.

### `tests/unit/infrastructure/`

```python
import random

from three_signals.infrastructure.fault import RandomSlowQueryPlanner


def test_a_seeded_planner_repeats_exactly():
    first = RandomSlowQueryPlanner(0.05, 1.5, random.Random(7))
    second = RandomSlowQueryPlanner(0.05, 1.5, random.Random(7))
    assert [first.next_delay_seconds() for _ in range(200)] == [second.next_delay_seconds() for _ in range(200)]


def test_about_one_request_in_twenty_is_slow():
    planner = RandomSlowQueryPlanner(0.05, 1.5, random.Random(20261027))
    delays = [planner.next_delay_seconds() for _ in range(2000)]
    assert set(delays) == {0.0, 1.5}
    assert 60 <= delays.count(1.5) <= 140


def test_probability_zero_never_delays_and_one_always_does():
    assert {RandomSlowQueryPlanner(0.0, 1.5, random.Random(1)).next_delay_seconds() for _ in range(50)} == {0.0}
    assert {RandomSlowQueryPlanner(1.0, 1.5, random.Random(1)).next_delay_seconds() for _ in range(50)} == {1.5}
```

Seeded planners repeat exactly; about one request in twenty is slow (a wide range, 60 to 140 slow out of 2000, avoids a fragile exact count); probability 0 never delays and 1 always does. A **set comprehension** `{... for _ in range(50)}` collapses the results to a set.

```python
import pytest

from three_signals.domain.errors import InvalidOrderIdError
from three_signals.domain.orders import Order, OrderHeader, OrderId, OrderItem
from three_signals.infrastructure.order_json import order_from_json, order_to_json

ORDER = Order(
    OrderHeader(OrderId(3), "customer-0003"), (OrderItem(1, "SKU-001", 2, 500), OrderItem(2, "SKU-002", 1, 90))
)


def test_an_order_survives_a_round_trip_through_json():
    assert order_from_json(order_to_json(ORDER)) == ORDER


def test_the_wire_shape_carries_the_total_and_the_bucket():
    document = order_to_json(ORDER)
    assert document["order"] == {"id": 3, "customer": "customer-0003", "total_cents": 1090, "item_count_bucket": "1-2"}
    assert len(document["items"]) == 2


def test_a_document_missing_a_field_is_rejected():
    with pytest.raises(KeyError):
        order_from_json({"order": {"id": 3, "customer": "x"}})


def test_a_document_with_a_bad_order_id_is_rejected():
    with pytest.raises(InvalidOrderIdError):
        order_from_json({"order": {"id": 0, "customer": "x"}, "items": []})
```

A round trip through JSON gives back an equal order (dataclasses compare field by field); the wire shape has the expected total and bucket; a missing field raises `KeyError`; an invalid id raises the domain error.

```python
import pytest

from three_signals.infrastructure.settings import BackSettings, FrontSettings, SettingsError

BACK_ENV = {"DATABASE_URL": "postgresql://reader@db/orders"}


def test_back_defaults_match_the_lesson():
    settings = BackSettings.from_env(BACK_ENV)
    assert (settings.slow_query_probability, settings.slow_query_seconds) == (0.05, 1.5)
    assert (settings.port, settings.bind_host, settings.service_name) == (8081, "127.0.0.1", "back")
    assert settings.slow_query_seed is None


def test_back_reads_overrides():
    settings = BackSettings.from_env(
        {
            **BACK_ENV,
            "SLOW_QUERY_PROBABILITY": "0.5",
            "SLOW_QUERY_SEED": "42",
            "BACK_PORT": "9000",
            "LOG_LEVEL": "debug",
        }
    )
    assert (settings.slow_query_probability, settings.slow_query_seed, settings.port) == (0.5, 42, 9000)
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    "overrides",
    [
        {"DATABASE_URL": ""},
        {"SLOW_QUERY_PROBABILITY": "1.5"},
        {"SLOW_QUERY_PROBABILITY": "often"},
        {"BACK_PORT": "0"},
        {"LOG_LEVEL": "LOUD"},
    ],
)
def test_back_rejects_missing_or_out_of_range_values(overrides):
    with pytest.raises(SettingsError):
        BackSettings.from_env({**BACK_ENV, **overrides})


def test_front_requires_the_back_url_and_strips_a_trailing_slash():
    assert FrontSettings.from_env({"BACK_URL": "http://back:8081/"}).back_url == "http://back:8081"
    with pytest.raises(SettingsError):
        FrontSettings.from_env({})


def test_front_validates_the_timeout():
    with pytest.raises(SettingsError):
        FrontSettings.from_env({"BACK_URL": "http://back", "BACK_TIMEOUT_SECONDS": "0"})
```

Defaults match the lesson (probability 0.05, 1.5 s, port 8081, loopback); overrides are read (`LOG_LEVEL=debug` becomes `DEBUG`); a parametrized test lists five bad environments and expects `SettingsError`; the front service requires `BACK_URL` and strips a trailing slash; its timeout must be at least 0.1. `{**BACK_ENV, **overrides}` merges two dicts so each case changes one thing.

```python
import io
import json
import logging

from three_signals.infrastructure.logging_setup import JsonFormatter, configure_logging


def _record(message="hello", **extra):
    record = logging.LogRecord("test.logger", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_every_line_is_one_json_object_with_the_standard_fields():
    payload = json.loads(JsonFormatter("back").format(_record("order assembled", order_id=5)))
    assert payload["msg"] == "order assembled"
    assert (payload["level"], payload["service"], payload["logger"]) == ("info", "back", "test.logger")
    assert payload["order_id"] == 5
    assert "trace_id" not in payload


def test_inside_a_span_the_line_carries_the_trace_and_span_ids(memory_telemetry):
    tracer = memory_telemetry.tracer_provider.get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        payload = json.loads(JsonFormatter("back").format(_record()))
        context = span.get_span_context()
    assert payload["trace_id"] == f"{context.trace_id:032x}"
    assert payload["span_id"] == f"{context.span_id:016x}"


def test_exceptions_are_included_as_text():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = _record()
        record.exc_info = sys.exc_info()
    assert "ValueError: boom" in json.loads(JsonFormatter("back").format(record))["exception"]


def test_configure_logging_replaces_handlers_and_writes_json_to_the_stream():
    stream = io.StringIO()
    configure_logging("INFO", "front", stream)
    configure_logging("INFO", "front", stream)
    logging.getLogger("x").info("once")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["service"] == "front"
```

`_record` builds a `LogRecord` by hand (`record.__dict__.update(extra)` is how `extra=` works). Tests check the standard fields and `order_id`; that inside a span the ids appear as 32 and 16 hex digits (`f"{context.trace_id:032x}"` formats an integer as zero-padded hexadecimal); that an exception is included as text; and that calling `configure_logging` twice still writes each line once, to a `StringIO` (an in-memory text stream).

```python
import pytest

from three_signals.domain.errors import OrderNotFoundError
from three_signals.domain.orders import ItemCountBucket
from three_signals.infrastructure.otel_telemetry import (
    BUCKET_ATTRIBUTE,
    HISTOGRAM_NAME,
    SPAN_NAME,
    OtelAssemblyTelemetry,
)


class Ticking:
    """A clock that advances 0.25 s per reading."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.25
        return self.now


def _adapter(memory_telemetry):
    return OtelAssemblyTelemetry(
        memory_telemetry.tracer_provider.get_tracer("t"), memory_telemetry.meter_provider.get_meter("t"), Ticking()
    )


def _histogram_points(memory_telemetry):
    data = memory_telemetry.metric_reader.get_metrics_data()
    metrics = [m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics]
    return next(m for m in metrics if m.name == HISTOGRAM_NAME).data.data_points


def test_a_successful_assembly_makes_a_span_with_the_bucket_and_one_histogram_sample(memory_telemetry):
    with _adapter(memory_telemetry).measure() as scope:
        scope.record_bucket(ItemCountBucket.THREE_TO_FIVE)
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert span.name == SPAN_NAME
    assert span.attributes[BUCKET_ATTRIBUTE] == "3-5"
    (point,) = _histogram_points(memory_telemetry)
    assert (point.count, point.sum) == (1, 0.25)
    assert point.attributes[BUCKET_ATTRIBUTE] == "3-5"


def test_the_histogram_sample_is_recorded_inside_the_span_so_it_can_carry_an_exemplar(memory_telemetry):
    with _adapter(memory_telemetry).measure() as scope:
        scope.record_bucket(ItemCountBucket.ONE_TO_TWO)
    (point,) = _histogram_points(memory_telemetry)
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert [exemplar.trace_id for exemplar in point.exemplars] == [span.context.trace_id]


def test_a_missing_order_is_not_an_error_span_and_is_counted_as_unknown_size(memory_telemetry):
    with pytest.raises(OrderNotFoundError), _adapter(memory_telemetry).measure():
        raise OrderNotFoundError(9)
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert span.attributes["order.found"] is False
    assert span.status.is_ok or span.status.status_code.name == "UNSET"
    assert _histogram_points(memory_telemetry)[0].attributes[BUCKET_ATTRIBUTE] == "unknown"


def test_an_unexpected_failure_marks_the_span_as_an_error_and_records_the_exception(memory_telemetry):
    with pytest.raises(RuntimeError), _adapter(memory_telemetry).measure():
        raise RuntimeError("database down")
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert span.status.status_code.name == "ERROR"
    assert [event.name for event in span.events] == ["exception"]
```

`Ticking` is a fake clock: each call advances 0.25 s, so durations are exact. `_adapter` builds the real adapter from the in-memory providers. `_histogram_points` reads the metrics back from the reader. The four tests: a successful assembly makes one span with the bucket attribute and one histogram sample of 0.25 s labelled `3-5`; the sample carries an **exemplar** whose trace id equals the span's (proof that the recording happens inside the span); a missing order is not an error span and is counted as bucket `unknown`; an unexpected failure sets the span status to ERROR and adds an `exception` event. `(span,) = list` unpacks a list that must have exactly one element.

```python
from contextlib import contextmanager

from three_signals.domain.orders import OrderId
from three_signals.infrastructure.postgres_orders import PostgresOrderRepository


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


class FakePool:
    def __init__(self, rows):
        self.cursor = FakeCursor(rows)

    @contextmanager
    def connection(self):
        yield FakeConnection(self.cursor)


class StepClock:
    def __init__(self, *readings):
        self.readings = list(readings)

    def __call__(self):
        return self.readings.pop(0)

```

Fakes for the database layer: a cursor that records executed statements, a connection, a pool whose `connection()` is a `@contextmanager`, and `StepClock`, a clock that returns values you list in order (`readings.pop(0)`).

```python
def test_find_header_returns_none_for_a_missing_order():
    assert PostgresOrderRepository(FakePool([]), 1.0).find_header(OrderId(5)) is None


def test_find_header_maps_the_row():
    header = PostgresOrderRepository(FakePool([(5, "customer-0005")]), 1.0).find_header(OrderId(5))
    assert header is not None
    assert (header.order_id, header.customer_name) == (OrderId(5), "customer-0005")


def test_find_items_passes_the_delay_to_the_database_and_maps_rows():
    pool = FakePool([(1, "SKU-001", 2, 500), (2, "SKU-002", 1, 90)])
    items = PostgresOrderRepository(pool, 1.0, StepClock(0.0, 0.01)).find_items(OrderId(5), 1.5)
    assert [item.sku for item in items] == ["SKU-001", "SKU-002"]
    sql, params = pool.cursor.executed[0]
    assert "pg_sleep(%(delay_seconds)s)" in sql
    assert params == {"order_id": 5, "delay_seconds": 1.5}


def test_a_fast_query_logs_two_info_lines_with_the_duration(caplog):
    with caplog.at_level("INFO"):
        PostgresOrderRepository(FakePool([]), 1.0, StepClock(0.0, 0.012)).find_items(OrderId(5), 0.0)
    started, finished = [r for r in caplog.records if "query" in r.getMessage()]
    assert (started.getMessage(), started.levelname) == ("order_items query started", "INFO")
    assert (finished.getMessage(), finished.levelname, finished.duration_ms) == (
        "order_items query finished",
        "INFO",
        12,
    )


def test_a_slow_query_logs_a_warning_named_slow(caplog):
    with caplog.at_level("INFO"):
        PostgresOrderRepository(FakePool([]), 1.0, StepClock(0.0, 1.502)).find_items(OrderId(5), 1.5)
    finished = [r for r in caplog.records if "query" in r.getMessage()][-1]
    assert (finished.getMessage(), finished.levelname, finished.duration_ms) == ("slow query finished", "WARNING", 1502)
```

Tests: no row gives `None`; a row maps to a header; the delay and order id reach the database as parameters and the SQL contains `pg_sleep(%(delay_seconds)s)`; a fast query logs two INFO lines and a duration of 12 ms (`0.012 s`); a 1.502 s query logs a WARNING named "slow query finished".

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from three_signals.domain.errors import BackendTimeoutError, BackendUnavailableError, OrderNotFoundError
from three_signals.domain.orders import OrderId
from three_signals.infrastructure.http_back_client import HttpBackClient, build_session

GOOD = {
    "order": {"id": 1, "customer": "customer-0001"},
    "items": [{"id": 1, "sku": "SKU-001", "quantity": 1, "unit_price_cents": 5}],
}


def _serve(behavior):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            status, body = behavior(len(calls))
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, calls

```

`_serve` starts a real HTTP server on a free port (port `0` means "any free port") in a background **daemon thread**, with a handler whose answer is decided by a function `behavior(call_number)`. `calls` records the paths requested. `log_message` is overridden to print nothing.

```python
@pytest.fixture
def serve():
    servers = []

    def start(behavior):
        server, calls = _serve(behavior)
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", calls

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _client(url, retries=0, timeout=2.0):
    return HttpBackClient(url, build_session(retries), timeout)

```

The `serve` fixture starts servers on demand and shuts them down after the test. `_client` builds the real client with a real session.

```python
def test_a_good_response_becomes_an_order(serve):
    url, calls = serve(lambda n: (200, GOOD))
    order = _client(url).fetch_order(OrderId(1))
    assert order.header.customer_name == "customer-0001"
    assert calls == ["/order/1"]


def test_404_becomes_order_not_found(serve):
    url, _ = serve(lambda n: (404, {"error": "order_not_found"}))
    with pytest.raises(OrderNotFoundError):
        _client(url).fetch_order(OrderId(9))


def test_a_server_error_becomes_backend_unavailable(serve):
    url, _ = serve(lambda n: (500, {}))
    with pytest.raises(BackendUnavailableError):
        _client(url).fetch_order(OrderId(1))


def test_a_503_is_retried_with_backoff_until_it_succeeds(serve):
    url, calls = serve(lambda n: (503, {}) if n < 3 else (200, GOOD))
    assert _client(url, retries=3).fetch_order(OrderId(1)).header.order_id == OrderId(1)
    assert len(calls) == 3


def test_an_unreadable_body_becomes_backend_unavailable(serve):
    url, _ = serve(lambda n: (200, b"not json"))
    with pytest.raises(BackendUnavailableError):
        _client(url).fetch_order(OrderId(1))


def test_a_slow_back_service_becomes_a_timeout(serve):
    import time

    def slow(n):
        time.sleep(0.5)
        return 200, GOOD

    url, _ = serve(slow)
    with pytest.raises(BackendTimeoutError):
        _client(url, timeout=0.1).fetch_order(OrderId(1))


def test_a_refused_connection_becomes_backend_unavailable():
    with pytest.raises(BackendUnavailableError):
        HttpBackClient("http://127.0.0.1:9", requests.Session(), 0.5).fetch_order(OrderId(1))
```

A good answer becomes an order and the path was `/order/1`; 404 becomes not-found; 500 becomes unavailable; two 503s are retried with backoff and the third attempt succeeds (3 calls); a body that is not JSON becomes unavailable; a server that sleeps 0.5 s with a 0.1 s timeout becomes `BackendTimeoutError`; a refused connection (port 9) becomes unavailable.

### `tests/unit/interfaces/`

```python
import pytest

from three_signals.application.assemble_order import AssembleOrder
from three_signals.application.fetch_order import FetchOrder
from three_signals.domain.errors import BackendTimeoutError, BackendUnavailableError, OrderNotFoundError
from three_signals.domain.orders import Order, OrderHeader, OrderId, OrderItem
from three_signals.interfaces.back_app import create_back_app
from three_signals.interfaces.front_app import create_front_app

ORDER = Order(OrderHeader(OrderId(3), "customer-0003"), (OrderItem(1, "SKU-001", 2, 500),))


class StubAssemble(AssembleOrder):
    def __init__(self, outcome):
        self._outcome = outcome

    def execute(self, order_id):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class StubFetch(FetchOrder):
    def __init__(self, outcome):
        self._outcome = outcome

    def execute(self, order_id):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _back(outcome=ORDER, check=lambda: None):
    return create_back_app(StubAssemble(outcome), check).test_client()


def _front(outcome=ORDER):
    return create_front_app(StubFetch(outcome)).test_client()

```

`StubAssemble` and `StubFetch` inherit from the real use cases but replace `execute` so it returns an order or raises the exception it was given (their constructors skip the parent's, since no collaborators are needed). `_back` and `_front` build an app and return Flask's `test_client()`.

```python
@pytest.mark.parametrize("make", [_back, _front])
def test_get_order_returns_the_order_as_json(make):
    response = make().get("/order/3")
    assert response.status_code == 200
    assert response.get_json()["order"]["customer"] == "customer-0003"
    assert response.get_json()["items"][0]["sku"] == "SKU-001"


@pytest.mark.parametrize("make", [_back, _front])
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (OrderNotFoundError(9), 404, "order_not_found"),
        (BackendTimeoutError("slow"), 504, "backend_timeout"),
        (BackendUnavailableError("down"), 502, "backend_unavailable"),
        (RuntimeError("bug"), 500, "internal_error"),
    ],
)
def test_named_errors_map_to_json_problems(make, error, status, code):
    response = make(error).get("/order/3")
    assert response.status_code == status
    assert response.get_json()["error"] == code


def test_order_id_zero_is_a_bad_request_and_a_word_is_not_found():
    client = _back()
    assert client.get("/order/0").status_code == 400
    assert client.get("/order/abc").status_code == 404
    assert client.get("/order/abc").get_json()["error"] == "not_found"


def test_back_health_reports_the_database():
    assert _back().get("/healthz").get_json() == {"status": "ok", "database": "up"}

    def broken():
        raise ConnectionError("down")

    response = _back(check=broken).get("/healthz")
    assert response.status_code == 503
    assert response.get_json()["database"] == "down"


def test_front_health_is_ok():
    assert _front().get("/healthz").get_json() == {"status": "ok"}


def test_every_response_inside_a_span_carries_the_trace_id_header(memory_telemetry):
    client = _back()
    tracer = memory_telemetry.tracer_provider.get_tracer("test")
    with tracer.start_as_current_span("request") as span:
        response = client.get("/order/3")
        expected = f"{span.get_span_context().trace_id:032x}"
    assert response.headers["X-Trace-Id"] == expected
```

Stacked `parametrize` decorators run every combination: both apps × each of the four errors, checking status and `error` code; `/order/0` is 400 and `/order/abc` is 404 with `not_found`; the back health check reports `database: up`, and 503 `down` when the check raises; the front health check is OK; and inside a span every response carries `X-Trace-Id` equal to the span's trace id.

```python
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from three_signals.interfaces.health_cli import main


def _server(status):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_exit_code_is_0_for_a_200_and_1_for_anything_else():
    healthy, broken = _server(200), _server(503)
    try:
        assert main(["--url", f"http://127.0.0.1:{healthy.server_address[1]}/healthz"]) == 0
        assert main(["--url", f"http://127.0.0.1:{broken.server_address[1]}/healthz"]) == 1
    finally:
        healthy.shutdown()
        broken.shutdown()


def test_exit_code_is_1_when_nothing_listens():
    assert main(["--url", "http://127.0.0.1:9/healthz", "--timeout", "0.5"]) == 1
```

A tiny HTTP server answers a chosen status. `main(["--url", ...])` returns 0 for a 200, 1 for 503, and 1 when nothing listens. `try/finally` shuts the servers down.

### `tests/unit/lab/`

```python
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from three_signals.lab.scenario import run_scenario


def _server(slow_ids=()):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            order_id = int(self.path.rsplit("/", 1)[1])
            seen.append(order_id)
            status = 404 if order_id in slow_ids else 200
            self.send_response(status)
            self.send_header("X-Trace-Id", f"{order_id:032x}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    class RoomyServer(ThreadingHTTPServer):
        request_queue_size = 128  # the default of 5 refuses connections when eight workers connect at once

    server = RoomyServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen
```

The throw-away server answers with an `X-Trace-Id` equal to the order id in hexadecimal (`f"{order_id:032x}"`). The inner class `RoomyServer` raises the listen queue to 128 (`request_queue_size`): the default of 5 refuses some connections when eight workers connect at once, which made the test fail on a busy machine.

```python
def test_the_scenario_sends_the_requested_count_with_seeded_ids_and_collects_trace_ids():
    server, seen = _server()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        first = run_scenario(url, 40, 1000.0, seed=3, sleep=lambda seconds: None)
        again = run_scenario(url, 40, 1000.0, seed=3, sleep=lambda seconds: None)
    finally:
        server.shutdown()
    assert len(first.records) == 40
    assert [r.order_id for r in first.records] == [r.order_id for r in again.records]
    assert all(r.trace_id == f"{r.order_id:032x}" for r in first.records)
    assert not first.failed
    assert len(seen) == 80


def test_failures_are_reported_and_a_dead_server_counts_as_status_zero():
    server, _ = _server(slow_ids=set(range(1, 1001)))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        result = run_scenario(url, 5, 1000.0, seed=1, sleep=lambda seconds: None)
    finally:
        server.shutdown()
    assert len(result.failed) == 5
    dead = run_scenario("http://127.0.0.1:9", 2, 1000.0, seed=1, timeout_seconds=0.3, sleep=lambda seconds: None)
    assert [record.status for record in dead.records] == [0, 0]
```

The scenario sends the requested number of requests (twice the same seed gives the same ids; the server saw 80), collects the trace ids and has no failures; a server that answers 404 for every id gives 5 failures; a dead address gives status 0.

```python
import base64

import pytest

from three_signals.lab.stores import SpanView, StoreError, TempoClient, _id_to_hex, is_complete

TRACE_JSON = {
    "batches": [
        {
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "back"}}]},
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "spanId": base64.b64encode(bytes.fromhex("00f067aa0ba902b7")).decode(),
                            "parentSpanId": "",
                            "name": "SELECT",
                            "startTimeUnixNano": "1000000000",
                            "endTimeUnixNano": "2502000000",
                            "attributes": [
                                {"key": "db.statement", "value": {"stringValue": "SELECT * FROM order_items"}}
                            ],
                        },
                        {
                            "spanId": "00f067aa0ba902b8",
                            "parentSpanId": "00f067aa0ba902b7",
                            "name": "child",
                            "startTimeUnixNano": "1000000000",
                            "endTimeUnixNano": "1001000000",
                            "attributes": [{"key": "n", "value": {"intValue": "3"}}],
                        },
                    ]
                }
            ],
        }
    ]
}

```

`TRACE_JSON` is a small Tempo response with a base64-encoded span id, a hex one, nested attributes with `stringValue` and `intValue` wrappers.

```python
def test_ids_are_normalized_from_base64_and_from_hex():
    assert _id_to_hex(base64.b64encode(bytes.fromhex("00f067aa0ba902b7")).decode()) == "00f067aa0ba902b7"
    assert _id_to_hex("00F067AA0BA902B7") == "00f067aa0ba902b7"


def test_a_tempo_trace_document_is_flattened_into_span_views():
    wide, child = TempoClient.parse_trace(TRACE_JSON)
    assert (wide.name, wide.service, wide.duration_ms) == ("SELECT", "back", 1502.0)
    assert wide.span_id == "00f067aa0ba902b7"
    assert wide.attributes["db.statement"] == "SELECT * FROM order_items"
    assert child.parent_span_id == wide.span_id
    assert child.attributes["n"] == "3"


def _view(span_id, parent=""):
    return SpanView("s", "back", span_id, parent, 1.0, {})


def test_a_trace_is_complete_when_it_has_one_root_and_every_parent_is_present():
    assert is_complete([_view("a"), _view("b", "a"), _view("c", "b")])
    assert not is_complete([_view("b", "a")])
    assert not is_complete([_view("a"), _view("c", "x")])
    assert not is_complete([_view("a"), _view("z")])

```

Ids are normalized from base64 and from upper-case hex; the document is flattened into two span views (duration 1502 ms) with the right parent link; `is_complete` accepts a whole tree and rejects a missing parent, an unknown parent and two roots.

```python
def test_waiting_for_a_trace_returns_once_the_late_spans_have_arrived(monkeypatch):
    class Slow(TempoClient):
        def __init__(self):
            super().__init__("http://x")
            self.answers = [None, [_view("b", "a")], [_view("a"), _view("b", "a")]]

        def trace(self, trace_id):
            return self.answers.pop(0)

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert [span.span_id for span in Slow().wait_for_trace("t", 30)] == ["a", "b"]


def test_waiting_for_a_trace_that_never_appears_raises_and_one_that_stays_partial_returns_what_it_has(monkeypatch):
    class Never(TempoClient):
        def trace(self, trace_id):
            return None

    class Partial(TempoClient):
        def trace(self, trace_id):
            return [_view("b", "a")]

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with pytest.raises(StoreError):
        Never("http://x").wait_for_trace("t", 0.05)
    assert [span.span_id for span in Partial("http://x").wait_for_trace("t", 0.05)] == ["b"]
```

Subclasses of `TempoClient` override `trace` to return scripted answers. `monkeypatch.setattr("time.sleep", lambda seconds: None)` replaces the sleep for this test only. Waiting returns once the late span arrives; a trace that never appears raises; one that stays partial returns what it has.

```python
import json

import pytest

from three_signals.lab.follow import (
    follow_slow_request,
    follow_with_patience,
    has_both_services,
    log_trace_id,
    render,
    slowest_database_span,
)
from three_signals.lab.stores import Exemplar, LogLine, SpanView, StoreError

TRACE_ID = "a" * 32


def _span(name, duration, statement="", service="back"):
    return SpanView(
        name, service, name[:4] + str(duration), "", duration, {"db.statement": statement} if statement else {}
    )


class Metrics:
    def __init__(self, points=((100.0, 0.008), (160.0, 1.4), (220.0, 0.01)), exemplars=None):
        self.points = list(points)
        self.found = (
            exemplars if exemplars is not None else [Exemplar("b" * 32, 0.01, 90.0), Exemplar(TRACE_ID, 1.502, 161.0)]
        )

    def query_range(self, expression, start, end, step_seconds):
        return self.points

    def exemplars(self, selector, start, end):
        return self.found


class Traces:
    def wait_for_trace(self, trace_id, timeout_seconds=60.0, until=None):
        return [
            _span("GET /order/{id}", 1520, service="front"),
            _span("SELECT", 2, "SELECT * FROM orders WHERE id = %s"),
            _span("SELECT", 1502, "SELECT oi.id FROM order_items AS oi"),
        ]


class Logs:
    def __init__(self, lines=None):
        self.lines = (
            lines
            if lines is not None
            else [
                LogLine(1, json.dumps({"msg": "order_items query started", "trace_id": TRACE_ID}), {}, {}),
                LogLine(
                    2, json.dumps({"msg": "slow query finished", "trace_id": TRACE_ID}), {}, {"trace_id": TRACE_ID}
                ),
            ]
        )
        self.asked = []

    def query(self, logql, start, end, limit=1000):
        self.asked.append(logql)
        return self.lines

```

Fakes for the three sources: `Metrics` returns canned p95 points and two exemplars (one fast, one slow); `Traces` returns three spans (the front's root, a fast select and a 1502 ms select); `Logs` returns two lines, one of which has the trace id as structured metadata, and remembers the query asked. `_span` builds a `SpanView`.

```python
def test_the_walk_takes_the_slowest_exemplar_and_finds_the_slow_database_span_and_its_lines():
    logs = Logs()
    result = follow_slow_request(Metrics(), Traces(), logs, now=300.0)
    assert result.peak_p95_seconds == 1.4
    assert result.exemplar.trace_id == TRACE_ID
    assert result.slow_span.duration_ms == 1502
    assert result.services_in_trace == {"front", "back"}
    assert logs.asked == [f'{{service_name="back"}} | trace_id="{TRACE_ID}"']
    assert result.reverse_trace_id == TRACE_ID


def test_without_p95_data_the_trail_goes_cold():
    with pytest.raises(StoreError, match="no data"):
        follow_slow_request(Metrics(points=()), Traces(), Logs(), now=300.0)


def test_without_a_slow_exemplar_the_trail_goes_cold():
    with pytest.raises(StoreError, match="no slow exemplar"):
        follow_slow_request(Metrics(exemplars=[Exemplar("b" * 32, 0.01, 90.0)]), Traces(), Logs(), now=300.0)


def test_without_log_lines_the_trail_goes_cold():
    with pytest.raises(StoreError, match="no log lines"):
        follow_slow_request(Metrics(), Traces(), Logs(lines=[]), now=300.0)


def test_the_slow_span_falls_back_to_any_select_when_no_statement_is_recorded():
    assert slowest_database_span([_span("SELECT", 5), _span("SELECT", 9), _span("GET", 50)]).duration_ms == 9
    with pytest.raises(StoreError):
        slowest_database_span([_span("GET", 50)])


def test_log_trace_id_prefers_structured_metadata_then_the_json_body():
    assert log_trace_id(LogLine(1, "{}", {}, {"trace_id": "x"})) == "x"
    assert log_trace_id(LogLine(1, '{"trace_id": "y"}', {}, {})) == "y"
    assert log_trace_id(LogLine(1, "plain text", {}, {})) == ""


def test_render_prints_five_numbered_clicks():
    text = render(follow_slow_request(Metrics(), Traces(), Logs(), now=300.0))
    clicks = [line for line in text.splitlines() if line[:2] in {"1.", "2.", "3.", "4.", "5."}]
    assert len(clicks) == 5
    assert "1502 ms" in text
    assert "the same trace" in text

```

The walk takes the slowest exemplar, finds the 1502 ms database span, sees both services, asked Loki exactly `{service_name="back"} | trace_id="aaaa..."` and closes the loop with the same trace id. Missing p95 data, missing slow exemplar and missing log lines each raise `StoreError` with the right message (`match=` is a regular expression). The fallback and the trace-id helper are tested, and `render` prints five numbered clicks.

```python
def test_patience_retries_while_the_telemetry_is_on_its_way_and_then_succeeds():
    class Late(Metrics):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def query_range(self, expression, start, end, step_seconds):
            self.calls += 1
            return [] if self.calls < 3 else self.points

    metrics = Late()
    naps: list[float] = []
    clock = iter(range(0, 1000, 5))
    result = follow_with_patience(
        metrics, Traces(), Logs(), 60, pause_seconds=5, clock=lambda: next(clock), sleep=naps.append
    )
    assert result.exemplar.trace_id == TRACE_ID
    assert naps == [5, 5]


def test_patience_gives_up_with_the_last_error_after_the_deadline():
    clock = iter(range(0, 1000, 20))
    with pytest.raises(StoreError, match="no data"):
        follow_with_patience(
            Metrics(points=()), Traces(), Logs(), 30, clock=lambda: next(clock), sleep=lambda seconds: None
        )


def test_an_exemplar_whose_database_span_is_fast_is_skipped_for_one_whose_span_is_slow():
    class PerTrace(Traces):
        def wait_for_trace(self, trace_id, timeout_seconds=60.0, until=None):
            width = 1502 if trace_id == TRACE_ID else 40
            return [
                _span("GET /order/{id}", 9000, service="front"),
                _span("SELECT", width, "SELECT oi FROM order_items"),
            ]

    queued = Exemplar("c" * 32, 9.0, 150.0)
    metrics = Metrics(exemplars=[Exemplar(TRACE_ID, 1.502, 161.0), queued])
    result = follow_slow_request(metrics, PerTrace(), Logs(), now=300.0)
    assert result.exemplar.trace_id == TRACE_ID
    assert result.slow_span.duration_ms == 1502


def test_when_no_exemplar_has_a_slow_database_span_the_widest_one_is_reported():
    class AllFast(Traces):
        def wait_for_trace(self, trace_id, timeout_seconds=60.0, until=None):
            width = 300 if trace_id == TRACE_ID else 40
            return [_span("SELECT", width, "SELECT oi FROM order_items")]

    metrics = Metrics(exemplars=[Exemplar("c" * 32, 9.0, 150.0), Exemplar(TRACE_ID, 1.2, 161.0)])
    assert follow_slow_request(metrics, AllFast(), Logs(), now=300.0).exemplar.trace_id == TRACE_ID


def test_a_trace_counts_as_whole_for_the_walk_only_with_spans_from_both_services():
    front = SpanView("GET", "front", "a", "", 5.0, {})
    back = SpanView("GET", "back", "b", "a", 3.0, {})
    assert has_both_services([front, back])
    assert not has_both_services([front])
    assert not has_both_services([back])
```

`follow_with_patience` retries while data is late (a subclass `Late` returns nothing for two calls, and a fake clock and sleep prove two pauses of 5 s) and gives up at the deadline with the last error. An exemplar whose database span is fast is skipped for one whose span is slow; when none is slow the widest is reported; and a trace counts as whole only with spans from both services.

### `tests/observability/test_configuration_as_code.py`: configuration is code

```python
"""Lint the files that configure the stack: the compose file, the Collector, the rules and the dashboards."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "observability"
DATASOURCE_UIDS = {"prometheus", "tempo", "loki"}


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return dict(_yaml(ROOT / "docker-compose.yml"))
```

`ROOT = Path(__file__).resolve().parents[2]` finds the repository root from the test file's location (two folders up). `_yaml` reads a YAML file with PyYAML's `safe_load`. The `compose` fixture has `scope="module"`, so the file is read once per test file.

```python
def test_every_third_party_image_is_pinned_to_a_version(compose):
    images = [service["image"] for service in compose["services"].values() if "build" not in service]
    assert len(images) >= 6
    for image in images:
        assert ":" in image, image
        assert not image.endswith(":latest"), image


def test_the_base_image_is_pinned_by_digest():
    first_line = next(line for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("FROM"))
    assert re.search(r"@sha256:[0-9a-f]{64}$", first_line)


def test_every_published_port_is_bound_to_loopback(compose):
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} on every interface"


def test_every_variable_the_compose_file_reads_is_in_env_example(compose):
    text = (ROOT / "docker-compose.yml").read_text()
    used = set(re.findall(r"\$\{([A-Z_]+)", text))
    declared = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text().splitlines() if "=" in line}
    assert used <= declared, sorted(used - declared)


def test_the_services_run_as_a_non_root_user_and_the_image_has_a_health_check(compose):
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER app$", dockerfile, re.MULTILINE)
    for name in ("front", "back"):
        assert "healthcheck" in compose["services"][name]
```

Compose rules: every third-party image has a tag that is not `latest`; the Dockerfile's `FROM` ends with a 64-hex-digit digest (`re.search(r"@sha256:[0-9a-f]{64}$", ...)`); every published port starts with `127.0.0.1:`; every `${VARIABLE}` the Compose file reads is declared in `.env.example` (`re.findall` collects the names, a set comparison `used <= declared` checks the subset); the Dockerfile has `USER app` and both services have a health check.

```python
def test_the_collector_pipelines_use_only_defined_components_with_the_memory_limiter_first_and_batch_last():
    config = _yaml(OBSERVABILITY / "collector" / "config.yaml")
    for signal, pipeline in config["service"]["pipelines"].items():
        assert all(name in config["receivers"] for name in pipeline["receivers"]), signal
        assert all(name in config["exporters"] for name in pipeline["exporters"]), signal
        assert pipeline["processors"][0] == "memory_limiter", signal
        assert pipeline["processors"][-1] == "batch", signal
        assert all(name in config["processors"] for name in pipeline["processors"]), signal


def test_the_collector_exposes_prometheus_metrics_with_exemplars():
    exporter = _yaml(OBSERVABILITY / "collector" / "config.yaml")["exporters"]["prometheus"]
    assert exporter["enable_open_metrics"] is True


def test_prometheus_stores_exemplars_and_scrapes_the_collector(compose):
    assert "--enable-feature=exemplar-storage" in compose["services"]["prometheus"]["command"]
    scrape = _yaml(OBSERVABILITY / "prometheus" / "prometheus.yml")["scrape_configs"]
    assert "otel-collector:8889" in [t for job in scrape for c in job["static_configs"] for t in c["targets"]]


def test_loki_accepts_structured_metadata_for_the_otlp_trace_ids():
    assert _yaml(OBSERVABILITY / "loki" / "loki.yaml")["limits_config"]["allow_structured_metadata"] is True
```

Collector rules: for every pipeline, each receiver, exporter and processor is defined, `memory_limiter` is first and `batch` last; the Prometheus exporter has OpenMetrics on. Prometheus: exemplar storage is enabled and the Collector is a scrape target. Loki: structured metadata is allowed.

```python
def test_every_alert_has_a_severity_a_summary_and_a_runbook():
    for path in (OBSERVABILITY / "prometheus" / "rules").glob("*.rules.yml"):
        for group in _yaml(path)["groups"]:
            for rule in group["rules"]:
                assert rule["labels"]["severity"] in {"page", "ticket"}, rule["alert"]
                assert {"summary", "runbook"} <= set(rule["annotations"]), rule["alert"]


def test_provisioned_datasources_are_the_three_the_dashboard_uses():
    sources = _yaml(OBSERVABILITY / "grafana" / "provisioning" / "datasources" / "datasources.yaml")["datasources"]
    assert {source["uid"] for source in sources} == DATASOURCE_UIDS
    prometheus = next(source for source in sources if source["uid"] == "prometheus")
    assert prometheus["jsonData"]["exemplarTraceIdDestinations"][0]["datasourceUid"] == "tempo"

```

Every alert has a severity of `page` or `ticket`, a summary and a runbook. The data sources are exactly the three uids, and the Prometheus one links exemplars to Tempo.

```python
def _panels(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    return [panel for panel in dashboard["panels"] if panel["type"] != "row"]


@pytest.fixture(scope="module")
def dashboard() -> dict[str, Any]:
    return dict(json.loads((OBSERVABILITY / "grafana" / "dashboards" / "three-signals.json").read_text()))


def test_dashboard_panel_ids_are_unique_and_every_panel_has_a_title_and_a_known_datasource(dashboard):
    panels = _panels(dashboard)
    assert len({panel["id"] for panel in panels}) == len(panels)
    for panel in panels:
        assert panel["title"]
        assert panel["datasource"]["uid"] in DATASOURCE_UIDS
        for target in panel["targets"]:
            assert target["datasource"]["uid"] in DATASOURCE_UIDS


def test_dashboard_has_the_latency_panel_with_exemplars_and_a_logs_panel(dashboard):
    panels = {panel["title"]: panel for panel in _panels(dashboard)}
    latency = next(panel for title, panel in panels.items() if "p95" in title)
    assert any(target.get("exemplar") for target in latency["targets"])
    assert "order_assembly_duration_seconds_bucket" in json.dumps(latency["targets"])
    assert any(panel["type"] == "logs" for panel in panels.values())


def test_dashboard_queries_only_use_metrics_the_stack_produces(dashboard):
    known = re.compile(r"order_assembly_duration_seconds|http_server_|http_client_|otelcol_|up\b")
    for panel in _panels(dashboard):
        for target in panel["targets"]:
            expression = target.get("expr", "")
            for metric in re.findall(r"\b([a-z][a-z0-9_]{6,})\s*[{\[(]", expression):
                if metric in {"histogram_quantile", "sum", "rate", "increase"}:
                    continue
                assert known.match(metric), f"{panel['title']}: {metric}"
```

Dashboard rules: panel ids are unique and each panel and target uses a known data source; there is a latency panel with `exemplar` on and a logs panel; and every metric named in a query belongs to a known family (a regular expression of allowed prefixes), so a typo like `order_assembly_durations_seconds` fails the test.

### `tests/integration/test_stack.py`

```python
"""Against the stack that `make up` started: every part answers and the services reach the database."""

from __future__ import annotations

import os
import subprocess

import pytest
import requests

from three_signals.lab.config import LabSettings
from three_signals.lab.stores import GrafanaClient, LokiClient, PrometheusClient, TempoClient

pytestmark = pytest.mark.integration

SETTINGS = LabSettings.from_env(os.environ)

```

`pytestmark = pytest.mark.integration` labels every test in the file. `SETTINGS` is read from the environment, the same way the lab tools do.

```python
def test_grafana_can_query_tempo_prometheus_and_loki():
    grafana = GrafanaClient(SETTINGS.grafana_url, SETTINGS.grafana_user, SETTINGS.grafana_password)
    assert grafana.datasource_uids() == ["loki", "prometheus", "tempo"]
    for uid in ("prometheus", "tempo", "loki"):
        ok, message = grafana.datasource_health(uid)
        assert ok, f"{uid}: {message}"


def test_each_backend_reports_ready():
    assert TempoClient(SETTINGS.tempo_url).ready()
    assert PrometheusClient(SETTINGS.prometheus_url).ready()
    assert LokiClient(SETTINGS.loki_url).ready()


def test_the_seed_data_is_1000_orders_and_3000_items():
    assert _psql("SELECT count(*) FROM orders").stdout.strip() == "1000"
    assert _psql("SELECT count(*) FROM order_items").stdout.strip() == "3000"
    five_item_orders = _psql("SELECT count(*) FROM (SELECT 1 FROM order_items GROUP BY order_id HAVING count(*) = 5) s")
    assert five_item_orders.stdout.strip() == "200"


def test_the_services_role_can_read_but_not_write():
    assert _psql("SELECT count(*) FROM orders", user="orders_reader").stdout.strip() == "1000"
    denied = _psql("DELETE FROM orders WHERE id = 1", user="orders_reader")
    assert denied.returncode != 0
    assert "permission denied" in denied.stderr


def test_an_order_flows_through_front_back_and_the_database():
    response = requests.get(f"{SETTINGS.front_url}/order/3", timeout=15)
    assert response.status_code == 200
    body = response.json()
    assert body["order"]["customer"] == "customer-0003"
    assert body["order"]["item_count_bucket"] == "1-2"
    assert [item["sku"] for item in body["items"]] == ["SKU-047", "SKU-004"]
    assert len(response.headers["X-Trace-Id"]) == 32


def test_bad_and_unknown_orders_get_the_documented_statuses():
    assert requests.get(f"{SETTINGS.front_url}/order/0", timeout=15).status_code == 400
    assert requests.get(f"{SETTINGS.front_url}/order/999999", timeout=15).status_code == 404
    assert requests.get(f"{SETTINGS.front_url}/order/abc", timeout=15).status_code == 404


def test_prometheus_scrapes_the_collector():
    prometheus = PrometheusClient(SETTINGS.prometheus_url)
    assert prometheus.scalar('up{job="otel-collector"}') == 1
    assert prometheus.scalar('up{job="otel-collector-internal"}') == 1
```

Against the running stack: Grafana lists exactly the three data sources and can query each; Tempo, Prometheus and Loki say they are ready; the database holds 1,000 orders and 3,000 items and 200 orders with five items; `orders_reader` can read but `DELETE` fails with "permission denied"; an order flows through front, back and database (customer, bucket `1-2`, SKUs `SKU-047` and `SKU-004`, a 32-character trace id); bad ids get 400 and 404; and Prometheus has both Collector jobs up.

### `tests/e2e/test_lesson_scenario.py`

```python
"""The lesson's steps against the real stack: send traffic, then query Tempo, Prometheus and Loki for the evidence."""

from __future__ import annotations

import json
import os
import time

import pytest

from three_signals.lab.config import LabSettings
from three_signals.lab.follow import Investigation, follow_with_patience, has_both_services, render
from three_signals.lab.scenario import RequestRecord, ScenarioResult, run_scenario
from three_signals.lab.stores import GrafanaClient, LokiClient, PrometheusClient, SpanView, TempoClient

pytestmark = pytest.mark.e2e

SETTINGS = LabSettings.from_env(os.environ)
PROMETHEUS = PrometheusClient(SETTINGS.prometheus_url)
TEMPO = TempoClient(SETTINGS.tempo_url)
LOKI = LokiClient(SETTINGS.loki_url)


@pytest.fixture(scope="module")
def scenario() -> ScenarioResult:
    result = run_scenario(SETTINGS.front_url, request_count=300, rate_per_second=5.0, seed=11)
    time.sleep(25)  # the 5 s metric export, the 5 s scrape and the 1 s batch, with margin
    return result


@pytest.fixture(scope="module")
def investigation(scenario: ScenarioResult) -> Investigation:
    result = follow_with_patience(PROMETHEUS, TEMPO, LOKI, patience_seconds=240)
    print(render(result))
    return result


def _answered(scenario: ScenarioResult) -> list[RequestRecord]:
    return [record for record in scenario.records if record.status == 200]


def _spans_of(trace_id: str) -> list[SpanView]:
    return TEMPO.wait_for_trace(trace_id, timeout_seconds=120, until=has_both_services)

```

A module-scoped fixture `scenario` sends 300 requests at 5 per second and waits 25 seconds for the 5 s metric export, the 5 s scrape and the 1 s batch. Fixture `investigation` runs the click path with up to 240 s of patience. Helpers pick answered requests and wait for whole traces with spans from both services.

```python
def test_step_1_grafana_queries_all_three_backends():
    grafana = GrafanaClient(SETTINGS.grafana_url, SETTINGS.grafana_user, SETTINGS.grafana_password)
    assert all(grafana.datasource_health(uid)[0] for uid in ("prometheus", "tempo", "loki"))


def test_the_scenario_got_a_200_for_nearly_every_request_and_met_the_planted_slow_query(scenario):
    # A request can time out on a machine that is busy with other work; more than 5% means something is wrong.
    assert len(scenario.failed) <= 0.05 * len(scenario.records)
    assert 5 <= len(scenario.slow) <= 60
    assert all(len(record.trace_id) == 32 for record in scenario.records if record.status)


def test_step_2_one_request_is_one_trace_with_spans_from_front_back_and_the_database(scenario):
    fast = min(_answered(scenario), key=lambda record: record.duration_seconds)
    spans = _spans_of(fast.trace_id)
    assert {span.service for span in spans} == {"front", "back"}
    selects = [span for span in spans if span.name.startswith("SELECT")]
    assert len(selects) == 2
    by_id = {span.span_id: span for span in spans}
    assert sum(span.parent_span_id == "" for span in spans) == 1
    assert all(span.parent_span_id in by_id for span in spans if span.parent_span_id)

```

Step 1: Grafana queries all three backends. Then: nearly every request got a 200 and 5 to 60 were slow. Step 2: the fastest request is one trace with spans from `front` and `back`, exactly two `SELECT` spans, one root, and every parent present.

```python
def test_step_3_the_manual_span_carries_the_bucket_and_the_histogram_has_a_p95(scenario):
    spans = _spans_of(_answered(scenario)[0].trace_id)
    manual = next(span for span in spans if span.name == "assemble-order-response")
    assert manual.attributes["order.item_count_bucket"] in {"1-2", "3-5"}
    assert manual.service == "back"
    p95 = PROMETHEUS.scalar(
        "histogram_quantile(0.95, sum by (le) (rate(order_assembly_duration_seconds_bucket[5m])))", default=-1.0
    )
    assert p95 > 0


def test_step_4_a_trace_finds_exactly_its_log_lines_and_a_log_line_finds_its_trace(scenario):
    trace_id = _answered(scenario)[1].trace_id
    spans = _spans_of(trace_id)
    now = time.time()
    lines = LOKI.query(f'{{service_name="back"}} | trace_id="{trace_id}"', now - 600, now + 60)
    assert len(lines) >= 2
    bodies = [json.loads(line.line) for line in lines]
    assert {body["trace_id"] for body in bodies} == {trace_id}
    span_ids = {span.span_id for span in spans}
    assert {body["span_id"] for body in bodies} <= span_ids
    assert {line.structured_metadata.get("trace_id") for line in lines} == {trace_id}
    other = LOKI.query(f'{{service_name="back"}} | trace_id!="" | trace_id!="{trace_id}"', now - 600, now + 60, limit=5)
    assert other
    assert all(trace_id not in line.line for line in other)
    assert TEMPO.wait_for_trace(json.loads(other[0].line)["trace_id"], timeout_seconds=120)

```

Step 3: the manual span carries the bucket, belongs to `back`, and the histogram has a p95 above 0. Step 4: for one trace, Loki returns at least two lines whose `trace_id` is that trace and whose `span_id`s are all spans of the trace; the structured metadata agrees; other lines have other trace ids; and one of those leads to a trace in Tempo: the click in the other direction.

```python
def test_step_5_the_walk_from_the_spike_ends_at_the_slow_order_items_span_and_its_log_lines(investigation):
    assert investigation.peak_p95_seconds >= 0.5
    assert investigation.exemplar.value >= 1.0
    assert investigation.services_in_trace == {"front", "back"}
    assert investigation.slow_span.duration_ms >= 1000
    assert "order_items" in str(investigation.slow_span.attributes["db.statement"])
    messages = [json.loads(line.line)["msg"] for line in investigation.log_lines]
    assert "order_items query started" in messages
    assert "slow query finished" in messages
    assert investigation.reverse_trace_id == investigation.exemplar.trace_id


def test_the_dashboard_is_provisioned_with_the_exemplar_panel():
    import requests

    response = requests.get(
        f"{SETTINGS.grafana_url}/api/dashboards/uid/three-signals",
        auth=(SETTINGS.grafana_user, SETTINGS.grafana_password),
        timeout=15,
    )
    assert response.status_code == 200
    titles = [panel["title"] for panel in response.json()["dashboard"]["panels"]]
    assert any("p95" in title for title in titles)
```

Step 5: the full walk ends at a database span of at least a second that mentions `order_items`, with the log messages `order_items query started` and `slow query finished`, and the reverse lookup gives the same trace id. The last test checks the dashboard is provisioned with a p95 panel.

## Try it yourself

```bash
make test
```

Expected: `104 passed` and a coverage table; the second command prints `Required test coverage of 95% reached`.

```bash
.venv/bin/python -m pytest tests/unit/domain -v
```

Expected: one line per test with `PASSED`, including the seven parametrized bucket cases.

```bash
.venv/bin/python -m pytest tests/unit -k "slow or timeout" -q
```

Expected: only the tests whose names contain those words run (`-k` selects by name).

```bash
.venv/bin/python -m pytest tests/integration tests/e2e --collect-only -q | tail -3
```

Expected: the integration and end-to-end tests are listed (collected) without being run.

```bash
.venv/bin/python -m pytest tests/unit/domain --cov=three_signals.domain --cov-report=term-missing -q
```

Expected: a table with the percentage covered for each domain file and the missing line numbers.

## Mistakes people make

- **Tests that depend on each other's order or leftovers.** Each test here builds its own data.
- **Sleeping for a fixed time** to wait for something. This repository polls with a deadline, and its unit tests replace `time.sleep` with `monkeypatch`.
- **Patching globally and forgetting to undo.** `monkeypatch` undoes itself.
- **Asserting too little.** `assert response` passes for any non-empty object. Assert the exact thing that matters.
- **Mocking everything.** A test full of mocks proves the mocks work. Fakes here are small hand-written classes with real behavior.
- **Typos in marker names.** `--strict-markers` turns an unknown marker into an error.

## Check yourself

1. What makes a function a test for pytest? *Its name starts with `test_` and it lives in a file named `test_*.py` (or in the configured paths).*
2. How does a test receive a fixture? *By naming it as a parameter.*
3. What does code after `yield` in a fixture do? *It runs after the test as cleanup.*
4. What does `pytest.raises` check? *That the block inside it raises the named exception.*
5. Why keep unit and integration tests apart? *Unit tests need no running services and run fast; integration tests need the stack.*

## Further reading

- pytest 9 documentation: https://docs.pytest.org/en/stable/
- Fixtures: https://docs.pytest.org/en/stable/how-to/fixtures.html
- Parametrize: https://docs.pytest.org/en/stable/how-to/parametrize.html
- pytest-cov: https://pytest-cov.readthedocs.io/en/latest/
- coverage.py: https://coverage.readthedocs.io/en/7.17.0/
