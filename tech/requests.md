# requests and urllib3

## What it is

`requests` is the most widely used Python library for making HTTP calls. `requests.get(url)` sends a GET request and returns a `Response` with `.status_code`, `.headers`, `.text` and `.json()`. A **Session** keeps connections open between calls (faster) and lets you set options once. `urllib3` is the lower-level library that `requests` uses underneath, and `urllib.request` from the standard library is a simpler client that needs no installation. This repository uses requests 2.34 and urllib3 2.8.

## Why this repository uses it

Three places make HTTP calls: the **front service** calls the back service (and its call is what makes one request a trace that spans two services); the **load scenario** calls the front service; the **store clients** in the lab package call Prometheus, Tempo, Loki and Grafana. The OpenTelemetry `requests` instrumentation automatically creates a client span for each call and adds the `traceparent` header.

## Where it appears

| Path | What it does with requests |
| --- | --- |
| `src/three_signals/infrastructure/http_back_client.py` | The front service's client of the back service, with timeouts and retries. |
| `src/three_signals/interfaces/front_main.py` | Builds the session and the client. |
| `src/three_signals/lab/stores.py` | Clients for Prometheus, Tempo, Loki and Grafana. |
| `src/three_signals/lab/scenario.py` | Sends the load (see `python-threads-and-signals.md`). |
| `tests/unit/infrastructure/test_http_back_client.py` | Tests the client against a tiny local server. |

## How it works underneath

A Session owns a **connection pool** per host. The first call opens a TCP connection; later calls reuse it (HTTP keep-alive). An **adapter** (`HTTPAdapter`) is mounted for a URL prefix such as `http://`, and decides how connections are made and how failures are retried. A `Retry` object from urllib3 describes the policy: `total` attempts, which status codes to retry (`status_forcelist`), which HTTP methods are safe to repeat (`allowed_methods`), `backoff_factor` (the pause grows: roughly `backoff_factor * 2^(attempt-1)` seconds), and `read=False` (do not retry when the server already received the request and the wait for its answer ran out).

`timeout=5.0` is applied to the connect step and to each wait for data. It is **not** a limit for the whole request. Without a timeout, `requests` waits forever. Failures raise exceptions that derive from `requests.RequestException`; `requests.Timeout` is the timeout one.

`response.json()` parses the body as JSON and raises `ValueError` (a `JSONDecodeError`) when the body is not JSON.

The OpenTelemetry requests instrumentation patches `Session.send`, so each outgoing call becomes a client span and the `traceparent` header is added.

## Walkthrough of this repository's use

### `infrastructure/http_back_client.py`

```python
"""HTTP adapter the front service uses to call the back service, with timeouts and retries."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from three_signals.domain.errors import BackendTimeoutError, BackendUnavailableError, OrderNotFoundError
from three_signals.domain.orders import Order, OrderId
from three_signals.infrastructure.order_json import order_from_json
```

`HTTPAdapter` and `Retry` are the two pieces used to add retry behavior to a session. The module imports the named domain errors it translates failures into, and `order_from_json` to parse a good answer.

```python
def build_session(retries: int) -> requests.Session:
    """Create a session that retries idempotent GETs on connection errors and 502, 503, 504 with backoff.

    A read timeout is not retried: the back service is already slow, and a retry would double the wait.
    """
```

`build_session(retries)` creates the session. The docstring states the policy: retry safe requests on connection errors and 502, 503, 504 with backoff, but **not** on a read timeout.

```python
    retry = Retry(
        total=retries,
        read=False,
        backoff_factor=0.2,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
```

The `Retry` object. `total=retries` is the maximum number of retries across all kinds of failure. `read=False` means: if the request was sent and the wait for the answer timed out, do not retry. The back service is slow because of the database; a second request would add load and double the wait. `backoff_factor=0.2` pauses between attempts, growing each time. `status_forcelist=(502, 503, 504)` retries those answers. `allowed_methods=frozenset({"GET"})` limits retries to the one method that is safe to repeat. `raise_on_status=False` means that after the retries are used up, the last response is returned and `fetch_order` decides what it means (503 becomes `BackendUnavailableError`), instead of urllib3 raising its own error.

```python
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session
```

`requests.Session()` creates the session; `session.mount("http://", HTTPAdapter(max_retries=retry))` makes every `http://` URL use an adapter with that policy. The same for `https://`.

```python
class HttpBackClient:
    """Calls ``GET /order/<id>`` on the back service."""

    def __init__(self, base_url: str, session: requests.Session, timeout_seconds: float) -> None:
        """Take the back service's base URL, a (retrying) session and the per-request timeout."""
        self._base_url = base_url
        self._session = session
        self._timeout_seconds = timeout_seconds

```

`HttpBackClient` holds the base URL (`http://back:8081` in Compose), the session and the timeout.

```python
    def fetch_order(self, order_id: OrderId) -> Order:
        """Return the order, mapping 404, timeouts and server errors to named domain errors."""
        try:
            response = self._session.get(f"{self._base_url}/order/{order_id.value}", timeout=self._timeout_seconds)
        except requests.Timeout as error:
            raise BackendTimeoutError(f"back did not answer within {self._timeout_seconds}s") from error
        except requests.RequestException as error:
            raise BackendUnavailableError(f"back is unreachable: {error.__class__.__name__}") from error
```

`fetch_order` makes the call with `timeout=self._timeout_seconds`. The two `except` branches are ordered from specific to general: `requests.Timeout` becomes `BackendTimeoutError`; any other `requests.RequestException` (refused connection, DNS failure) becomes `BackendUnavailableError`. `raise ... from error` keeps the original as the cause. `error.__class__.__name__` puts the error's class name in the message without leaking details.

```python
        if response.status_code == 404:
            raise OrderNotFoundError(order_id.value)
        if response.status_code >= 500:
            raise BackendUnavailableError(f"back answered {response.status_code}")
        try:
            return order_from_json(response.json())
        except (ValueError, KeyError, TypeError) as error:
            raise BackendUnavailableError("back sent a response this service cannot read") from error
```

Status handling: 404 becomes `OrderNotFoundError`; 500 and above becomes `BackendUnavailableError`. For anything else it parses the JSON and builds the `Order`. A body that is not JSON raises `ValueError`; a missing key raises `KeyError`; a wrong shape raises `TypeError`. All three become `BackendUnavailableError("back sent a response this service cannot read")`. The front service therefore only ever raises the named errors, and `http_common.py` maps them to 404, 502 or 504.

### `interfaces/front_main.py`: the composition root of the front service

A **composition root** is the one place that creates the real objects and connects them.

```python
"""Composition root of the front service: read settings, build the adapters, serve."""

from __future__ import annotations

import os

from three_signals.application.fetch_order import FetchOrder
from three_signals.infrastructure.http_back_client import HttpBackClient, build_session
from three_signals.infrastructure.logging_setup import configure_logging
from three_signals.infrastructure.settings import FrontSettings
from three_signals.interfaces.front_app import create_front_app
from three_signals.interfaces.server import run_server


def main() -> None:
    """Start the front service."""
    settings = FrontSettings.from_env(os.environ)
    configure_logging(settings.log_level, settings.service_name)
    session = build_session(settings.back_retries)
    client = HttpBackClient(settings.back_url, session, settings.back_timeout_seconds)
    app = create_front_app(FetchOrder(client))
    run_server(app, settings.bind_host, settings.port, on_close=[session.close])
```

It reads `FrontSettings` from `os.environ`, configures logging, builds the session with the configured retries, builds the client (base URL, session, timeout), wraps it in the use case, creates the Flask app, and hands everything to `run_server`. `on_close=[session.close]` closes the HTTP session when the server stops. When started with `opentelemetry-instrument three-signals-front`, the SDK is configured before `main()` runs, and the Flask and requests instrumentations are already active; nothing here mentions OpenTelemetry.

### `lab/stores.py`: clients for the four stores

These are the clients the lab tools use to ask the stores questions. They return plain typed values.

```python
"""Small HTTP clients for Prometheus, Tempo, Loki and Grafana, returning plain typed values."""

from __future__ import annotations

import base64
import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import requests


class StoreError(RuntimeError):
    """A backend answered with an error or something this client cannot read."""
```

`base64` and `contextlib` are standard modules used below. `StoreError` is the one error these clients raise.

```python
@dataclass(frozen=True, slots=True)
class Exemplar:
    """One histogram sample that carries the trace it was measured in."""

    trace_id: str
    value: float
    timestamp: float


@dataclass(frozen=True, slots=True)
class SpanView:
    """The parts of a span the investigation needs."""

    name: str
    service: str
    span_id: str
    parent_span_id: str
    duration_ms: float
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogLine:
    """One log entry from Loki."""

    timestamp_ns: int
    line: str
    labels: Mapping[str, str]
    structured_metadata: Mapping[str, str]
```

Four frozen dataclasses: an `Exemplar` (trace id, measured value, time), a `SpanView` (the parts of a span the walk needs: name, service, ids, duration in milliseconds, attributes), and a `LogLine` (time in nanoseconds, the text, the stream labels and the structured metadata). `field(default_factory=dict)` gives `attributes` a fresh empty dict by default; a plain `= {}` would be one dict shared by all objects.

```python
def is_complete(spans: list[SpanView]) -> bool:
    """True when the spans form one tree: exactly one root, and every other span's parent is present."""
    span_ids = {span.span_id for span in spans}
    roots = [span for span in spans if not span.parent_span_id]
    return len(roots) == 1 and all(span.parent_span_id in span_ids for span in spans if span.parent_span_id)
```

`is_complete(spans)` is true when the spans form a single tree: exactly one root (a span with no parent) and every other span's parent present. Tempo can return half a trace right after the request; this check tells the caller to wait.

```python
def _get(url: str, **kwargs: Any) -> requests.Response:
    try:
        response = requests.get(url, timeout=kwargs.pop("timeout", 60), **kwargs)
    except requests.RequestException as error:
        raise StoreError(f"GET {url} failed: {error}") from error
    return response
```

`_get` is the one place that calls `requests.get`. It sets a 60-second timeout unless the caller gives another (`kwargs.pop("timeout", 60)` removes the key and returns it or the default), passes the rest through, and converts any `requests.RequestException` to `StoreError` with the URL in the message.

```python
def _id_to_hex(value: str) -> str:
    """Tempo returns ids either as hex or as base64 bytes; normalize to lowercase hex."""
    stripped = value.strip()
    if len(stripped) in (16, 32) and all(char in "0123456789abcdefABCDEF" for char in stripped):
        return stripped.lower()
    return base64.b64decode(stripped + "=" * (-len(stripped) % 4)).hex()


def _attribute_value(wrapper: Mapping[str, Any]) -> Any:
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in wrapper:
            return wrapper[key]
    return None
```

`_id_to_hex` normalizes ids. Tempo's JSON API sometimes returns ids as hexadecimal text and sometimes as base64 bytes. If the text has 16 or 32 characters and all are hex digits it is lower-cased and returned; otherwise it is base64-decoded (padding `=` characters are added as needed: `-len(stripped) % 4` is how many are missing) and written as hex. `_attribute_value` reads the value out of OpenTelemetry's JSON attribute shape, where the value sits under a key named after its type (`stringValue`, `intValue`, ...).

```python
class PrometheusClient:
    """Instant queries, exemplar queries and readiness."""

    def __init__(self, base_url: str) -> None:
        """Take the Prometheus base URL."""
        self._base = base_url.rstrip("/")

    def ready(self) -> bool:
        """True when Prometheus reports itself healthy."""
        return _get(f"{self._base}/-/healthy").status_code == 200

    def query(self, expression: str) -> list[tuple[Mapping[str, str], float]]:
        """Run an instant query and return (labels, value) pairs."""
        body = self._json(_get(f"{self._base}/api/v1/query", params={"query": expression}))
        return [(item["metric"], float(item["value"][1])) for item in body["data"]["result"]]
```

`PrometheusClient`: `ready` calls `/-/healthy`; `query` calls `/api/v1/query` (an instant query, see `promql.md`) with the expression as the `query` parameter and returns pairs of (labels, value). Prometheus answers each sample as `[timestamp, "value"]`, so the value is `float(item["value"][1])`.

```python
    def scalar(self, expression: str, default: float = 0.0) -> float:
        """Run an instant query expected to return at most one series and return its value."""
        result = self.query(expression)
        return result[0][1] if result else default

    def query_range(self, expression: str, start: float, end: float, step_seconds: float) -> list[tuple[float, float]]:
        """Run a range query expected to return one series and return its (time, value) points."""
        params = {"query": expression, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": f"{step_seconds:.0f}"}
        body = self._json(_get(f"{self._base}/api/v1/query_range", params=params))
        result = body["data"]["result"]
        if not result:
            return []
        return [(float(when), float(value)) for when, value in result[0]["values"] if value != "NaN"]
```

`scalar` returns the first value of a query expected to return one series, or a default. `query_range` calls `/api/v1/query_range` with `start`, `end` and `step` and returns the points of the first series as `(time, value)` pairs, skipping `NaN` ("not a number", what a division by zero gives).

```python
    def exemplars(self, selector: str, start: float, end: float) -> list[Exemplar]:
        """Return every exemplar stored for the selector between two Unix times, newest last."""
        params = {"query": selector, "start": f"{start:.3f}", "end": f"{end:.3f}"}
        body = self._json(_get(f"{self._base}/api/v1/query_exemplars", params=params))
        found = [
            Exemplar(item["labels"]["trace_id"], float(item["value"]), float(item["timestamp"]))
            for series in body["data"]
            for item in series["exemplars"]
            if "trace_id" in item["labels"]
        ]
        return sorted(found, key=lambda exemplar: exemplar.timestamp)
```

`exemplars` calls `/api/v1/query_exemplars`. The answer is a list of series, each with a list of exemplars; a nested comprehension flattens it, keeps those that have a `trace_id` label, and builds `Exemplar` objects. The result is sorted by time.

```python
    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        if response.status_code != 200:
            raise StoreError(f"Prometheus answered {response.status_code}: {response.text[:200]}")
        body: dict[str, Any] = response.json()
        if body.get("status") != "success":
            raise StoreError(f"Prometheus query failed: {body}")
        return body
```

`_json` checks the HTTP status and Prometheus's own `"status": "success"` field before returning the body. `@staticmethod` marks a method that does not use `self`.

```python
class TempoClient:
    """Trace lookup by id, tag search and readiness."""

    def __init__(self, base_url: str) -> None:
        """Take the Tempo base URL."""
        self._base = base_url.rstrip("/")

    def ready(self) -> bool:
        """True when Tempo reports itself ready."""
        response = _get(f"{self._base}/ready")
        return response.status_code == 200 and "ready" in response.text

    def trace(self, trace_id: str) -> list[SpanView] | None:
        """Return the spans of one trace, or ``None`` while Tempo does not know it (yet)."""
        response = _get(f"{self._base}/api/traces/{trace_id}", headers={"Accept": "application/json"})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise StoreError(f"Tempo answered {response.status_code} for trace {trace_id}: {response.text[:200]}")
        return self.parse_trace(response.json())
```

`TempoClient`: `ready` calls `/ready`; `trace(trace_id)` fetches `/api/traces/<id>` asking for JSON. A 404 returns `None` (the trace is not there yet); another error status raises `StoreError`.

```python
    @staticmethod
    def parse_trace(document: Mapping[str, Any]) -> list[SpanView]:
        """Flatten Tempo's batches into span views."""
        spans: list[SpanView] = []
        for batch in document.get("batches", document.get("resourceSpans", [])):
            resource = {
                attribute["key"]: _attribute_value(attribute["value"])
                for attribute in batch.get("resource", {}).get("attributes", [])
            }
            for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])):
                for span in scope.get("spans", []):
                    start = int(span["startTimeUnixNano"])
                    end = int(span["endTimeUnixNano"])
                    spans.append(
                        SpanView(
                            name=span["name"],
                            service=str(resource.get("service.name", "")),
                            span_id=_id_to_hex(span["spanId"]),
                            parent_span_id=_id_to_hex(span["parentSpanId"]) if span.get("parentSpanId") else "",
                            duration_ms=(end - start) / 1e6,
                            attributes={a["key"]: _attribute_value(a["value"]) for a in span.get("attributes", [])},
                        )
                    )
        return spans
```

`parse_trace` flattens Tempo's nested JSON (batches of resource spans, each with scope spans, each with spans) into a flat list of `SpanView`. The resource attributes give the service name. Times arrive as strings of nanoseconds; `(end - start) / 1e6` converts to milliseconds. Tempo versions name some keys differently (`batches`/`resourceSpans`, `scopeSpans`/`instrumentationLibrarySpans`), so `.get(a, .get(b))` accepts both.

```python
    def search(self, traceql: str, start: float, end: float, limit: int = 50) -> list[str]:
        """Return the ids of traces matching a TraceQL query in the time range."""
        params = {"q": traceql, "start": int(start), "end": int(end) + 1, "limit": limit}
        response = _get(f"{self._base}/api/search", params=params)
        if response.status_code != 200:
            raise StoreError(f"Tempo search answered {response.status_code}: {response.text[:200]}")
        return [item["traceID"] for item in response.json().get("traces", [])]
```

`search` runs a TraceQL query on `/api/search` for a time range and returns the matching trace ids.

```python
    def wait_for_trace(
        self,
        trace_id: str,
        timeout_seconds: float = 60.0,
        until: Callable[[list[SpanView]], bool] = lambda spans: is_complete(spans),
    ) -> list[SpanView]:
        """Poll until ``until`` accepts the trace; raise ``StoreError`` if no span appears within the timeout.

        The two services export their spans separately, so Tempo can know half a trace for a few seconds. By default a
        trace is accepted when every span's parent is in it; callers that know which services must appear say so. At the
        deadline the spans found so far are returned.
        """
        deadline = time.monotonic() + timeout_seconds
        found: list[SpanView] = []
        while time.monotonic() < deadline:
            with contextlib.suppress(StoreError):  # a slow or restarting Tempo is retried until the deadline
                found = self.trace(trace_id) or found
            if found and until(found):
                return found
            time.sleep(1.0)
        if found:
            return found
        raise StoreError(f"trace {trace_id} did not appear in Tempo within {timeout_seconds:.0f}s")
```

`wait_for_trace` polls until the `until` function accepts the spans (by default `is_complete`) or the timeout passes. Each pass: `contextlib.suppress(StoreError)` ignores a temporary error from a slow or restarting Tempo; `self.trace(trace_id) or found` keeps the previous answer if the new one is `None`; then it checks and sleeps one second. At the deadline it returns what it has if it has any spans, else raises.

```python
class LokiClient:
    """LogQL range queries and readiness."""

    def __init__(self, base_url: str) -> None:
        """Take the Loki base URL."""
        self._base = base_url.rstrip("/")

    def ready(self) -> bool:
        """True when Loki reports itself ready."""
        response = _get(f"{self._base}/ready")
        return response.status_code == 200 and "ready" in response.text

    def query(self, logql: str, start: float, end: float, limit: int = 1000) -> list[LogLine]:
        """Run a LogQL range query and return the lines oldest first."""
        params = {
            "query": logql,
            "start": int(start * 1e9),
            "end": int(end * 1e9),
            "limit": limit,
            "direction": "forward",
        }
        # Without this flag Loki leaves structured metadata (where the OTLP trace_id lives) out of the response.
        headers = {"X-Loki-Response-Encoding-Flags": "categorize-labels"}
        response = _get(f"{self._base}/loki/api/v1/query_range", params=params, headers=headers)
        if response.status_code != 200:
            raise StoreError(f"Loki answered {response.status_code}: {response.text[:200]}")
        lines: list[LogLine] = []
        for stream in response.json()["data"]["result"]:
            for entry in stream["values"]:
                metadata = entry[2] if len(entry) > 2 else {}
                structured = metadata.get("structuredMetadata", metadata) if isinstance(metadata, dict) else {}
                lines.append(LogLine(int(entry[0]), entry[1], stream["stream"], structured))
        return sorted(lines, key=lambda line: line.timestamp_ns)
```

`LokiClient.query` sends a LogQL range query (`/loki/api/v1/query_range`). Times are in nanoseconds (`start * 1e9`). `direction: forward` returns oldest first. The header `X-Loki-Response-Encoding-Flags: categorize-labels` is required: without it Loki leaves the structured metadata (where the OTLP trace id lives) out of the answer. Each result entry is `[timestamp, line, metadata]`; the code reads `structuredMetadata` from the third element when it exists. The result is sorted by time.

```python
class GrafanaClient:
    """Datasource health checks through Grafana's own API."""

    def __init__(self, base_url: str, user: str, password: str) -> None:
        """Take the Grafana base URL and the admin credentials."""
        self._base = base_url.rstrip("/")
        self._auth = (user, password)

    def datasource_health(self, uid: str) -> tuple[bool, str]:
        """Ask Grafana to query one datasource; returns (ok, message).

        Prometheus and Loki have a backend health check. Tempo's data source has none in Grafana 11.2 (the API answers
        404, "Method not implemented"), so it is queried through Grafana's data source proxy with a real search, which
        is what the "Save & test" button does.
        """
        if uid == "tempo":
            proxied = _get(f"{self._base}/api/datasources/proxy/uid/tempo/api/search?limit=1", auth=self._auth)
            return proxied.status_code == 200, f"Tempo answered {proxied.status_code} through Grafana's proxy"
        response = _get(f"{self._base}/api/datasources/uid/{uid}/health", auth=self._auth)
        message = ""
        try:
            message = str(response.json().get("message", ""))
        except ValueError:
            message = response.text[:200]
        return response.status_code == 200, message

    def datasource_uids(self) -> list[str]:
        """The uids of every provisioned datasource."""
        response = _get(f"{self._base}/api/datasources", auth=self._auth)
        if response.status_code != 200:
            raise StoreError(f"Grafana answered {response.status_code}")
        return sorted(item["uid"] for item in response.json())
```

`GrafanaClient` asks Grafana to test each data source with the admin login (`auth=(user, password)` is HTTP basic authentication). Prometheus and Loki answer on `/api/datasources/uid/<uid>/health`. Grafana 11.2 has no health check for Tempo (the API answers 404 "Method not implemented"), so for Tempo the client sends a real search through Grafana's proxy, which is what the "Save & test" button does. `datasource_uids` lists the provisioned data sources, sorted.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/infrastructure/test_http_back_client.py tests/unit/lab/test_stores.py -q
```

Expected: `12 passed`. The first file starts a throw-away HTTP server on your machine to test statuses, retries and timeouts.

```bash
.venv/bin/python -c "
from three_signals.lab.stores import PrometheusClient
print(PrometheusClient('http://127.0.0.1:29090').scalar('up{job=\"otel-collector\"}'))"
```

Expected (needs `make up`): `1.0`.

```bash
.venv/bin/python -c "
from three_signals.lab.stores import TempoClient
print(TempoClient('http://127.0.0.1:23200').search('{ resource.service.name = \"back\" }', 0, 4102444800, 3))"
```

Expected (needs traffic from `make run`): a list of up to three trace ids.

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:23100/ready
```

Expected: `200`.

## Mistakes people make

- **No timeout.** The most common cause of a hung service.
- **Retrying a read timeout.** The slow server is already busy; a retry doubles the work. `read=False` avoids it.
- **Creating a new Session per call**, losing connection reuse.
- **Catching `Exception` instead of `requests.RequestException`.**
- **Not checking `status_code` before `.json()`.**

## Check yourself

1. What does a Session add over `requests.get`? *Connection reuse and shared settings.*
2. What does `timeout=5.0` limit? *The connect wait and each wait for data, not the total time.*
3. Which exception means "took too long"? *`requests.Timeout`.*
4. Why is only `GET` retried here? *It is idempotent: repeating it changes nothing.*
5. What does `raise_on_status=False` change? *After the retries are used up, the last response is returned instead of an exception.*

## Further reading

- requests 2.34 documentation: https://requests.readthedocs.io/en/stable/
- Advanced usage (sessions, adapters, timeouts): https://requests.readthedocs.io/en/stable/user/advanced/
- urllib3 `Retry`: https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html#urllib3.util.Retry
- `urllib.request`: https://docs.python.org/3.12/library/urllib.request.html
