# Loki (3.1.1)

## What it is

Loki is a database for **logs** made by Grafana Labs. Unlike a full-text search engine it does not index the text of each line. It indexes only a small set of **labels** per log stream (here `service_name`) and stores the line text compressed in chunks. Queries use **LogQL**: you pick streams with label matchers, then filter lines. This repository pins Loki 3.1.1 as one process with local storage.

## Why this repository uses it

Loki holds the logs, and it is where the third signal links back to the first two. Every JSON log line the back service writes during a request carries that request's trace id. Loki stores it as structured metadata, so one LogQL filter (`| trace_id="..."`) returns exactly the lines of one trace, and Grafana can make a log line's `trace_id` a link to the trace.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/loki/loki.yaml` | Loki's configuration. |
| `docker-compose.yml` (service `loki`) | The pinned image, config, data volume, port and health check. |
| `observability/collector/config.yaml` | The exporter `otlphttp/loki` (see `opentelemetry-collector.md`). |
| `observability/grafana/provisioning/datasources/datasources.yaml` | The Loki data source with a derived field (see `grafana.md`). |
| `src/three_signals/lab/stores.py` | `LokiClient` (see `requests.md`). |
| `src/three_signals/lab/follow.py` | The query for a trace's lines. |
| `observability/grafana/dashboards/three-signals.json` | Panel 5, the back service's log lines. |

## How it works underneath

A **stream** is all the lines that share exactly the same labels. The OTLP route is Loki's native one: the Collector posts to `/otlp`, and Loki turns the resource attribute `service.name` into the label `service_name`. Other fields of the log record (the trace id and span id) are stored as **structured metadata**: per-line key/value pairs that are *not* labels, so they do not create new streams, yet you can still filter on them. Structured metadata needs `allow_structured_metadata: true` and the schema version v13 with the TSDB index.

`schema_config` says how data is laid out over time. A new config entry applies from a date. `store: tsdb` is the index type, `object_store: filesystem` puts chunks in a folder, and `period: 24h` creates a new index table every day.

LogQL: `{service_name="back"}` selects a stream. `| trace_id="abc"` filters by a structured metadata field. `| json` would parse the line as JSON. `|= "text"` filters by substring. The HTTP API `/loki/api/v1/query_range` returns results over a time range; the request header `X-Loki-Response-Encoding-Flags: categorize-labels` asks Loki to include structured metadata in the answer.

`auth_enabled: false` turns off multi-tenancy, so no tenant header is needed.

## Walkthrough of this repository's use

### `loki.yaml`

```yaml
# Single-binary Loki with local storage. Structured metadata must be on to receive the OTLP trace and span ids.
auth_enabled: false

server:
  http_listen_port: 3100
  log_level: warn
```

The comment states the one setting that is easy to forget: structured metadata must be on. `auth_enabled: false` turns off multi-tenancy. `server` sets the HTTP port to 3100 and the log level to `warn`.

```yaml
common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
```

`common` holds settings shared by Loki's internal parts. `instance_addr: 127.0.0.1` is the address Loki advertises to itself. `path_prefix: /loki` is the base folder (a volume in Compose). `replication_factor: 1` keeps one copy of the data. The `ring` is how several Loki instances agree who owns what; with `store: inmemory` a single instance keeps that in memory. `storage.filesystem` puts chunks (compressed log data) in `/loki/chunks` and rules in `/loki/rules`.

```yaml
schema_config:
  configs:
    - from: "2024-04-01"
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
```

`schema_config` describes the on-disk layout. One entry, valid `from` 1 April 2024: `store: tsdb` is the index type, `object_store: filesystem` keeps chunks in a folder, `schema: v13` is the schema version (required for structured metadata), and the index tables are named with prefix `index_` and rotate every `24h`.

```yaml
limits_config:
  allow_structured_metadata: true
  volume_enabled: true

analytics:
  reporting_enabled: false
```

`limits_config.allow_structured_metadata: true` is the key switch: without it Loki rejects log records that carry structured metadata, which is how OTLP sends the trace id and span id. `volume_enabled: true` enables the volume API that Grafana's log explorer uses. `analytics.reporting_enabled: false` turns off anonymous usage reports.

### The Compose service

```yaml
  loki:
    image: grafana/loki:3.1.1
    command: ["-config.file=/etc/loki/loki.yaml"]
    volumes:
      - ./observability/loki/loki.yaml:/etc/loki/loki.yaml:ro
      - loki-data:/loki
    ports:
      - "127.0.0.1:${LOKI_HOST_PORT:-23100}:3100"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3100/ready | grep -q ready"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

The same pattern as Tempo: pinned image, `-config.file` flag, config bind-mounted read-only, a data volume at `/loki`, host port 23100 on loopback and a `wget ... | grep -q ready` health check on `/ready`.

### LogQL in this repository

```python
    lines = logs.query(f'{{service_name="back"}} | trace_id="{exemplar.trace_id}"', start, end + 60)
```

The f-string builds `{service_name="back"} | trace_id="<id>"`. The braces in the Python source are doubled (`{{ }}`) to write literal braces. `{service_name="back"}` selects the stream of the back service (the label `service_name` comes from the OTLP resource). `| trace_id="..."` is a label filter on structured metadata. The result is exactly the lines written during that trace.

```json
          "expr": "{service_name=\"back\"}",
```

Panel 5 selects all lines of the back service; each line is JSON, and Grafana shows its `trace_id` as a link (see `grafana.md`).

## Try it yourself

```bash
curl -s http://127.0.0.1:23100/ready
```

Expected (needs `make up`): `ready`.

```bash
curl -s --get "http://127.0.0.1:23100/loki/api/v1/labels"
```

Expected: JSON with `"data"` containing labels such as `service_name`.

```bash
curl -s --get "http://127.0.0.1:23100/loki/api/v1/query_range" --data-urlencode 'query={service_name="back"}' --data-urlencode 'limit=2' -H 'X-Loki-Response-Encoding-Flags: categorize-labels' | python3 -m json.tool | head -40
```

Expected (after traffic): entries with the JSON log line and a `structuredMetadata` object holding `trace_id` and `span_id`. Run it again without the `-H ...` header and the structured metadata disappears from the answer.

```bash
.venv/bin/python -m pytest tests/e2e -m e2e -k step_4 -q
```

Expected (needs `make up`): `1 passed`, proving that one trace finds exactly its log lines and a log line finds its trace.

## Mistakes people make

- **Turning high-cardinality values into labels** (trace ids, user ids). Use structured metadata or keep them in the line.
- **Forgetting `allow_structured_metadata`**, so the OTLP trace ids are rejected.
- **Querying a time range with no data** and thinking Loki is broken. Always give start and end.
- **Not asking for categorized labels**, then missing structured metadata in API results.

## Check yourself

1. What does Loki index? *Only the labels of each stream, not the text of the lines.*
2. What is structured metadata? *Per-line key/value pairs stored beside the line, not part of the stream's labels.*
3. How does `service.name` reach Loki as a label? *Loki's OTLP endpoint converts it to `service_name`.*
4. What does `{service_name="back"} | trace_id="x"` do? *Selects the back service's stream and keeps lines whose trace id is `x`.*
5. Why is `auth_enabled` false here? *A single-user lab needs no tenant separation.*

## Further reading

- Loki 3.1 documentation: https://grafana.com/docs/loki/v3.1.x/
- Configuration: https://grafana.com/docs/loki/v3.1.x/configure/
- Structured metadata: https://grafana.com/docs/loki/v3.1.x/get-started/labels/structured-metadata/
- OTLP ingestion: https://grafana.com/docs/loki/v3.1.x/send-data/otel/
- LogQL: https://grafana.com/docs/loki/v3.1.x/query/
- HTTP API: https://grafana.com/docs/loki/v3.1.x/reference/loki-http-api/
