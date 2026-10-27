"""The JSON shape of an order on the wire between front, back and the client."""

from __future__ import annotations

from typing import Any

from three_signals.domain.orders import Order, OrderHeader, OrderId, OrderItem


def order_to_json(order: Order) -> dict[str, Any]:
    """Serialize an order the way the back service returns it."""
    return {
        "order": {
            "id": order.header.order_id.value,
            "customer": order.header.customer_name,
            "total_cents": order.total_cents,
            "item_count_bucket": str(order.item_count_bucket),
        },
        "items": [
            {
                "id": item.item_id,
                "sku": item.sku,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
            }
            for item in order.items
        ],
    }


def order_from_json(payload: dict[str, Any]) -> Order:
    """Parse the back service's response; raises ``KeyError``, ``TypeError`` or a domain error when malformed."""
    header = payload["order"]
    items = tuple(
        OrderItem(item["id"], item["sku"], item["quantity"], item["unit_price_cents"]) for item in payload["items"]
    )
    return Order(OrderHeader(OrderId(header["id"]), header["customer"]), items)
