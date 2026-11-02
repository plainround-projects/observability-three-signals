# Tempo (2.6.1)

## What it is

Tempo is a database for **traces** made by Grafana Labs. It accepts spans (here, over OTLP gRPC from the Collector), stores them cheaply in blocks on disk, and answers two kinds of question: "give me the trace with this id" and "find traces matching this condition" in a query language called **TraceQL**. This repository pins Tempo 2.6.1 and runs it as one process ("single binary") with its data on a local volume. That is enough for a laptop; a production Tempo is split into several services.

## Why this repository uses it

Tempo holds the traces: for each request, the spans from the front service, the back service and the database. Grafana opens a trace from an exemplar dot, and the lab tools find traces by id and by TraceQL search to prove that one request is one trace across two services.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/tempo/tempo.yaml` | Tempo's configuration. |
| `docker-compose.yml` (service `tempo`) | The pinned image, mounted config, data volume, published port and health check. |
| `observability/collector/config.yaml` | The exporter `otlp/tempo` that sends traces to Tempo (see `opentelemetry-collector.md`). |
| `observability/grafana/provisioning/datasources/datasources.yaml` | The Tempo data source (see `grafana.md`). |
| `src/three_signals/lab/stores.py` | `TempoClient` (see `requests.md`). |
| `observability/grafana/dashboards/three-signals.json` | Panel 6 queries Tempo with TraceQL. |

## How it works underneath

Tempo's pieces, all inside one process here: the **distributor** receives spans and hashes the trace id to choose an ingester; the **ingester** holds recent spans in memory and in a write-ahead log (`wal`, a file where every write is recorded first, so a crash loses nothing), and cuts a **block** after `max_block_duration`; the **compactor** merges small blocks and deletes blocks older than `block_retention`; the **querier** reads blocks and the ingester to answer queries.

A trace arrives in pieces, because each service exports its own spans separately. Tempo can therefore return a **partial trace** for a few seconds: for example the back service's spans without the front service's root span. Tools that need a whole trace (the `wait_for_trace` helper here) check that every span's parent is present.

TraceQL looks like `{ resource.service.name = "back" && duration > 1s }`. Braces hold conditions on one span; `resource.` reads attributes of the resource (the process), `span.` reads span attributes, `duration` is the span's length. The API `/api/search?q=...` runs TraceQL; `/api/traces/<id>` fetches one trace; `/ready` says whether Tempo is ready.

## Walkthrough of this repository's use

### `tempo.yaml`

```yaml
# Single-binary Tempo with local storage: enough for a laptop, not a production topology.
stream_over_http_enabled: true
server:
  http_listen_port: 3200
  log_level: warn
```

The comment says what this is: a single-binary Tempo, fine for a laptop. `stream_over_http_enabled: true` allows streaming search results over HTTP (Grafana uses it). The `server` block sets the HTTP port to 3200 (the API, the `/ready` endpoint) and the log level to `warn` so the container log stays short.

```yaml
distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
```

The `distributor` receives spans. Its `otlp` receiver listens for gRPC on `0.0.0.0:4317` inside the container. The Collector's exporter points at `tempo:4317`.

```yaml
ingester:
  max_block_duration: 2m

compactor:
  compaction:
    block_retention: 24h
```

The `ingester` cuts a block of recent traces every `max_block_duration: 2m` (short, so traces become queryable from storage soon; the default is 30 minutes). The `compactor` deletes blocks after `block_retention: 24h`.

```yaml
storage:
  trace:
    backend: local
    wal:
      path: /var/tempo/wal
    local:
      path: /var/tempo/blocks

usage_report:
  reporting_enabled: false
```

`storage.trace` uses `backend: local`, a folder on disk. `wal.path` is the write-ahead log folder and `local.path` is where finished blocks go; both are under `/var/tempo`, which Compose mounts from the volume `tempo-data`. `usage_report` with `reporting_enabled: false` stops Tempo from sending anonymous usage statistics.

### The Compose service

```yaml
  tempo:
    image: grafana/tempo:2.6.1
    command: ["-config.file=/etc/tempo/tempo.yaml"]
    volumes:
      - ./observability/tempo/tempo.yaml:/etc/tempo/tempo.yaml:ro
      - tempo-data:/var/tempo
    ports:
      - "127.0.0.1:${TEMPO_HOST_PORT:-23200}:3200"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3200/ready | grep -q ready"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

`command: ["-config.file=/etc/tempo/tempo.yaml"]` points Tempo at its configuration (mounted read-only). `tempo-data:/var/tempo` keeps traces between restarts. The port 3200 is published at host 23200 on the loopback address. The health check runs `wget -q -O - http://localhost:3200/ready | grep -q ready` in a shell (`CMD-SHELL`): `wget` fetches the page, the `|` pipe passes it to `grep -q ready`, which exits 0 only if the word `ready` is in it. This matters because right after start Tempo is running but not yet ready to take writes; checking the word `ready` in the answer, and not only that the port answers, avoids calling it healthy too early.

### TraceQL in this repository

```json
          "query": "{ resource.service.name = \"back\" && duration > 1s }",
```

`{ resource.service.name = "back" && duration > 1s }` selects spans of the back service that lasted more than one second. This is the "same slow requests, found from the trace side" panel.

## Try it yourself

```bash
curl -s http://127.0.0.1:23200/ready
```

Expected (needs `make up`): `ready`.

```bash
curl -s --get "http://127.0.0.1:23200/api/search" --data-urlencode 'q={ resource.service.name = "back" && duration > 1s }' --data-urlencode 'limit=3' | python3 -m json.tool | head -30
```

Expected (after `make run`): up to three traces, each with `traceID` and `durationMs`.

```bash
.venv/bin/three-signals-follow
```

Expected: five numbered lines; line 3 names the trace's services and the widest database span.

```bash
docker compose exec tempo ls /var/tempo
```

Expected: the folders `wal` and `blocks` (the latter appears after the first block is cut).

## Mistakes people make

- **Looking for the trace immediately.** Spans take seconds to flush and index.
- **Treating a partial trace as the whole.** Check that every parent exists.
- **Running the single binary in production.** It is for learning.
- **Forgetting that ids may be base64 or hex** depending on the API; the client here normalizes.

## Check yourself

1. What does Tempo store? *Traces (spans).*
2. What does the WAL protect? *Recent spans that are not yet in a block, in case of a crash.*
3. Why might a trace look incomplete right after the request? *The services export spans separately and Tempo receives them at slightly different times.*
4. What does `{ duration > 1s }` select? *Spans longer than one second.*
5. Which endpoint tells you Tempo is ready? *`/ready`.*

## Further reading

- Tempo 2.6 documentation: https://grafana.com/docs/tempo/v2.6.x/
- Configuration: https://grafana.com/docs/tempo/v2.6.x/configuration/
- TraceQL: https://grafana.com/docs/tempo/v2.6.x/traceql/
- HTTP API: https://grafana.com/docs/tempo/v2.6.x/api_docs/
