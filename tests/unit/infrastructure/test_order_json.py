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
