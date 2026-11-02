# Flask

## What it is

Flask is a small Python library for writing web applications. You create an `app` object, and tell it which Python function answers which URL path and HTTP method. Flask does the HTTP details: it parses the request, calls your function, turns the return value into a response. It is called a **micro-framework** because it gives you routing, request and response objects and error handling, and leaves everything else (database, login) to other libraries. This repository uses Flask 3.1.

## Why this repository uses it

The lesson needs two small web services that talk to each other: a **front** service that the load scenario calls, and a **back** service that reads the database. Flask is small enough that the whole HTTP surface of each service fits on a page, and OpenTelemetry has a ready-made Flask instrumentation that turns every request into a span and continues a trace that arrives in the `traceparent` header.

## Where it appears

| Path | What it does with Flask |
| --- | --- |
| `src/three_signals/interfaces/back_app.py` | The back service: `GET /order/<id>` and `GET /healthz` (which checks the database). |
| `src/three_signals/interfaces/front_app.py` | The front service: `GET /order/<id>` (calls the back service) and `GET /healthz`. |
| `src/three_signals/interfaces/http_common.py` | What both share: the `X-Trace-Id` response header and the mapping of named errors to JSON problems. |
| `tests/unit/interfaces/test_apps.py` | Calls both apps through Flask's test client. |

## How it works underneath

A Flask app is a **WSGI application**. WSGI (Web Server Gateway Interface) is the Python standard for the contract between a web server and an application: the server calls the app as a function with the request data and a callback, and the app returns the response body. Flask itself does not listen on a port; a WSGI server (here, waitress) does, and calls the Flask app for each request.

`@app.get("/order/<int:order_id>")` registers a **route**. The `<int:order_id>` part is a **converter**: it captures that part of the path, converts it to an `int`, and passes it as the argument `order_id`. A path such as `/order/abc` does not match, so Flask answers 404.

A function returns a `Response`, usually made with `jsonify(...)`, which turns a Python dict into a JSON body with the right `Content-Type` header. A return value of `(response, 503)` sets the status code.

`@app.after_request` registers a function that runs on every response before it is sent, and may change it (here: add a header). `@app.errorhandler(SomeError)` registers a function that turns that exception into a response. Flask picks the most specific handler: a handler for `OrderNotFoundError` wins over one for `Exception`.

`app.test_client()` gives a fake client that calls the app directly, without a network, which is how the unit tests exercise the routes.

The OpenTelemetry Flask instrumentation wraps the app so each request becomes a server span, and reads the incoming `traceparent` header.

## Walkthrough of this repository's use

### `interfaces/back_app.py`

```python
"""The back service's HTTP surface."""

from __future__ import annotations

from collections.abc import Callable

from flask import Flask, Response, jsonify

from three_signals.application.assemble_order import AssembleOrder
from three_signals.domain.orders import OrderId
from three_signals.infrastructure.order_json import order_to_json
from three_signals.interfaces.http_common import register_common

```

`Flask` is the application class, `Response` the response type (used in hints), `jsonify` builds a JSON response. The module imports the use case and the JSON helper, plus `register_common` for the shared behavior. `Callable[[], None]` is the type of a function that takes no arguments and returns nothing.

```python
def create_back_app(assemble_order: AssembleOrder, check_database: Callable[[], None]) -> Flask:
    """Build the Flask app: ``GET /order/<id>`` and ``GET /healthz`` (which checks the database)."""
    app = Flask("back")
    register_common(app)
```

`create_back_app` is a **factory function**: it builds and returns a new app each time. Tests can build as many apps as they like, each with fake collaborators. `Flask("back")` names the app (the import name Flask uses to find resources). `register_common(app)` adds the shared hooks and handlers described below. The function takes the use case and a `check_database` function as parameters, so the web layer never creates a database connection itself.

```python
    @app.get("/order/<int:order_id>")
    def get_order(order_id: int) -> Response:
        return jsonify(order_to_json(assemble_order.execute(OrderId(order_id))))
```

`@app.get("/order/<int:order_id>")` registers `get_order` for GET requests to paths like `/order/3`. `<int:order_id>` captures the segment as an integer; a path like `/order/abc` does not match and becomes a 404. The function creates an `OrderId(order_id)` (which rejects 0 with `InvalidOrderIdError`, becoming HTTP 400 below), runs the use case, converts the order to a dict with `order_to_json` and returns it as JSON. The function is a **closure**: it uses `assemble_order` from the enclosing function.

```python
    @app.get("/healthz")
    def healthz() -> tuple[Response, int]:
        try:
            check_database()
        except Exception:
            return jsonify({"status": "unavailable", "database": "down"}), 503
        return jsonify({"status": "ok", "database": "up"}), 200

    return app
```

`/healthz` calls `check_database()`. If any exception happens, it answers `503` with `{"status": "unavailable", "database": "down"}`; otherwise `200` with `"database": "up"`. Returning `(response, 503)` is Flask's way to set the status. Catching `Exception` here is deliberate and the only broad catch in the service: a health check must turn *any* failure into "unhealthy". `return app` hands the finished app to the caller.

### `interfaces/front_app.py`

```python
"""The front service's HTTP surface."""

from __future__ import annotations

from flask import Flask, Response, jsonify

from three_signals.application.fetch_order import FetchOrder
from three_signals.domain.orders import OrderId
from three_signals.infrastructure.order_json import order_to_json
from three_signals.interfaces.http_common import register_common


def create_front_app(fetch_order: FetchOrder) -> Flask:
    """Build the Flask app: ``GET /order/<id>`` (calls the back service) and ``GET /healthz``."""
    app = Flask("front")
    register_common(app)

    @app.get("/order/<int:order_id>")
    def get_order(order_id: int) -> Response:
        return jsonify(order_to_json(fetch_order.execute(OrderId(order_id))))

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"status": "ok"})

    return app
```

The same shape. `GET /order/<int:order_id>` calls `fetch_order.execute(...)` (which calls the back service) and returns the order as JSON. `/healthz` just answers `{"status": "ok"}`: the front service is healthy if it can answer at all. Whether the back service is healthy shows up as 502 or 504 answers on the order route.

### `interfaces/http_common.py`

```python
"""Behavior both services share: the trace-id response header and the mapping of named errors to statuses."""

from __future__ import annotations

import logging

from flask import Flask, Response, jsonify
from opentelemetry import trace
from werkzeug.exceptions import HTTPException

from three_signals.domain.errors import (
    BackendTimeoutError,
    BackendUnavailableError,
    InvalidOrderIdError,
    OrderNotFoundError,
)

_log = logging.getLogger(__name__)


def _problem(status: int, code: str, detail: str) -> tuple[Response, int]:
    return jsonify({"error": code, "detail": detail}), status
```

`HTTPException` is Werkzeug's base class for HTTP errors such as 404 Not Found (Werkzeug is the library Flask is built on). `_problem(status, code, detail)` builds the one JSON shape every error uses: `{"error": "<code>", "detail": "<text>"}` plus the status. A client can rely on `error` being a fixed machine-readable word.

```python
def register_common(app: Flask) -> None:
    """Add the ``X-Trace-Id`` header to every response and map the named errors to JSON problems."""

    @app.after_request
    def add_trace_id(response: Response) -> Response:
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            response.headers["X-Trace-Id"] = trace.format_trace_id(context.trace_id)
        return response
```

`register_common` is called by both factories. `@app.after_request` registers `add_trace_id`, a function that runs for **every** response after the route has run. It asks OpenTelemetry for the current span. Because the OpenTelemetry Flask instrumentation has opened a server span for this request, the span is valid, and its trace id is copied into the response header `X-Trace-Id`. This is the bridge between a client and the traces: the scenario reads that header and records the trace id of each request it sent.

```python
    @app.errorhandler(InvalidOrderIdError)
    def invalid_order_id(error: InvalidOrderIdError) -> tuple[Response, int]:
        return _problem(400, "invalid_order_id", str(error))

    @app.errorhandler(OrderNotFoundError)
    def order_not_found(error: OrderNotFoundError) -> tuple[Response, int]:
        return _problem(404, "order_not_found", str(error))
```

Error handlers for the two domain errors that come from bad input: `InvalidOrderIdError` becomes `400 invalid_order_id`; `OrderNotFoundError` becomes `404 order_not_found`.

```python
    @app.errorhandler(BackendTimeoutError)
    def backend_timeout(error: BackendTimeoutError) -> tuple[Response, int]:
        _log.warning("back timed out", extra={"detail": str(error)})
        return _problem(504, "backend_timeout", str(error))

    @app.errorhandler(BackendUnavailableError)
    def backend_unavailable(error: BackendUnavailableError) -> tuple[Response, int]:
        _log.warning("back unavailable", extra={"detail": str(error)})
        return _problem(502, "backend_unavailable", str(error))
```

`BackendTimeoutError` becomes `504 backend_timeout` and `BackendUnavailableError` becomes `502 backend_unavailable`, each also writing a `WARNING` log line. Because `BackendTimeoutError` is a subclass of `BackendUnavailableError`, Flask uses the more specific handler for the timeout.

```python
    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> tuple[Response, int]:
        return _problem(error.code or 500, (error.name or "error").lower().replace(" ", "_"), error.description or "")

    @app.errorhandler(Exception)
    def unexpected(error: Exception) -> tuple[Response, int]:
        _log.exception("unhandled error")
        return _problem(500, "internal_error", "the service failed; see its logs with this trace id")
```

`HTTPException` covers errors Flask raises itself (unknown path: 404, wrong method: 405). The handler converts them to the same JSON shape. `error.code or 500` handles a missing code; `(error.name or "error").lower().replace(" ", "_")` turns `Not Found` into `not_found`. The last handler, for plain `Exception`, is the safety net: `_log.exception("unhandled error")` writes the message with the traceback (the JSON formatter puts it in the `exception` field, with the trace id), and the response is a generic `500 internal_error` that does not leak internals.

## Try it yourself

```bash
.venv/bin/python -m pytest tests/unit/interfaces/test_apps.py -q
```

Expected: `14 passed`.

```bash
curl -s -i http://127.0.0.1:28081/order/3
```

Expected (needs `make up`): `HTTP/1.1 200 OK`, a header `X-Trace-Id:` with 32 hexadecimal characters and a JSON body with the order and its items.

```bash
curl -s http://127.0.0.1:28081/order/0
```

Expected: `{"detail":"order id must be a positive integer, got 0","error":"invalid_order_id"}`.

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:28081/order/abc
```

Expected: `404`.

```bash
curl -s http://127.0.0.1:28081/healthz
```

Expected: `{"status":"ok"}`.

## Mistakes people make

- **Running `app.run()` in production.** That is the development server. This repository uses waitress.
- **Handling every error in every route.** One `errorhandler` per error type gives one consistent answer.
- **Returning HTML error pages from an API.** The `HTTPException` handler here turns Flask's own errors (404, 405) into JSON.
- **Global mutable state** shared between requests. The app here only reads objects built at start.

## Check yourself

1. What is WSGI? *The Python standard interface between a web server and an application.*
2. What does `<int:order_id>` do? *Captures the path part, converts it to an int and passes it to the function.*
3. What does `after_request` do? *Runs on each response before it is sent, and can change it.*
4. How does the test client differ from a real client? *It calls the app in-process; there is no network.*
5. Why is `app.run()` not used? *It is the development server, not meant for production.*

## Further reading

- Flask 3.1 documentation: https://flask.palletsprojects.com/en/3.1.x/
- Routing: https://flask.palletsprojects.com/en/3.1.x/quickstart/#routing
- Error handlers: https://flask.palletsprojects.com/en/3.1.x/errorhandling/
- Testing: https://flask.palletsprojects.com/en/3.1.x/testing/
- WSGI: https://peps.python.org/pep-3333/
