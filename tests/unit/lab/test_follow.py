import json

import pytest

from three_signals.lab.follow import (
    follow_slow_request,
    follow_with_patience,
    has_both_services,
    log_trace_id,
    render,
    slowest_database_span,
)
from three_signals.lab.stores import Exemplar, LogLine, SpanView, StoreError

TRACE_ID = "a" * 32


def _span(name, duration, statement="", service="back"):
    return SpanView(
        name, service, name[:4] + str(duration), "", duration, {"db.statement": statement} if statement else {}
    )


class Metrics:
    def __init__(self, points=((100.0, 0.008), (160.0, 1.4), (220.0, 0.01)), exemplars=None):
        self.points = list(points)
        self.found = (
            exemplars if exemplars is not None else [Exemplar("b" * 32, 0.01, 90.0), Exemplar(TRACE_ID, 1.502, 161.0)]
        )

    def query_range(self, expression, start, end, step_seconds):
        return self.points

    def exemplars(self, selector, start, end):
        return self.found


class Traces:
    def wait_for_trace(self, trace_id, timeout_seconds=60.0, until=None):
        return [
            _span("GET /order/{id}", 1520, service="front"),
            _span("SELECT", 2, "SELECT * FROM orders WHERE id = %s"),
            _span("SELECT", 1502, "SELECT oi.id FROM order_items AS oi"),
        ]


class Logs:
    def __init__(self, lines=None):
        self.lines = (
            lines
            if lines is not None
            else [
                LogLine(1, json.dumps({"msg": "order_items query started", "trace_id": TRACE_ID}), {}, {}),
                LogLine(
                    2, json.dumps({"msg": "slow query finished", "trace_id": TRACE_ID}), {}, {"trace_id": TRACE_ID}
                ),
            ]
        )
        self.asked = []

    def query(self, logql, start, end, limit=1000):
        self.asked.append(logql)
        return self.lines


def test_the_walk_takes_the_slowest_exemplar_and_finds_the_slow_database_span_and_its_lines():
    logs = Logs()
    result = follow_slow_request(Metrics(), Traces(), logs, now=300.0)
    assert result.peak_p95_seconds == 1.4
    assert result.exemplar.trace_id == TRACE_ID
    assert result.slow_span.duration_ms == 1502
    assert result.services_in_trace == {"front", "back"}
    assert logs.asked == [f'{{service_name="back"}} | trace_id="{TRACE_ID}"']
    assert result.reverse_trace_id == TRACE_ID


def test_without_p95_data_the_trail_goes_cold():
    with pytest.raises(StoreError, match="no data"):
        follow_slow_request(Metrics(points=()), Traces(), Logs(), now=300.0)


def test_without_a_slow_exemplar_the_trail_goes_cold():
    with pytest.raises(StoreError, match="no slow exemplar"):
        follow_slow_request(Metrics(exemplars=[Exemplar("b" * 32, 0.01, 90.0)]), Traces(), Logs(), now=300.0)


def test_without_log_lines_the_trail_goes_cold():
    with pytest.raises(StoreError, match="no log lines"):
        follow_slow_request(Metrics(), Traces(), Logs(lines=[]), now=300.0)


def test_the_slow_span_falls_back_to_any_select_when_no_statement_is_recorded():
    assert slowest_database_span([_span("SELECT", 5), _span("SELECT", 9), _span("GET", 50)]).duration_ms == 9
    with pytest.raises(StoreError):
        slowest_database_span([_span("GET", 50)])


def test_log_trace_id_prefers_structured_metadata_then_the_json_body():
    assert log_trace_id(LogLine(1, "{}", {}, {"trace_id": "x"})) == "x"
    assert log_trace_id(LogLine(1, '{"trace_id": "y"}', {}, {})) == "y"
    assert log_trace_id(LogLine(1, "plain text", {}, {})) == ""


def test_render_prints_five_numbered_clicks():
    text = render(follow_slow_request(Metrics(), Traces(), Logs(), now=300.0))
    clicks = [line for line in text.splitlines() if line[:2] in {"1.", "2.", "3.", "4.", "5."}]
    assert len(clicks) == 5
    assert "1502 ms" in text
    assert "the same trace" in text


def test_patience_retries_while_the_telemetry_is_on_its_way_and_then_succeeds():
    class Late(Metrics):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def query_range(self, expression, start, end, step_seconds):
            self.calls += 1
            return [] if self.calls < 3 else self.points

    metrics = Late()
    naps: list[float] = []
    clock = iter(range(0, 1000, 5))
    result = follow_with_patience(
        metrics, Traces(), Logs(), 60, pause_seconds=5, clock=lambda: next(clock), sleep=naps.append
    )
    assert result.exemplar.trace_id == TRACE_ID
    assert naps == [5, 5]


def test_patience_gives_up_with_the_last_error_after_the_deadline():
    clock = iter(range(0, 1000, 20))
    with pytest.raises(StoreError, match="no data"):
        follow_with_patience(
            Metrics(points=()), Traces(), Logs(), 30, clock=lambda: next(clock), sleep=lambda seconds: None
        )


def test_an_exemplar_whose_database_span_is_fast_is_skipped_for_one_whose_span_is_slow():
    class PerTrace(Traces):
        def wait_for_trace(self, trace_id, timeout_seconds=60.0, until=None):
            width = 1502 if trace_id == TRACE_ID else 40
            return [
                _span("GET /order/{id}", 9000, service="front"),
                _span("SELECT", width, "SELECT oi FROM order_items"),
            ]

    queued = Exemplar("c" * 32, 9.0, 150.0)
    metrics = Metrics(exemplars=[Exemplar(TRACE_ID, 1.502, 161.0), queued])
    result = follow_slow_request(metrics, PerTrace(), Logs(), now=300.0)
    assert result.exemplar.trace_id == TRACE_ID
    assert result.slow_span.duration_ms == 1502


def test_when_no_exemplar_has_a_slow_database_span_the_widest_one_is_reported():
    class AllFast(Traces):
        def wait_for_trace(self, trace_id, timeout_seconds=60.0, until=None):
            width = 300 if trace_id == TRACE_ID else 40
            return [_span("SELECT", width, "SELECT oi FROM order_items")]

    metrics = Metrics(exemplars=[Exemplar("c" * 32, 9.0, 150.0), Exemplar(TRACE_ID, 1.2, 161.0)])
    assert follow_slow_request(metrics, AllFast(), Logs(), now=300.0).exemplar.trace_id == TRACE_ID


def test_a_trace_counts_as_whole_for_the_walk_only_with_spans_from_both_services():
    front = SpanView("GET", "front", "a", "", 5.0, {})
    back = SpanView("GET", "back", "b", "a", 3.0, {})
    assert has_both_services([front, back])
    assert not has_both_services([front])
    assert not has_both_services([back])
