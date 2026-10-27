"""The order model: immutable values with the few rules the services rely on."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from three_signals.domain.errors import InvalidOrderIdError


@dataclass(frozen=True, slots=True)
class OrderId:
    """A positive integer identifying one order."""

    value: int

    def __post_init__(self) -> None:
        """Reject zero, negative and non-integer ids at construction."""
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 1:
            raise InvalidOrderIdError(f"order id must be a positive integer, got {self.value!r}")


@dataclass(frozen=True, slots=True)
class OrderItem:
    """One line of an order."""

    item_id: int
    sku: str
    quantity: int
    unit_price_cents: int

    @property
    def line_total_cents(self) -> int:
        """Price of the whole line in cents."""
        return self.quantity * self.unit_price_cents


class ItemCountBucket(StrEnum):
    """A bounded category for the number of items in an order.

    The raw count (or the order id) is unbounded, so it never labels a metric; this handful of
    values can safely group spans and series.
    """

    NONE = "0"
    ONE_TO_TWO = "1-2"
    THREE_TO_FIVE = "3-5"
    SIX_OR_MORE = "6+"

    @classmethod
    def for_count(cls, item_count: int) -> ItemCountBucket:
        """Return the bucket that holds ``item_count`` items."""
        if item_count <= 0:
            return cls.NONE
        if item_count <= 2:
            return cls.ONE_TO_TWO
        if item_count <= 5:
            return cls.THREE_TO_FIVE
        return cls.SIX_OR_MORE


@dataclass(frozen=True, slots=True)
class OrderHeader:
    """The row from the orders table, without its items."""

    order_id: OrderId
    customer_name: str


@dataclass(frozen=True, slots=True)
class Order:
    """An order with all of its items."""

    header: OrderHeader
    items: tuple[OrderItem, ...]

    @property
    def total_cents(self) -> int:
        """Sum of every line."""
        return sum(item.line_total_cents for item in self.items)

    @property
    def item_count_bucket(self) -> ItemCountBucket:
        """The bounded category of this order's size."""
        return ItemCountBucket.for_count(len(self.items))
