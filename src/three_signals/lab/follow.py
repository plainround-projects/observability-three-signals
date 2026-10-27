"""Follow one slow request from the latency panel to its trace to its log lines, the way the lesson's clicks do."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from three_signals.lab.stores import Exemplar, LogLine, SpanView, StoreError

P95_EXPRESSION = "histogram_quantile(0.95, sum by (le) (rate(order_assembly_duration_seconds_bucket[1m])))"
EXEMPLAR_SELECTOR = "order_assembly_duration_seconds_bucket"
SLOW_SPAN_SECONDS = 1.0


class MetricSource(Protocol):
    """The Prometheus calls the walk-through needs."""

    def query_range(self, expression: str, start: float, end: float, step_seconds: float) -> list[tuple[float, float]]:
        """Points of one series."""
        ...

    def exemplars(self, selector: str, start: float, end: float) -> list[Exemplar]:
        """Exemplars stored for a selector."""
        ...


class TraceSource(Protocol):
    """The Tempo calls the walk-through needs."""

    def wait_for_trace(self, trace_id: str, timeout_seconds: float = ...) -> list[SpanView]:
        """The spans of one trace."""
        ...


class LogSource(Protocol):
    """The Loki call the walk-through needs."""

    def query(self, logql: str, start: float, end: float, limit: int = ...) -> list[LogLine]:
        """Lines matching a LogQL query."""
        ...


@dataclass(frozen=True, slots=True)
class Investigation:
    """What each click found."""

    peak_p95_seconds: float
    peak_time: float
    exemplar: Exemplar
    spans: tuple[SpanView, ...]
    slow_span: SpanView
    log_lines: tuple[LogLine, ...]
    reverse_trace_id: str

    @property
    def services_in_trace(self) -> frozenset[str]:
        """The services that contributed spans to the trace."""
        return frozenset(span.service for span in self.spans)


def slowest_database_span(spans: list[SpanView]) -> SpanView:
    """The widest span that queried the order_items table."""
    candidates = [span for span in spans if "order_items" in str(span.attributes.get("db.statement", ""))]
    if not candidates:
        candidates = [span for span in spans if span.name.startswith("SELECT")]
    if not candidates:
        raise StoreError("the trace has no database span")
    return max(candidates, key=lambda span: span.duration_ms)


def log_trace_id(line: LogLine) -> str:
    """The trace id of a log line: its structured metadata, or the ``trace_id`` field of its JSON body."""
    if "trace_id" in line.structured_metadata:
        return str(line.structured_metadata["trace_id"])
    try:
        body: Mapping[str, object] = json.loads(line.line)
    except ValueError:
        return ""
    return str(body.get("trace_id", ""))


def follow_slow_request(
    metrics: MetricSource,
    traces: TraceSource,
    logs: LogSource,
    window_seconds: float = 900.0,
    now: float | None = None,
) -> Investigation:
    """Start at the p95 panel's peak, take the slowest exemplar, open its trace, then read its log lines."""
    end = now if now is not None else time.time()
    start = end - window_seconds
    points = metrics.query_range(P95_EXPRESSION, start, end, 5.0)
    if not points:
        raise StoreError("the p95 panel has no data in the window; run the scenario first")
    peak_time, peak_value = max(points, key=lambda point: point[1])

    exemplars = metrics.exemplars(EXEMPLAR_SELECTOR, start, end)
    slow_exemplars = [exemplar for exemplar in exemplars if exemplar.value >= SLOW_SPAN_SECONDS]
    if not slow_exemplars:
        raise StoreError("no slow exemplar found; was the slow-query fault enabled?")
    exemplar = max(slow_exemplars, key=lambda candidate: candidate.value)

    spans = traces.wait_for_trace(exemplar.trace_id)
    slow_span = slowest_database_span(spans)

    lines = logs.query(f'{{service_name="back"}} | trace_id="{exemplar.trace_id}"', start, end + 60)
    if not lines:
        raise StoreError(f"no log lines for trace {exemplar.trace_id}")
    return Investigation(
        peak_p95_seconds=peak_value,
        peak_time=peak_time,
        exemplar=exemplar,
        spans=tuple(spans),
        slow_span=slow_span,
        log_lines=tuple(lines),
        reverse_trace_id=log_trace_id(lines[0]),
    )


def follow_with_patience(
    metrics: MetricSource,
    traces: TraceSource,
    logs: LogSource,
    patience_seconds: float,
    pause_seconds: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Investigation:
    """Retry ``follow_slow_request`` while telemetry reaches the stores; raise the last error at the deadline."""
    deadline = clock() + patience_seconds
    while True:
        try:
            return follow_slow_request(metrics, traces, logs)
        except StoreError:
            if clock() >= deadline:
                raise
            sleep(pause_seconds)


def render(investigation: Investigation) -> str:
    """The investigation as numbered clicks, ready to paste into a runbook."""
    exemplar = investigation.exemplar
    lines = [
        f"1. Latency panel: p95 peaks at {investigation.peak_p95_seconds * 1000:.0f} ms "
        f"at {time.strftime('%H:%M:%S', time.localtime(investigation.peak_time))}.",
        f"2. Click the exemplar dot: trace {exemplar.trace_id}, a {exemplar.value * 1000:.0f} ms assembly.",
        f"3. The trace spans {', '.join(sorted(investigation.services_in_trace))} "
        f"with {len(investigation.spans)} spans; the widest database span is "
        f"'{investigation.slow_span.name}' at {investigation.slow_span.duration_ms:.0f} ms "
        f"({investigation.slow_span.attributes.get('db.statement', '')}).",
        f"4. Click 'logs for this span': {len(investigation.log_lines)} lines carry trace_id={exemplar.trace_id}:",
    ]
    lines.extend(f"     {line.line}" for line in investigation.log_lines)
    lines.append(
        f"5. Back the other way: the first log line's trace_id is {investigation.reverse_trace_id}, "
        f"{'the same trace' if investigation.reverse_trace_id == exemplar.trace_id else 'a different trace'}."
    )
    return "\n".join(lines)
