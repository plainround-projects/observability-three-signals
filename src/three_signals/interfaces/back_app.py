"""The back service's HTTP surface."""

from __future__ import annotations

from collections.abc import Callable

from flask import Flask, Response, jsonify

from three_signals.application.assemble_order import AssembleOrder
from three_signals.domain.orders import OrderId
from three_signals.infrastructure.order_json import order_to_json
from three_signals.interfaces.http_common import register_common


def create_back_app(assemble_order: AssembleOrder, check_database: Callable[[], None]) -> Flask:
    """Build the Flask app: ``GET /order/<id>`` and ``GET /healthz`` (which checks the database)."""
    app = Flask("back")
    register_common(app)

    @app.get("/order/<int:order_id>")
    def get_order(order_id: int) -> Response:
        return jsonify(order_to_json(assemble_order.execute(OrderId(order_id))))

    @app.get("/healthz")
    def healthz() -> tuple[Response, int]:
        try:
            check_database()
        except Exception:
            return jsonify({"status": "unavailable", "database": "down"}), 503
        return jsonify({"status": "ok", "database": "up"}), 200

    return app
