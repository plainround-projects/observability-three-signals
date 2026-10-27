"""Named errors that cross layer boundaries."""


class DomainError(Exception):
    """Base class for every error this project raises on purpose."""


class InvalidOrderIdError(DomainError):
    """The order id is not a positive integer."""


class OrderNotFoundError(DomainError):
    """No order with this id exists."""

    def __init__(self, order_id: int) -> None:
        super().__init__(f"order {order_id} does not exist")
        self.order_id = order_id


class BackendUnavailableError(DomainError):
    """The back service answered with a server error or could not be reached."""


class BackendTimeoutError(BackendUnavailableError):
    """The back service did not answer within the configured time."""
