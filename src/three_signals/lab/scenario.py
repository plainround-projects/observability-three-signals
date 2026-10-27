"""The load scenario: steady traffic to the front service, so some requests meet the planted slow query."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import requests

ORDER_COUNT = 1000
SLOW_REQUEST_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """The outcome of one request as the client saw it."""

    order_id: int
    status: int
    trace_id: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Everything the scenario sent and got back."""

    records: tuple[RequestRecord, ...]
    started_at: float
    finished_at: float

    @property
    def slow(self) -> tuple[RequestRecord, ...]:
        """Requests that took longer than ``SLOW_REQUEST_SECONDS``."""
        return tuple(record for record in self.records if record.duration_seconds >= SLOW_REQUEST_SECONDS)

    @property
    def failed(self) -> tuple[RequestRecord, ...]:
        """Requests that did not get a 200."""
        return tuple(record for record in self.records if record.status != 200)


def _send(session: requests.Session, base_url: str, order_id: int, timeout_seconds: float) -> RequestRecord:
    started = time.monotonic()
    try:
        response = session.get(f"{base_url}/order/{order_id}", timeout=timeout_seconds)
        status, trace_id = response.status_code, response.headers.get("X-Trace-Id", "")
    except requests.RequestException:
        status, trace_id = 0, ""
    return RequestRecord(order_id, status, trace_id, time.monotonic() - started)


def run_scenario(
    base_url: str,
    request_count: int,
    rate_per_second: float,
    seed: int,
    concurrency: int = 8,
    timeout_seconds: float = 15.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ScenarioResult:
    """Send ``request_count`` ``GET /order/<id>`` requests at ``rate_per_second``, ids drawn from a seeded generator."""
    rng = random.Random(seed)
    order_ids = [rng.randint(1, ORDER_COUNT) for _ in range(request_count)]
    started_at = time.time()
    begin = clock()
    futures: list[Future[RequestRecord]] = []
    with requests.Session() as session, ThreadPoolExecutor(max_workers=concurrency) as pool:
        for index, order_id in enumerate(order_ids):
            wait = begin + index / rate_per_second - clock()
            if wait > 0:
                sleep(wait)
            futures.append(pool.submit(_send, session, base_url, order_id, timeout_seconds))
        records = tuple(future.result() for future in futures)
    return ScenarioResult(records, started_at, time.time())
