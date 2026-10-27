"""The back service's use case: read an order and its items and assemble the response."""

from __future__ import annotations

import logging

from three_signals.application.ports import AssemblyTelemetry, OrderRepository, SlowQueryPlanner
from three_signals.domain.errors import OrderNotFoundError
from three_signals.domain.orders import Order, OrderId

_log = logging.getLogger(__name__)


class AssembleOrder:
    """Loads an order's header and items (two queries) inside one measured span."""

    def __init__(
        self,
        repository: OrderRepository,
        planner: SlowQueryPlanner,
        telemetry: AssemblyTelemetry,
    ) -> None:
        """Wire the use case to its collaborators."""
        self._repository = repository
        self._planner = planner
        self._telemetry = telemetry

    def execute(self, order_id: OrderId) -> Order:
        """Return the order, or raise ``OrderNotFoundError``."""
        with self._telemetry.measure() as scope:
            header = self._repository.find_header(order_id)
            if header is None:
                raise OrderNotFoundError(order_id.value)
            items = self._repository.find_items(order_id, self._planner.next_delay_seconds())
            order = Order(header=header, items=items)
            scope.record_bucket(order.item_count_bucket)
        _log.info(
            "order assembled",
            extra={"order_id": order_id.value, "item_count": len(items), "bucket": str(order.item_count_bucket)},
        )
        return order
