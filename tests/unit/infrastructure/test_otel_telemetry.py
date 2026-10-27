import pytest

from three_signals.domain.errors import OrderNotFoundError
from three_signals.domain.orders import ItemCountBucket
from three_signals.infrastructure.otel_telemetry import (
    BUCKET_ATTRIBUTE,
    HISTOGRAM_NAME,
    SPAN_NAME,
    OtelAssemblyTelemetry,
)


class Ticking:
    """A clock that advances 0.25 s per reading."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 0.25
        return self.now


def _adapter(memory_telemetry):
    return OtelAssemblyTelemetry(
        memory_telemetry.tracer_provider.get_tracer("t"), memory_telemetry.meter_provider.get_meter("t"), Ticking()
    )


def _histogram_points(memory_telemetry):
    data = memory_telemetry.metric_reader.get_metrics_data()
    metrics = [m for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics]
    return next(m for m in metrics if m.name == HISTOGRAM_NAME).data.data_points


def test_a_successful_assembly_makes_a_span_with_the_bucket_and_one_histogram_sample(memory_telemetry):
    with _adapter(memory_telemetry).measure() as scope:
        scope.record_bucket(ItemCountBucket.THREE_TO_FIVE)
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert span.name == SPAN_NAME
    assert span.attributes[BUCKET_ATTRIBUTE] == "3-5"
    (point,) = _histogram_points(memory_telemetry)
    assert (point.count, point.sum) == (1, 0.25)
    assert point.attributes[BUCKET_ATTRIBUTE] == "3-5"


def test_the_histogram_sample_is_recorded_inside_the_span_so_it_can_carry_an_exemplar(memory_telemetry):
    with _adapter(memory_telemetry).measure() as scope:
        scope.record_bucket(ItemCountBucket.ONE_TO_TWO)
    (point,) = _histogram_points(memory_telemetry)
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert [exemplar.trace_id for exemplar in point.exemplars] == [span.context.trace_id]


def test_a_missing_order_is_not_an_error_span_and_is_counted_as_unknown_size(memory_telemetry):
    with pytest.raises(OrderNotFoundError), _adapter(memory_telemetry).measure():
        raise OrderNotFoundError(9)
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert span.attributes["order.found"] is False
    assert span.status.is_ok or span.status.status_code.name == "UNSET"
    assert _histogram_points(memory_telemetry)[0].attributes[BUCKET_ATTRIBUTE] == "unknown"


def test_an_unexpected_failure_marks_the_span_as_an_error_and_records_the_exception(memory_telemetry):
    with pytest.raises(RuntimeError), _adapter(memory_telemetry).measure():
        raise RuntimeError("database down")
    (span,) = memory_telemetry.span_exporter.get_finished_spans()
    assert span.status.status_code.name == "ERROR"
    assert [event.name for event in span.events] == ["exception"]
