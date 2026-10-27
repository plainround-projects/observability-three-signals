from contextlib import contextmanager

import pytest

from three_signals.application.assemble_order import AssembleOrder
from three_signals.application.fetch_order import FetchOrder
from three_signals.domain.errors import OrderNotFoundError
from three_signals.domain.orders import ItemCountBucket, Order, OrderHeader, OrderId, OrderItem

MISSING = object()
ITEMS = (OrderItem(1, "SKU-001", 1, 500), OrderItem(2, "SKU-002", 2, 700))


class FakeRepository:
    def __init__(self, header=MISSING, items=ITEMS):
        self.header = OrderHeader(OrderId(5), "customer-0005") if header is MISSING else header
        self.items = items
        self.delays = []

    def find_header(self, order_id):
        return self.header

    def find_items(self, order_id, planned_delay_seconds):
        self.delays.append(planned_delay_seconds)
        return self.items


class FakePlanner:
    def __init__(self, delay):
        self.delay = delay

    def next_delay_seconds(self):
        return self.delay


class FakeTelemetry:
    def __init__(self):
        self.buckets, self.entered, self.exited = [], 0, 0

    @contextmanager
    def measure(self):
        self.entered += 1
        try:
            yield self
        finally:
            self.exited += 1

    def record_bucket(self, bucket):
        self.buckets.append(bucket)


def test_assembling_an_order_returns_header_and_items_and_records_the_bucket():
    telemetry = FakeTelemetry()
    order = AssembleOrder(FakeRepository(), FakePlanner(0.0), telemetry).execute(OrderId(5))
    assert order.items == ITEMS
    assert telemetry.buckets == [ItemCountBucket.ONE_TO_TWO]
    assert (telemetry.entered, telemetry.exited) == (1, 1)


def test_the_planned_delay_reaches_the_items_query():
    repository = FakeRepository()
    AssembleOrder(repository, FakePlanner(1.5), FakeTelemetry()).execute(OrderId(5))
    assert repository.delays == [1.5]


def test_a_missing_order_raises_not_found_and_still_closes_the_measurement():
    telemetry = FakeTelemetry()
    use_case = AssembleOrder(FakeRepository(header=None), FakePlanner(0.0), telemetry)
    with pytest.raises(OrderNotFoundError) as raised:
        use_case.execute(OrderId(9))
    assert raised.value.order_id == 9
    assert (telemetry.entered, telemetry.exited) == (1, 1)
    assert telemetry.buckets == []


def test_assembling_logs_one_line_with_the_order_size(caplog):
    with caplog.at_level("INFO"):
        AssembleOrder(FakeRepository(), FakePlanner(0.0), FakeTelemetry()).execute(OrderId(5))
    record = next(entry for entry in caplog.records if entry.getMessage() == "order assembled")
    assert (record.order_id, record.item_count, record.bucket) == (5, 2, "1-2")


class FakeBack:
    def __init__(self):
        self.asked = []

    def fetch_order(self, order_id):
        self.asked.append(order_id)
        return Order(OrderHeader(order_id, "customer"), ITEMS)


def test_fetching_an_order_asks_the_back_service_once():
    back = FakeBack()
    order = FetchOrder(back).execute(OrderId(2))
    assert back.asked == [OrderId(2)]
    assert order.items == ITEMS
