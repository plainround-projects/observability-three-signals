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
