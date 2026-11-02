# OpenTelemetry Collector (contrib 0.111.0)

## What it is

The OpenTelemetry Collector is a standalone program that receives telemetry, changes it, and sends it on. Applications send OTLP to the Collector at one address, and the Collector decides where each signal goes. That keeps the applications simple and lets you change backends without changing code. Its configuration is one YAML file with four kinds of building blocks:

- **receivers** accept data (for example `otlp`, which listens for OTLP over gRPC on port 4317 and over HTTP on port 4318);
- **processors** change data on the way (batch it, limit memory, drop or rewrite attributes);
- **exporters** send data out (to Tempo, Prometheus, Loki);
- **extensions** add side features that are not in the data path (a health check endpoint).

You choose the image `otel/opentelemetry-collector-contrib`, the distribution that includes the community components. This repository pins version 0.111.0.

## Why this repository uses it

The services speak one protocol (OTLP) to one address. The Collector is the single place that knows the three backends: traces go to Tempo, metrics are published for Prometheus to scrape, and logs go to Loki. The services never learn about Tempo, Prometheus or Loki. It also protects itself: if too much data arrives, the Collector refuses some instead of being killed.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/collector/config.yaml` | The whole Collector configuration. |
| `docker-compose.yml` (service `otel-collector`) | Runs the pinned image with that file mounted read-only and two published ports. |
| `Makefile` (`lint-config`) | Validates the file with the Collector's own `validate` command. |
| `tests/observability/test_configuration_as_code.py` | Checks the pipelines are well formed. |

## How it works underneath

Defining a component under `receivers:` or `exporters:` does **not** turn it on. Only components listed in `service.pipelines` run. A **pipeline** is a signal type (`traces`, `metrics` or `logs`) plus an ordered list of receivers, processors and exporters. Data enters from the receivers, passes through each processor in the written order, and is sent to every exporter.

Names can have a suffix after a slash (`otlp/tempo`, `otlphttp/loki`): the part before the slash is the component type, and the part after is a label so you can define the same type twice.

Inside the process, each pipeline is a chain of queues and workers. The `memory_limiter` processor checks memory use every `check_interval`. When use crosses the soft limit (`limit_mib` minus `spike_limit_mib`) it starts refusing new data, which tells the sender to retry later; this prevents the Linux out-of-memory killer from stopping the process. The `batch` processor collects items and sends them when `send_batch_size` is reached or `timeout` passes, so the backend gets a few big requests, not thousands of small ones.

The Collector also reports on itself: its own logs and metrics (`service.telemetry`), and a `health_check` extension that answers HTTP 200 on port 13133 when the process is running. The image has no shell or `wget`, so Docker cannot run a health-check command inside it; other tools call the extension from outside.

## Walkthrough of this repository's use

### `observability/collector/config.yaml`

```yaml
# One Collector between the services and the three backends: receivers -> processors -> exporters.
extensions:
  health_check:
    endpoint: 0.0.0.0:13133
```

The first comment is the whole design: receivers, then processors, then exporters. `extensions:` lists add-ons that are not in the data path. `health_check` starts a small HTTP server on `0.0.0.0:13133` (all interfaces inside the container) that answers 200 while the Collector runs. Compose publishes it on the host as port 23133. The image contains only the Collector program (no shell, no `wget`), so a Docker health check inside it is impossible; `make health` calls this endpoint from outside.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
```

The only receiver is `otlp`, listening on two protocols: **gRPC** on 4317 and **HTTP** on 4318. The services use HTTP (`OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318`). gRPC is also open for any client that prefers it. `0.0.0.0` makes the Collector reachable from other containers (a Collector that listens on `localhost` only would refuse them).

```yaml
processors:
  # First in every pipeline: shed load by refusing data instead of being killed by the kernel's out-of-memory killer.
  memory_limiter:
    check_interval: 1s
    limit_mib: 384
    spike_limit_mib: 96
  # Last before the exporters: one request per second per signal instead of one per span.
  batch:
    timeout: 1s
    send_batch_size: 512
```

Two processors. The comment on `memory_limiter` explains its job. `check_interval: 1s` is how often it measures memory; `limit_mib: 384` is the hard limit; `spike_limit_mib: 96` is how much memory use can rise between two checks. The soft limit is `limit_mib - spike_limit_mib` = 288 MiB; above it the Collector starts refusing data. `batch` groups items: it sends a batch when it holds 512 items (`send_batch_size`) or after 1 second (`timeout`), whichever comes first.

```yaml
exporters:
  otlp/tempo:
    endpoint: tempo:4317
    tls:
      insecure: true
```

The exporter `otlp/tempo` sends traces to `tempo:4317` (Tempo's OTLP gRPC port, reached by its Compose service name). `tls: insecure: true` means plain text, no encryption: acceptable between two containers on a private laptop network, never across the internet.

```yaml
  prometheus:
    endpoint: 0.0.0.0:8889
    # Exemplars (the trace id attached to a histogram sample) are only exposed in the OpenMetrics format.
    enable_open_metrics: true
    # service.name and friends become labels, so a panel can filter by service_name.
    resource_to_telemetry_conversion:
      enabled: true
```

The `prometheus` exporter does **not** push anywhere. It opens port 8889 and publishes the metrics in Prometheus format for Prometheus to scrape. `enable_open_metrics: true` selects the OpenMetrics text format, which is the only format that carries exemplars. `resource_to_telemetry_conversion` copies the resource attributes (`service.name` and others) onto every metric as labels, so panels can filter by `service_name`.

```yaml
  # Loki ingests OTLP natively: service.name becomes the service_name label, the trace and span ids become
  # structured metadata, and the log body (our JSON line) is stored as the line.
  otlphttp/loki:
    endpoint: http://loki:3100/otlp
```

`otlphttp/loki` sends logs as OTLP over HTTP to Loki's native endpoint `http://loki:3100/otlp`. Loki turns `service.name` into the label `service_name`, stores the trace and span ids as structured metadata, and keeps the log body, our JSON line, as the line. (The older `loki` exporter is marked deprecated for logs in this Collector version.)

```yaml
service:
  extensions: [health_check]
  telemetry:
    logs:
      level: info
    metrics:
      level: basic
      address: 0.0.0.0:8888
```

`service:` is where components are switched on. `extensions: [health_check]` enables the extension. `telemetry` configures the Collector's own diagnostics: logs at `info`, metrics at level `basic`, published on `0.0.0.0:8888` (Prometheus scrapes them as the job `otel-collector-internal`, to show accepted and refused spans and exporter queue sizes).

```yaml
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/tempo]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheus]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/loki]
```

Three **pipelines**, one per signal, each: `receivers: [otlp]` (the same receiver feeds all three), `processors: [memory_limiter, batch]` in that order, and its own exporter: traces to `otlp/tempo`, metrics to `prometheus`, logs to `otlphttp/loki`. A test checks that every name a pipeline uses is defined, that `memory_limiter` comes first and `batch` last.

### The Compose service and the Makefile check

```yaml
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.111.0
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      - ./observability/collector/config.yaml:/etc/otelcol/config.yaml:ro
    ports:
      - "127.0.0.1:${OTLP_HTTP_HOST_PORT:-24318}:4318"
      - "127.0.0.1:${COLLECTOR_HEALTH_HOST_PORT:-23133}:13133"
    depends_on:
      tempo: { condition: service_healthy }
      prometheus: { condition: service_healthy }
      loki: { condition: service_healthy }
```

`image: otel/opentelemetry-collector-contrib:0.111.0` pins the version. `command: ["--config=/etc/otelcol/config.yaml"]` tells the program where to read its configuration. The configuration is a bind mount of the file in the repository, read-only (`:ro`). The two ports are published on the loopback address only: OTLP/HTTP (container 4318, host 24318) and the health endpoint (container 13133, host 23133). `depends_on` with `service_healthy` waits for Tempo, Prometheus and Loki so the Collector does not start sending to backends that are not ready.

```makefile
# promtool and the Collector validate their own files, so a typo fails here and not at 3 a.m.
lint-config: ## promtool check on the Prometheus config and rules; the Collector validates its config
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  check config /etc/prometheus/prometheus.yml
	docker run --rm -v "$(CURDIR)/observability/collector/config.yaml:/etc/otelcol/config.yaml:ro" $(COLLECTOR_IMAGE) \
	  validate --config=/etc/otelcol/config.yaml
```

`make lint-config` runs the Collector image once with the command `validate --config=...` which parses the file, checks that every component exists in that distribution and that the pipelines are wired correctly, and exits non-zero if not. `--rm` deletes the container afterwards. See `make.md` for the rest of the syntax.

## Try it yourself

```bash
docker run --rm -v "$PWD/observability/collector/config.yaml:/etc/otelcol/config.yaml:ro" otel/opentelemetry-collector-contrib:0.111.0 validate --config=/etc/otelcol/config.yaml; echo "exit $?"
```

Expected: `exit 0` and no error text.

```bash
curl -s http://127.0.0.1:23133/
```

Expected (needs `make up`): `{"status":"Server available", ...}`.

```bash
curl -s http://127.0.0.1:8889/metrics | head
```

Expected: this port is not published to your machine, so the command fails. Use `docker compose exec prometheus wget -q -O - http://otel-collector:8889/metrics | head` to read it from inside the network; you will see `order_assembly_duration_seconds_bucket` lines after traffic.

```bash
docker compose logs otel-collector | tail -5
```

Expected: start-up lines such as `Everything is ready. Begin running and processing data.`

## Mistakes people make

- **Defining a component but not adding it to a pipeline.** It then does nothing and no error is shown.
- **Wrong processor order.** `memory_limiter` must be first, `batch` last.
- **Using `localhost` as an exporter endpoint** inside a container. Use the service name.
- **Forgetting `tls: insecure: true`** for a plain-text local gRPC endpoint.
- **Using a deprecated exporter.** The `loki` exporter is deprecated for logs in 0.111.0; this repository sends logs to Loki's native OTLP endpoint with `otlphttp`.

## Check yourself

1. What are the four kinds of Collector components? *Receivers, processors, exporters and extensions.*
2. What makes a defined component actually run? *Listing it in a pipeline (extensions: under `service.extensions`).*
3. Why is `memory_limiter` first? *So it can refuse data before the other processors use more memory.*
4. What does `batch` do? *It groups items into larger requests, sent by size or timeout.*
5. Why does `otlp/tempo` have a slash? *The suffix lets you define several components of the same type.*

## Further reading

- Collector documentation: https://opentelemetry.io/docs/collector/
- Configuration: https://opentelemetry.io/docs/collector/configuration/
- Components list (contrib v0.111.0): https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.111.0
- `memory_limiter` processor: https://github.com/open-telemetry/opentelemetry-collector/tree/v0.111.0/processor/memorylimiterprocessor
- `batch` processor: https://github.com/open-telemetry/opentelemetry-collector/tree/v0.111.0/processor/batchprocessor
