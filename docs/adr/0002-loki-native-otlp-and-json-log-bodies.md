# 2. Send logs to Loki over its native OTLP endpoint, with the JSON line as the body

Status: accepted, 2026-10-27

## Context

The lesson's last click is from a span to exactly its log lines, and from a log line to its trace. That needs the trace
id to be a field the log store can filter on. The lesson's starting-point Collector config uses the `loki` exporter.

## Decision

The Collector exports logs with `otlphttp/loki` to `http://loki:3100/otlp`. Loki is configured with
`allow_structured_metadata: true`, so the OTLP `trace_id` and `span_id` become structured metadata and `service.name`
becomes the `service_name` label. The services attach the same `JsonFormatter` to the OTLP log handler as to stdout, so
the stored line is the JSON object and also contains `trace_id` and `span_id`.

## Alternatives considered

- **The `loki` exporter.** It rewrites each record into a JSON envelope (`body`, `traceid`, `attributes`), so the
  trace id is `traceid` and the application's own JSON is a string inside it. Loki ingests OTLP natively since 3.0, so the
  extra translation step buys nothing.
- **A log shipper reading container stdout (Promtail).** Works, but adds a component, and trace ids would be found only by
  parsing the line.

## Consequences

- Two queries find the same lines: `{service_name="back"} | trace_id="..."` (structured metadata) and
  `{service_name="back"} | json | trace_id="..."` (the body). The end-to-end test checks both agree.
- Grafana's Loki data source derives a link from the body's `"trace_id"` field to Tempo.
