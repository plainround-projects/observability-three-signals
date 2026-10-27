"""PostgreSQL adapter: the two queries behind an order."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol

from three_signals.domain.orders import OrderHeader, OrderId, OrderItem

_log = logging.getLogger(__name__)

_HEADER_SQL = "SELECT id, customer_name FROM orders WHERE id = %(order_id)s"

# The CROSS JOIN with pg_sleep(0) costs nothing on a normal request; with a planned delay it makes the
# database itself spend those seconds inside this one statement, so the slow span is the SELECT span.
_ITEMS_SQL = (
    "SELECT oi.id, oi.sku, oi.quantity, oi.unit_price_cents "
    "FROM order_items AS oi CROSS JOIN pg_sleep(%(delay_seconds)s) "
    "WHERE oi.order_id = %(order_id)s ORDER BY oi.id"
)


class ConnectionSource(Protocol):
    """What the repository needs from a connection pool: a connection that closes (or returns) on exit."""

    def connection(self) -> AbstractContextManager[Any]:
        """Borrow a connection."""
        ...


class PostgresOrderRepository:
    """Reads orders through a connection pool."""

    def __init__(
        self,
        pool: ConnectionSource,
        slow_warn_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Take the pool, the duration above which a query is logged as slow, and an injectable clock."""
        self._pool = pool
        self._slow_warn_seconds = slow_warn_seconds
        self._clock = clock

    def find_header(self, order_id: OrderId) -> OrderHeader | None:
        """Return the order row, or ``None``."""
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(_HEADER_SQL, {"order_id": order_id.value})
            row = cursor.fetchone()
        return None if row is None else OrderHeader(OrderId(row[0]), row[1])

    def find_items(self, order_id: OrderId, planned_delay_seconds: float) -> tuple[OrderItem, ...]:
        """Return the order's lines, logging when the query starts and how long it took."""
        _log.info("order_items query started", extra={"order_id": order_id.value})
        started = self._clock()
        with self._pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(_ITEMS_SQL, {"order_id": order_id.value, "delay_seconds": float(planned_delay_seconds)})
            rows = cursor.fetchall()
        elapsed = self._clock() - started
        slow = elapsed >= self._slow_warn_seconds
        _log.log(
            logging.WARNING if slow else logging.INFO,
            "slow query finished" if slow else "order_items query finished",
            extra={"order_id": order_id.value, "duration_ms": round(elapsed * 1000)},
        )
        return tuple(OrderItem(row[0], row[1], row[2], row[3]) for row in rows)
