# OpenTelemetry (Python API, SDK, exporter and instrumentations)

## What it is

OpenTelemetry (OTel) is an open standard, plus libraries for many languages, for producing **telemetry**: the three kinds of evidence a running program can give about itself.

- A **trace** records the path of one request through the system. It is a tree of **spans**. A span is one timed step with a name, a start time, an end time, a parent span, and **attributes** (key/value notes such as `db.statement`). All spans of one request share one **trace id**.
- A **metric** is a number measured over time: a counter that only goes up, a gauge that moves up and down, or a **histogram** that counts how many measurements fell into each range ("bucket") of values.
- A **log** is a line of text (here JSON) with a time and a level. When written inside a span, a log line can carry that span's trace id and span id, which is what links the three signals.

OpenTelemetry does not store or display anything. It defines how to *produce* and *send* the data (the **OTLP** protocol, the OpenTelemetry Protocol, over HTTP or gRPC) so that any backend can receive it.

## Why this repository uses it

The whole lesson is: instrument two services once, send the three signals through one Collector, and follow a slow request from a metric to a trace to logs. Most of that is **zero-code**: the command `opentelemetry-instrument` and the installed instrumentation packages create spans for the incoming HTTP request, the outgoing HTTP call and every SQL statement, and propagate the trace between the services. The only OpenTelemetry code in the repository is the part the automatic tools cannot know: one **manual span** that wraps "assemble the order" and carries the order-size bucket, and one **histogram** of how long that took.

## Where it appears

| Path | What it does with OpenTelemetry |
| --- | --- |
| `src/three_signals/infrastructure/otel_telemetry.py` | The manual span and the duration histogram (the only place that creates them). |
| `src/three_signals/infrastructure/logging_setup.py` | Reads the current span to put `trace_id` and `span_id` in each log line; attaches the SDK's log handler (see `python-logging.md`). |
| `src/three_signals/interfaces/http_common.py` | Copies the current trace id into the `X-Trace-Id` response header (see `flask.md`). |
| `src/three_signals/interfaces/back_main.py` | Asks the API for the tracer and meter named `three_signals.back`. |
| `Dockerfile` | Starts the services through `opentelemetry-instrument`. |
| `docker-compose.yml` | The `OTEL_*` environment variables that configure the SDK for each service (explained in `docker-compose.md`). |
| `pyproject.toml` | Lists the distro, the OTLP/HTTP exporter and the Flask, requests and psycopg instrumentations. |
| `tests/conftest.py`, `tests/unit/infrastructure/test_otel_telemetry.py` | An in-memory pipeline that tests read back (see `pytest.md`). |

## How it works underneath

The Python packages are split by role:

- `opentelemetry-api` holds the names your code calls (`trace.get_tracer`, `metrics.get_meter`). With no SDK configured the calls do nothing, so libraries can use the API safely.
- `opentelemetry-sdk` is the real implementation: it makes spans, aggregates metrics, batches them and hands them to **exporters**.
- `opentelemetry-exporter-otlp-proto-http` is the exporter that sends OTLP over HTTP (protobuf-encoded) to a Collector.
- `opentelemetry-instrumentation-*` packages wrap a library (Flask, requests, psycopg) so that every request, outgoing call or SQL statement creates a span automatically. This is **auto-instrumentation**.
- `opentelemetry-distro` and `opentelemetry-instrument` are the glue: the command `opentelemetry-instrument <program>` sets up the SDK from environment variables (`OTEL_*`), switches on all installed instrumentations, and then runs the program. The program's own code needs no setup lines.

**Context propagation.** The current span is stored in a *context* (a `contextvars` variable) so code deeper in the call chain automatically becomes a child. When `requests` calls another service, the requests instrumentation adds the `traceparent` header, and the Flask instrumentation on the other side reads it and continues the same trace.

**Resource.** A set of attributes that describe the process producing the data (`service.name=back`). The backends use `service.name` to tell the services apart.

**Exemplars.** When a measurement is recorded inside a span, the SDK can attach the trace id and span id of that span to the histogram sample. Prometheus then stores "this sample (1.5 s) came from trace X". A dashboard turns it into a dot you can click to open the trace.

**Logs.** Python's standard `logging` module is bridged: a `LoggingHandler` converts each record into an OTLP log record, including the active trace id and span id.

Data leaves the process in batches on a timer, so a request is not visible in the backends for a few seconds.

## Walkthrough of this repository's use

### `infrastructure/otel_telemetry.py`

```python
"""OpenTelemetry adapter for the order-assembly span and the duration histogram."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from opentelemetry.metrics import Meter
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from three_signals.application.ports import AssemblyScope
from three_signals.domain.errors import OrderNotFoundError
from three_signals.domain.orders import ItemCountBucket
```

The imports are from the **API** only (`opentelemetry.metrics`, `opentelemetry.trace`), never the SDK, so this module works with whatever SDK the process was started with, and with none (then it does nothing). `Span`, `Status`, `StatusCode` and `Tracer` are the API types used below. `contextmanager` turns a generator function into a `with`-able object.

```python
SPAN_NAME = "assemble-order-response"
BUCKET_ATTRIBUTE = "order.item_count_bucket"
HISTOGRAM_NAME = "order_assembly_duration_seconds"
# Seconds, spread so a 1.5 s outlier lands in a bucket of its own; the SDK default is tuned for milliseconds.
HISTOGRAM_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
```

The names that other code (and tests) agree on: the span is called `assemble-order-response`; the attribute that carries the size bucket is `order.item_count_bucket`; the histogram is `order_assembly_duration_seconds`. The Prometheus name in dashboards is `order_assembly_duration_seconds_bucket`, `_count` and `_sum` (see `promql.md`).

`HISTOGRAM_BUCKETS` are the upper edges, in seconds, of the histogram's buckets: 5 ms, 10 ms, 25 ms, 50 ms, 100 ms, 250 ms, 500 ms, 1 s, 2.5 s, 5 s and 10 s. The SDK's default buckets are tuned for milliseconds (0, 5, 10, 25, ... 10 000), so seconds-sized values would all land in the first buckets. Here a 1.5 s outlier falls in the bucket "1 s to 2.5 s" by itself, which is what makes the p95 and the "share slower than 1 s" meaningful.

```python
class _Scope:
    def __init__(self, span: Span) -> None:
        self._span = span
        self.bucket: ItemCountBucket | None = None

    def record_bucket(self, bucket: ItemCountBucket) -> None:
        self.bucket = bucket
        self._span.set_attribute(BUCKET_ATTRIBUTE, str(bucket))
```

`_Scope` is the object the use case receives inside the `with`. It remembers the bucket and, when `record_bucket` is called, sets it as an **attribute** of the span (`span.set_attribute`). Attributes are the notes you attach to a span: they are searchable in Tempo (`{ span.order.item_count_bucket = "1-2" }`).

```python
class OtelAssemblyTelemetry:
    """Wraps one assembly in a child span of the request and records its duration, with the size bucket."""

    def __init__(self, tracer: Tracer, meter: Meter, clock: Callable[[], float] = time.monotonic) -> None:
        """Take the tracer, the meter and an injectable monotonic clock."""
        self._tracer = tracer
        self._clock = clock
        self._duration = meter.create_histogram(
            HISTOGRAM_NAME,
            unit="s",
            description="Time to read an order and its items and assemble the response.",
            explicit_bucket_boundaries_advisory=HISTOGRAM_BUCKETS,
        )
```

The adapter takes a tracer, a meter and a clock. `meter.create_histogram(name, unit="s", description=..., explicit_bucket_boundaries_advisory=HISTOGRAM_BUCKETS)` makes the histogram instrument. The `unit` is documentation; the "advisory" argument is a hint, defined in the OpenTelemetry specification, that tells the SDK which bucket boundaries to use.

```python
    @contextmanager
    def measure(self) -> Iterator[AssemblyScope]:
        """Open the span, yield a scope, then record the duration inside the span so the sample links to it."""
        with self._tracer.start_as_current_span(
            SPAN_NAME, record_exception=False, set_status_on_exception=False
        ) as span:
```

`@contextmanager` marks `measure` as a generator that yields once: the code before `yield` runs on entering the `with`, the code after on leaving. `self._tracer.start_as_current_span(SPAN_NAME, ...)` starts a span and makes it the current one, so everything inside (the SQL spans created by the psycopg instrumentation, and the log lines) becomes its child. `record_exception=False` and `set_status_on_exception=False` turn off the automatic handling, because the code decides what counts as an error.

```python
            scope = _Scope(span)
            started = self._clock()
            try:
                yield scope
            except OrderNotFoundError:
                span.set_attribute("order.found", False)
                raise
            except Exception as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                raise
```

`yield scope` is where the use case's block runs. If it raises `OrderNotFoundError`, that is a normal outcome (a customer asked for an order that does not exist), so the span is **not** marked as an error: it only gets `order.found = False`, and the exception is re-raised with a bare `raise`. Any other exception is an unexpected failure: `span.record_exception(error)` stores the error as a span event and `span.set_status(Status(StatusCode.ERROR, ...))` marks the span as failed, then it re-raises.

```python
            finally:
                bucket = scope.bucket if scope.bucket is not None else "unknown"
                self._duration.record(self._clock() - started, attributes={BUCKET_ATTRIBUTE: str(bucket)})
```

The `finally` block runs in every case. It uses the bucket the scope recorded, or `"unknown"` when none was recorded (the order was not found). `self._duration.record(elapsed, attributes={...})` adds one measurement to the histogram. This line is **inside** the `with` of the span (the `finally` belongs to the `try` inside it), so the span is still active at that moment, and the SDK can attach the span's trace id to the sample as an **exemplar**. If this line moved outside the span, the dashboard would show no clickable dots.

### How the rest is configured: `opentelemetry-instrument` and the `OTEL_*` variables

The Dockerfile's last line and the Compose `command:` start each service as `opentelemetry-instrument three-signals-back` (or `-front`). That command reads these environment variables, set in `docker-compose.yml`:

```yaml
x-service-environment: &service-environment
  BIND_HOST: 0.0.0.0
  OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318
  OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf
  OTEL_TRACES_EXPORTER: otlp
  OTEL_METRICS_EXPORTER: otlp
  OTEL_LOGS_EXPORTER: otlp
  OTEL_METRIC_EXPORT_INTERVAL: "5000"
  OTEL_RESOURCE_ATTRIBUTES: service.version=0.1.0,deployment.environment=local
  OTEL_PYTHON_FLASK_EXCLUDED_URLS: healthz
  OTEL_PYTHON_REQUESTS_EXCLUDED_URLS: healthz
  LOG_LEVEL: INFO
```

- `OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318` is where the SDK sends data: the Collector, by its Compose service name, on its OTLP/HTTP port.
- `OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf` picks OTLP over HTTP with Protocol Buffers encoding (the exporter package `opentelemetry-exporter-otlp-proto-http`).
- `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER` set to `otlp` switch each signal on. Without `OTEL_LOGS_EXPORTER` the log handler in `logging_setup.py` would not be attached.
- `OTEL_METRIC_EXPORT_INTERVAL: "5000"` sends metrics every 5000 ms (the default is 60 000), so the lab reacts in seconds.
- `OTEL_RESOURCE_ATTRIBUTES` adds `service.version` and `deployment.environment` to every signal.
- `OTEL_PYTHON_FLASK_EXCLUDED_URLS: healthz` and `OTEL_PYTHON_REQUESTS_EXCLUDED_URLS: healthz` are comma-separated regular expressions of URLs that must not be traced. The health checks run every 10 seconds; without this, the traces would be full of them.
- `OTEL_SERVICE_NAME` (set per service in Compose) becomes the resource attribute `service.name`.

`LOG_LEVEL` is this project's own variable, not an OpenTelemetry one.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/infrastructure/test_otel_telemetry.py -q
```

Expected: `4 passed`.

```bash
curl -s -D - -o /dev/null http://127.0.0.1:28081/order/3 | grep -i x-trace-id
```

Expected (needs `make up`): `X-Trace-Id: ` followed by 32 hex characters. Copy it.

```bash
curl -s "http://127.0.0.1:23200/api/traces/<paste the id>" | head -c 300
```

Expected: the start of a JSON document with `batches` (give it ten seconds after the request).

```bash
docker compose exec back env | grep ^OTEL_ | sort
```

Expected: the `OTEL_*` variables listed above, with `OTEL_SERVICE_NAME=back`.

```bash
curl -s http://127.0.0.1:23133/
```

Expected: the Collector's health extension answers with a small JSON document with `"status":"Server available"`.

## Mistakes people make

- **Using unbounded values as metric labels** (user ids, order ids). Every distinct value is a new time series. This repository puts only a four-value bucket on the metric and the order id on spans and logs, where unlimited values are fine.
- **Recording a measurement outside the span** and then wondering why the exemplar is missing. Exemplars need a span active at the moment of recording.
- **Forgetting that the SDK buffers.** A short-lived process must flush on exit.
- **Mixing up the API and the SDK.** Application code should only import the API.
- **Missing propagation.** If a client library is not instrumented, the trace breaks into two.

## Check yourself

1. What are the three signals? *Traces, metrics and logs.*
2. What is a span? *One timed step in a request, with a parent, attributes and a trace id shared with the rest of the request.*
3. What does `opentelemetry-instrument` do? *It configures the SDK from `OTEL_*` variables, enables the installed instrumentations and runs your program.*
4. How does the trace continue from the front service to the back service? *The `requests` instrumentation sends a `traceparent` header and the Flask instrumentation reads it.*
5. What is an exemplar? *A trace id attached to one histogram sample, so you can open the trace that produced it.*

## Further reading

- OpenTelemetry concepts: https://opentelemetry.io/docs/concepts/
- Python instrumentation (SDK 1.45, instrumentations 0.66b1): https://opentelemetry.io/docs/languages/python/
- Zero-code Python: https://opentelemetry.io/docs/zero-code/python/
- OTLP specification: https://opentelemetry.io/docs/specs/otlp/
- Environment variables: https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/
