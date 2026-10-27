"""The boundaries the use cases talk through; infrastructure supplies the implementations."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from three_signals.domain.orders import ItemCountBucket, Order, OrderHeader, OrderId, OrderItem


class OrderRepository(Protocol):
    """Reads orders from the database."""

    def find_header(self, order_id: OrderId) -> OrderHeader | None:
        """Return the order row, or ``None`` when no such order exists."""
        ...

    def find_items(self, order_id: OrderId, planned_delay_seconds: float) -> tuple[OrderItem, ...]:
        """Return the order's lines; the database spends ``planned_delay_seconds`` extra on the query."""
        ...


class SlowQueryPlanner(Protocol):
    """Decides, per request, how much slower the items query should be (the planted fault)."""

    def next_delay_seconds(self) -> float:
        """Return 0.0 for a normal request, or the extra seconds the items query must take."""
        ...


class AssemblyScope(Protocol):
    """What the use case may record while an order is being assembled."""

    def record_bucket(self, bucket: ItemCountBucket) -> None:
        """Attach the order-size bucket to the span (and the duration metric) of this assembly."""
        ...


class AssemblyTelemetry(Protocol):
    """Wraps the assembly of one order in a span and a duration measurement."""

    def measure(self) -> AbstractContextManager[AssemblyScope]:
        """Open the span and start the clock; leaving the block closes the span and records the duration."""
        ...


class BackClient(Protocol):
    """How the front service asks the back service for an order."""

    def fetch_order(self, order_id: OrderId) -> Order:
        """Return the order, raising ``OrderNotFoundError`` or ``BackendUnavailableError``."""
        ...
