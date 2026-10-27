import base64

from three_signals.lab.stores import TempoClient, _id_to_hex

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
