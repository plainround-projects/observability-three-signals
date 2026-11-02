# JSON

## What it is

JSON (JavaScript Object Notation) is a text format for data. It has objects (`{"key": value}`), arrays (`[1, 2, 3]`), strings in double quotes, numbers, `true`, `false` and `null`. It is the common language that programs use to talk over HTTP, and it is also the file format of Grafana dashboards. JSON has no comments and does not allow a trailing comma.

## Why this repository uses it

JSON appears at four levels: the **dashboard file** (Grafana's native format), the **HTTP bodies** between the services and in the store APIs, the **log lines** (one JSON object per line), and the **OTLP** data on the wire (protobuf in this repository, but JSON when you read a trace from Tempo).

## Where it appears

| Where | What |
| --- | --- |
| `observability/grafana/dashboards/three-signals.json` | The dashboard (see `grafana.md` for every field). |
| `src/three_signals/infrastructure/order_json.py` | The order document the services exchange. |
| `src/three_signals/infrastructure/logging_setup.py` | One JSON object per log line. |
| `src/three_signals/lab/stores.py` | Reads JSON from Prometheus, Tempo, Loki and Grafana. |
| `tests/observability/test_configuration_as_code.py` | Loads the dashboard with `json.loads`. |

## How it works underneath

A program that sends JSON turns an in-memory structure into text (**serializing**, `json.dumps` in Python). A program that receives it turns the text back into a structure (**parsing**, `json.loads`). Python maps JSON objects to `dict`, arrays to `list`, strings to `str`, numbers to `int` or `float`, `true`/`false` to `True`/`False` and `null` to `None`.

Keys of an object are always strings. Numbers have no size limit in the format, but some programs store them as 64-bit floats, so very large integers can lose precision. That is one reason ids that are long (such as the 128-bit trace id) are sent as hexadecimal strings.

**Structured logging** writes one JSON object per line ("JSON Lines"). Each line is self-describing, so a log store can search by field.

## Walkthrough of this repository's use

**The order document.** The back service answers `GET /order/3` with:

```python
def order_to_json(order: Order) -> dict[str, Any]:
    """Serialize an order the way the back service returns it."""
    return {
        "order": {
            "id": order.header.order_id.value,
            "customer": order.header.customer_name,
            "total_cents": order.total_cents,
            "item_count_bucket": str(order.item_count_bucket),
        },
        "items": [
            {
                "id": item.item_id,
                "sku": item.sku,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
            }
            for item in order.items
        ],
    }
```

The outer object has two keys: `order` (an object) and `items` (an array of objects). `jsonify` writes keys in sorted order, so a real response lists `items` before `order`; the order of keys carries no meaning.

**A log line.** The formatter in `logging_setup.py` builds a dict and writes it with `json.dumps`:

```python
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
            "logger": record.name,
            "service": self._service_name,
        }
```

Each key becomes a JSON key; `trace_id` and `span_id` are added when a span is active.

**The dashboard.** A dashboard is one JSON object with `panels` (an array). In JSON, quotes inside a string are escaped with a backslash, which is why a PromQL label matcher looks like this in the file:

```json
          "expr": "(sum(rate(order_assembly_duration_seconds_count[1m])) - sum(rate(order_assembly_duration_seconds_bucket{le=\"1.0\"}[1m]))) / sum(rate(order_assembly_duration_seconds_count[1m]))",
```

`{le=\"1.0\"}` is the text `{le="1.0"}`.

**Reading JSON from code.** The configuration test loads the dashboard and checks its structure:

```python
@pytest.fixture(scope="module")
def dashboard() -> dict[str, Any]:
    return dict(json.loads((OBSERVABILITY / "grafana" / "dashboards" / "three-signals.json").read_text()))
```

## Try it yourself

```bash
python3 -m json.tool observability/grafana/dashboards/three-signals.json | head -8
```

Expected: the file pretty-printed; the command fails with a position if the file is not valid JSON.

```bash
curl -s http://127.0.0.1:28081/order/3 | python3 -m json.tool
```

Expected (needs `make up`): the order with its items, indented.

```bash
docker compose logs back | tail -1 | python3 -m json.tool
```

Expected: one log line as an object with `timestamp`, `level`, `msg`, `service`, and for lines inside a request `trace_id` and `span_id`.

```bash
python3 -c "import json; print(json.dumps({'id': 3, 'name': None, 'ok': True}))"
```

Expected: `{"id": 3, "name": null, "ok": true}`: Python's `None` and `True` become `null` and `true`.

```bash
python3 -c "import json; json.loads('{\"a\": 1,}')"
```

Expected: a `JSONDecodeError` about the trailing comma.

## Mistakes people make

- **A trailing comma** or single quotes: invalid JSON.
- **Assuming key order** matters. It does not for the data's meaning.
- **Putting JSON in a string and forgetting to escape quotes.** Inside a JSON string every `"` must be written `\"`. The dashboard's PromQL expressions show this.
- **Hand-editing large JSON.** Dashboards here are code-reviewed and linted by tests.

## Check yourself

1. Which quotes does JSON require for strings? *Double quotes.*
2. Does JSON allow comments? *No.*
3. What does `json.dumps` do? *Turns a Python structure into JSON text.*
4. How do you write a double quote inside a JSON string? *With a backslash: `\"`.*
5. Why is a trace id sent as a hex string and not a number? *It is 128 bits, which many programs cannot hold exactly as a number.*

## Further reading

- JSON specification (RFC 8259): https://www.rfc-editor.org/rfc/rfc8259
- Python `json` module: https://docs.python.org/3.12/library/json.html
