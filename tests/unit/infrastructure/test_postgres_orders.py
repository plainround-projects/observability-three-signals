from contextlib import contextmanager

from three_signals.domain.orders import OrderId
from three_signals.infrastructure.postgres_orders import PostgresOrderRepository


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.executed = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


class FakePool:
    def __init__(self, rows):
        self.cursor = FakeCursor(rows)

    @contextmanager
    def connection(self):
        yield FakeConnection(self.cursor)


class StepClock:
    def __init__(self, *readings):
        self.readings = list(readings)

    def __call__(self):
        return self.readings.pop(0)


def test_find_header_returns_none_for_a_missing_order():
    assert PostgresOrderRepository(FakePool([]), 1.0).find_header(OrderId(5)) is None


def test_find_header_maps_the_row():
    header = PostgresOrderRepository(FakePool([(5, "customer-0005")]), 1.0).find_header(OrderId(5))
    assert header is not None
    assert (header.order_id, header.customer_name) == (OrderId(5), "customer-0005")


def test_find_items_passes_the_delay_to_the_database_and_maps_rows():
    pool = FakePool([(1, "SKU-001", 2, 500), (2, "SKU-002", 1, 90)])
    items = PostgresOrderRepository(pool, 1.0, StepClock(0.0, 0.01)).find_items(OrderId(5), 1.5)
    assert [item.sku for item in items] == ["SKU-001", "SKU-002"]
    sql, params = pool.cursor.executed[0]
    assert "pg_sleep(%(delay_seconds)s)" in sql
    assert params == {"order_id": 5, "delay_seconds": 1.5}


def test_a_fast_query_logs_two_info_lines_with_the_duration(caplog):
    with caplog.at_level("INFO"):
        PostgresOrderRepository(FakePool([]), 1.0, StepClock(0.0, 0.012)).find_items(OrderId(5), 0.0)
    started, finished = [r for r in caplog.records if "query" in r.getMessage()]
    assert (started.getMessage(), started.levelname) == ("order_items query started", "INFO")
    assert (finished.getMessage(), finished.levelname, finished.duration_ms) == (
        "order_items query finished",
        "INFO",
        12,
    )


def test_a_slow_query_logs_a_warning_named_slow(caplog):
    with caplog.at_level("INFO"):
        PostgresOrderRepository(FakePool([]), 1.0, StepClock(0.0, 1.502)).find_items(OrderId(5), 1.5)
    finished = [r for r in caplog.records if "query" in r.getMessage()][-1]
    assert (finished.getMessage(), finished.levelname, finished.duration_ms) == ("slow query finished", "WARNING", 1502)
