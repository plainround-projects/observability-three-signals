"""OpenTelemetry adapter for the order-assembly span and the duration histogram."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from opentelemetry.metrics import Meter
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from three_signals.application.ports import AssemblyScope
from three_signals.domain.errors import OrderNotFoundError
from three_signals.domain.orders import ItemCountBucket

SPAN_NAME = "assemble-order-response"
BUCKET_ATTRIBUTE = "order.item_count_bucket"
HISTOGRAM_NAME = "order_assembly_duration_seconds"
# Seconds, spread so a 1.5 s outlier lands in a bucket of its own; the SDK default is tuned for milliseconds.
HISTOGRAM_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class _Scope:
    def __init__(self, span: Span) -> None:
        self._span = span
        self.bucket: ItemCountBucket | None = None

    def record_bucket(self, bucket: ItemCountBucket) -> None:
        self.bucket = bucket
        self._span.set_attribute(BUCKET_ATTRIBUTE, str(bucket))


class OtelAssemblyTelemetry:
    """Wraps one assembly in a child span of the request and records its duration, with the size bucket."""

    def __init__(self, tracer: Tracer, meter: Meter, clock: Callable[[], float] = time.monotonic) -> None:
        """Take the tracer, the meter and an injectable monotonic clock."""
        self._tracer = tracer
        self._clock = clock
        self._duration = meter.create_histogram(
            HISTOGRAM_NAME,
            unit="s",
            description="Time to read an order and its items and assemble the response.",
            explicit_bucket_boundaries_advisory=HISTOGRAM_BUCKETS,
        )

    @contextmanager
    def measure(self) -> Iterator[AssemblyScope]:
        """Open the span, yield a scope, then record the duration inside the span so the sample links to it."""
        with self._tracer.start_as_current_span(
            SPAN_NAME, record_exception=False, set_status_on_exception=False
        ) as span:
            scope = _Scope(span)
            started = self._clock()
            try:
                yield scope
            except OrderNotFoundError:
                span.set_attribute("order.found", False)
                raise
            except Exception as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR, str(error)))
                raise
            finally:
                bucket = scope.bucket if scope.bucket is not None else "unknown"
                self._duration.record(self._clock() - started, attributes={BUCKET_ATTRIBUTE: str(bucket)})
