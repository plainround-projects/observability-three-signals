import pytest

from three_signals.domain.errors import InvalidOrderIdError
from three_signals.domain.orders import ItemCountBucket, Order, OrderHeader, OrderId, OrderItem


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


def test_bucket_values_are_the_strings_used_as_attribute_values():
    assert [str(bucket) for bucket in ItemCountBucket] == ["0", "1-2", "3-5", "6+"]


@pytest.mark.parametrize("bad", [0, -1, True])
def test_order_id_rejects_non_positive_and_boolean_values(bad):
    with pytest.raises(InvalidOrderIdError):
        OrderId(bad)


def test_order_id_rejects_a_string():
    with pytest.raises(InvalidOrderIdError):
        OrderId("7")  # type: ignore[arg-type]


def test_order_total_is_the_sum_of_its_lines_and_its_bucket_follows_the_item_count():
    items = (OrderItem(1, "SKU-001", 2, 500), OrderItem(2, "SKU-002", 1, 1250), OrderItem(3, "SKU-003", 3, 100))
    order = Order(OrderHeader(OrderId(3), "customer-0003"), items)
    assert order.total_cents == 2 * 500 + 1250 + 3 * 100
    assert order.item_count_bucket is ItemCountBucket.THREE_TO_FIVE


def test_orders_are_immutable():
    header = OrderHeader(OrderId(1), "customer-0001")
    with pytest.raises(AttributeError):
        header.customer_name = "other"  # type: ignore[misc]
