import base64

import pytest

from three_signals.lab.stores import SpanView, StoreError, TempoClient, _id_to_hex, is_complete

TRACE_JSON = {
    "batches": [
        {
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "back"}}]},
            "scopeSpans": [
                {
                    "spans": [
                        {
                            "spanId": base64.b64encode(bytes.fromhex("00f067aa0ba902b7")).decode(),
                            "parentSpanId": "",
                            "name": "SELECT",
                            "startTimeUnixNano": "1000000000",
                            "endTimeUnixNano": "2502000000",
                            "attributes": [
                                {"key": "db.statement", "value": {"stringValue": "SELECT * FROM order_items"}}
                            ],
                        },
                        {
                            "spanId": "00f067aa0ba902b8",
                            "parentSpanId": "00f067aa0ba902b7",
                            "name": "child",
                            "startTimeUnixNano": "1000000000",
                            "endTimeUnixNano": "1001000000",
                            "attributes": [{"key": "n", "value": {"intValue": "3"}}],
                        },
                    ]
                }
            ],
        }
    ]
}


def test_ids_are_normalized_from_base64_and_from_hex():
    assert _id_to_hex(base64.b64encode(bytes.fromhex("00f067aa0ba902b7")).decode()) == "00f067aa0ba902b7"
    assert _id_to_hex("00F067AA0BA902B7") == "00f067aa0ba902b7"


def test_a_tempo_trace_document_is_flattened_into_span_views():
    wide, child = TempoClient.parse_trace(TRACE_JSON)
    assert (wide.name, wide.service, wide.duration_ms) == ("SELECT", "back", 1502.0)
    assert wide.span_id == "00f067aa0ba902b7"
    assert wide.attributes["db.statement"] == "SELECT * FROM order_items"
    assert child.parent_span_id == wide.span_id
    assert child.attributes["n"] == "3"


def _view(span_id, parent=""):
    return SpanView("s", "back", span_id, parent, 1.0, {})


def test_a_trace_is_complete_when_it_has_one_root_and_every_parent_is_present():
    assert is_complete([_view("a"), _view("b", "a"), _view("c", "b")])
    assert not is_complete([_view("b", "a")])
    assert not is_complete([_view("a"), _view("c", "x")])
    assert not is_complete([_view("a"), _view("z")])


def test_waiting_for_a_trace_returns_once_the_late_spans_have_arrived(monkeypatch):
    class Slow(TempoClient):
        def __init__(self):
            super().__init__("http://x")
            self.answers = [None, [_view("b", "a")], [_view("a"), _view("b", "a")]]

        def trace(self, trace_id):
            return self.answers.pop(0)

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert [span.span_id for span in Slow().wait_for_trace("t", 30)] == ["a", "b"]


def test_waiting_for_a_trace_that_never_appears_raises_and_one_that_stays_partial_returns_what_it_has(monkeypatch):
    class Never(TempoClient):
        def trace(self, trace_id):
            return None

    class Partial(TempoClient):
        def trace(self, trace_id):
            return [_view("b", "a")]

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with pytest.raises(StoreError):
        Never("http://x").wait_for_trace("t", 0.05)
    assert [span.span_id for span in Partial("http://x").wait_for_trace("t", 0.05)] == ["b"]
