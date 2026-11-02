# HTTP and the OTLP wire (what the services say to each other)

## What it is

HTTP is the protocol (the agreed rules) that programs use to talk over a network. A **client** sends a **request**; a **server** sends back a **response**. A request has a **method** (`GET` reads, `POST` creates or sends data), a **path** (`/order/3`), **headers** (extra information as `Name: value` lines) and sometimes a **body**. A response has a **status code**, headers and a body. This repository's services, the monitoring stores, Grafana and the Collector all speak HTTP.

## Why this repository uses it

Every arrow in the architecture diagram is an HTTP call: the load scenario calls the front service, the front calls the back, the services send telemetry to the Collector (OTLP over HTTP), Prometheus scrapes the Collector, Grafana queries the stores, and the lab tools query the stores. HTTP is also how trace context travels: a header.

## Where it appears

| Where | The HTTP involved |
| --- | --- |
| `interfaces/*_app.py`, `http_common.py` | Routes, status codes, the `X-Trace-Id` response header. |
| `infrastructure/http_back_client.py` | The front's call to the back, with timeout and retries. |
| `lab/stores.py`, `lab/scenario.py` | Calls to the stores and to the front. |
| `docker-compose.yml` | Published ports and health checks over HTTP. |
| `observability/collector/config.yaml` | OTLP/HTTP receiver on 4318. |

## How it works underneath

The client opens a **TCP connection** to the server's address and **port** (a number that picks one program on that machine, such as 8080). It writes the request as text lines. The server reads them, does the work and writes the response. Status codes are grouped by first digit: `2xx` success (`200 OK`), `3xx` redirect, `4xx` the client made a mistake (`400 Bad Request`, `404 Not Found`), `5xx` the server failed (`500`, `502 Bad Gateway`, `503 Service Unavailable`, `504 Gateway Timeout`).

A **timeout** is how long the client waits before giving up. A **connection timeout** applies to opening the connection; a **read timeout** applies to waiting for the response bytes. A **retry** sends the same request again after a failure; it is only safe for requests that change nothing (`GET` is idempotent).

The address `127.0.0.1` (also `localhost`) is the loopback address: it always means "this same machine". `0.0.0.0` as a *listen* address means "all network interfaces of this machine".

A **health endpoint** (here `/healthz`) is a path that answers 200 when the service can do its job; orchestrators and Docker call it repeatedly.

A **header** can carry the trace identity between services. `traceparent` is the W3C standard header that OpenTelemetry uses to pass the trace id and the parent span id from the caller to the callee.

## Walkthrough of this repository's use

**The path of one request.**

1. The scenario sends `GET /order/3` to `127.0.0.1:28081` (published port of the front container).
2. The front's Flask instrumentation starts a server span and reads no incoming `traceparent` (the scenario has none), so it creates a new trace id.
3. The front calls `GET http://back:8081/order/3`. The `requests` instrumentation adds a `traceparent` header (the trace id, the front span's id, and a flag); the format is `00-<32 hex trace id>-<16 hex span id>-01`.
4. The back's Flask instrumentation reads `traceparent`, starts a server span as a **child** of the front's client span, and the SQL spans become its descendants.
5. The back answers 200 with JSON; the front answers the scenario with 200, the JSON and the header `X-Trace-Id`.
6. Every 5 seconds each service sends its telemetry to `http://otel-collector:4318/v1/traces` (and `/v1/metrics`, `/v1/logs`) with `POST` and a protobuf body.

The status handling in code:

```python
    @app.errorhandler(InvalidOrderIdError)
    def invalid_order_id(error: InvalidOrderIdError) -> tuple[Response, int]:
        return _problem(400, "invalid_order_id", str(error))

    @app.errorhandler(OrderNotFoundError)
    def order_not_found(error: OrderNotFoundError) -> tuple[Response, int]:
        return _problem(404, "order_not_found", str(error))

    @app.errorhandler(BackendTimeoutError)
    def backend_timeout(error: BackendTimeoutError) -> tuple[Response, int]:
        _log.warning("back timed out", extra={"detail": str(error)})
        return _problem(504, "backend_timeout", str(error))

    @app.errorhandler(BackendUnavailableError)
    def backend_unavailable(error: BackendUnavailableError) -> tuple[Response, int]:
        _log.warning("back unavailable", extra={"detail": str(error)})
        return _problem(502, "backend_unavailable", str(error))
```

Each named error maps to one status and one code word: 400 invalid id, 404 not found, 504 the back service timed out, 502 the back service failed or is unreachable.

The retry policy in the client retries only `GET` on 502, 503 and 504:

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

## Try it yourself

```bash
curl -s -i http://127.0.0.1:28081/order/3 | head -12
```

Expected: the status line `HTTP/1.1 200 OK`, headers (`Content-Type: application/json`, `X-Trace-Id`, `Server: three-signals`), a blank line, then the body.

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://127.0.0.1:28081/order/3
```

Expected: `200` and a time in seconds (about 0.02 s, or about 1.5 s for the slow one in twenty).

```bash
docker compose exec back python -c "import urllib.request as u; r = u.urlopen('http://localhost:8081/healthz'); print(r.status, r.read())"
```

Expected: `200 b'{"database":"up","status":"ok"}\n'`.

```bash
curl -s -X POST http://127.0.0.1:28081/order/3 -w "\n%{http_code}\n"
```

Expected: a JSON problem with `method_not_allowed` and status `405`.

```bash
curl -s -H "traceparent: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01" -D - -o /dev/null http://127.0.0.1:28081/order/3 | grep -i x-trace-id
```

Expected: `X-Trace-Id: 0af7651916cd43dd8448eb211c80319c`: the service continued the trace you sent.

## Mistakes people make

- **No timeout.** A call without a timeout can wait forever. Every client call here sets one.
- **Retrying non-idempotent calls.** Retrying a payment could charge twice.
- **Using `0.0.0.0` as a destination.** It is only meaningful as a listen address.
- **Confusing 502, 503 and 504.** 502: a gateway got a bad answer; 503: not ready or overloaded; 504: the upstream did not answer in time.

## Check yourself

1. What are the parts of an HTTP request? *Method, path, headers, optional body.*
2. What does a 404 mean? *The thing asked for was not found.*
3. Which statuses count as server failures? *The 5xx family.*
4. What does listening on `127.0.0.1` allow? *Only connections from the same machine.*
5. Why is `GET` safe to retry? *It does not change anything on the server.*

## Further reading

- MDN, HTTP overview: https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview
- HTTP semantics (RFC 9110): https://www.rfc-editor.org/rfc/rfc9110
- W3C Trace Context (the `traceparent` header): https://www.w3.org/TR/trace-context/
