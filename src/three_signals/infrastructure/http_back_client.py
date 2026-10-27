"""HTTP adapter the front service uses to call the back service, with timeouts and retries."""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from three_signals.domain.errors import BackendTimeoutError, BackendUnavailableError, OrderNotFoundError
from three_signals.domain.orders import Order, OrderId
from three_signals.infrastructure.order_json import order_from_json


def build_session(retries: int) -> requests.Session:
    """Create a session that retries idempotent GETs on connection errors and 502, 503, 504 with backoff.

    A read timeout is not retried: the back service is already slow, and a retry would double the wait.
    """
    retry = Retry(
        total=retries,
        read=False,
        backoff_factor=0.2,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class HttpBackClient:
    """Calls ``GET /order/<id>`` on the back service."""

    def __init__(self, base_url: str, session: requests.Session, timeout_seconds: float) -> None:
        """Take the back service's base URL, a (retrying) session and the per-request timeout."""
        self._base_url = base_url
        self._session = session
        self._timeout_seconds = timeout_seconds

    def fetch_order(self, order_id: OrderId) -> Order:
        """Return the order, mapping 404, timeouts and server errors to named domain errors."""
        try:
            response = self._session.get(f"{self._base_url}/order/{order_id.value}", timeout=self._timeout_seconds)
        except requests.Timeout as error:
            raise BackendTimeoutError(f"back did not answer within {self._timeout_seconds}s") from error
        except requests.RequestException as error:
            raise BackendUnavailableError(f"back is unreachable: {error.__class__.__name__}") from error
        if response.status_code == 404:
            raise OrderNotFoundError(order_id.value)
        if response.status_code >= 500:
            raise BackendUnavailableError(f"back answered {response.status_code}")
        try:
            return order_from_json(response.json())
        except (ValueError, KeyError, TypeError) as error:
            raise BackendUnavailableError("back sent a response this service cannot read") from error
