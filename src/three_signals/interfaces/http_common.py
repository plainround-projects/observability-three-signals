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


def register_common(app: Flask) -> None:
    """Add the ``X-Trace-Id`` header to every response and map the named errors to JSON problems."""

    @app.after_request
    def add_trace_id(response: Response) -> Response:
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            response.headers["X-Trace-Id"] = trace.format_trace_id(context.trace_id)
        return response

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

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> tuple[Response, int]:
        return _problem(error.code or 500, (error.name or "error").lower().replace(" ", "_"), error.description or "")

    @app.errorhandler(Exception)
    def unexpected(error: Exception) -> tuple[Response, int]:
        _log.exception("unhandled error")
        return _problem(500, "internal_error", "the service failed; see its logs with this trace id")
