"""The front service's use case: ask the back service for an order."""

from __future__ import annotations

import logging

from three_signals.application.ports import BackClient
from three_signals.domain.orders import Order, OrderId

_log = logging.getLogger(__name__)


class FetchOrder:
    """Forwards an order lookup to the back service."""

    def __init__(self, back: BackClient) -> None:
        """Wire the use case to the client of the back service."""
        self._back = back

    def execute(self, order_id: OrderId) -> Order:
        """Return the order the back service assembled."""
        order = self._back.fetch_order(order_id)
        _log.info("order fetched from back", extra={"order_id": order_id.value})
        return order
