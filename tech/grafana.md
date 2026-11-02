# Grafana (11.2.0)

## What it is

Grafana is a web application that draws dashboards from data stores. It stores no telemetry itself. You connect **data sources** (Prometheus, Tempo, Loki), and each dashboard **panel** runs a query against one data source and draws the result as a graph, a table or a list of log lines. Grafana can also link data sources: a click on an exemplar dot opens a trace in Tempo; a click on "logs for this span" runs a Loki query. This repository pins Grafana 11.2.0.

## Why this repository uses it

Grafana is where the three signals meet. The lesson's click path happens here: the latency panel shows a spike, a click on an exemplar dot opens the trace in Tempo, "logs for this span" opens the lines in Loki, and a `trace_id` in a log line opens the trace again. Everything is configured by files (data sources and the dashboard), so a fresh `make up` always gives the same Grafana.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/grafana/provisioning/datasources/datasources.yaml` | Creates the three data sources and the links between them. |
| `observability/grafana/provisioning/dashboards/dashboards.yaml` | Tells Grafana to load dashboard files from a folder. |
| `observability/grafana/dashboards/three-signals.json` | The dashboard: six panels. |
| `docker-compose.yml` (service `grafana`) | The pinned image, mounts, admin password, health check. |
| `src/three_signals/lab/stores.py` (`GrafanaClient`) | Asks Grafana to test each data source. |
| `tests/observability/test_configuration_as_code.py` | Lints the data sources and the dashboard. |

## How it works underneath

Grafana runs as one server process with a built-in web UI, a small database (SQLite in `/var/lib/grafana`) for its own settings, and an HTTP API. The browser sends requests to the Grafana server, and the server forwards the queries to the data sources (`access: proxy`), so the browser never needs to reach Prometheus directly.

**Provisioning** means Grafana reads YAML files from `/etc/grafana/provisioning` at start and creates data sources and dashboard providers from them, instead of someone clicking in the UI. A **dashboard provider** tells Grafana a folder where it should load dashboard JSON files. The result is the same on every start, and everything is code that can be reviewed.

Settings can come from environment variables named `GF_<SECTION>_<KEY>` (for example `GF_SECURITY_ADMIN_PASSWORD`).

A dashboard JSON has a `uid` (its stable id), a list of `panels`, each with a `gridPos` (x, y, width, height on a 24-column grid), `targets` (the queries), and display options. `schemaVersion` records the structure version Grafana expects.

Grafana's data-source API: `GET /api/datasources/uid/<uid>/health` asks a data source to test itself, and `GET /api/datasources/proxy/uid/<uid>/<path>` passes a request through to that data source.

## Walkthrough of this repository's use

### `datasources.yaml`

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
```

`apiVersion: 1` is the provisioning file format. Each data source has a `name` (shown in the UI), a `uid` (the stable id other files refer to), a `type` (the plug-in: `prometheus`), `access: proxy` (Grafana's server makes the queries), the `url` inside the Compose network, and `isDefault: true` (panels that name no data source use this one).

```yaml
    jsonData:
      httpMethod: POST
      # An exemplar's trace_id label opens that trace in Tempo: the first click of the investigation.
      exemplarTraceIdDestinations:
        - name: trace_id
          datasourceUid: tempo
```

`jsonData` holds type-specific options. `httpMethod: POST` sends queries as POST so long expressions do not hit URL length limits. `exemplarTraceIdDestinations` is the link that makes exemplar dots clickable: an exemplar's label named `trace_id` is opened in the data source with uid `tempo`.

```yaml
  - name: Tempo
    uid: tempo
    type: tempo
    access: proxy
    url: http://tempo:3200
    jsonData:
      # "Logs for this span": Loki lines of the same service and trace id, within a minute of the span.
      tracesToLogsV2:
        datasourceUid: loki
        spanStartTimeShift: "-1m"
        spanEndTimeShift: "1m"
        customQuery: true
        query: '{service_name="${__span.tags["service.name"]}"} | trace_id="${__span.traceId}"'
      tracesToMetrics:
        datasourceUid: prometheus
      nodeGraph:
        enabled: true

```

The Tempo data source (`http://tempo:3200`). `tracesToLogsV2` configures the "Logs for this span" button: `datasourceUid: loki` is where to go; `spanStartTimeShift: "-1m"` and `spanEndTimeShift: "1m"` widen the time range by a minute on each side of the span; `customQuery: true` allows a hand-written query; and `query` is a Loki query with Grafana variables: `${__span.tags["service.name"]}` is the span's service name and `${__span.traceId}` its trace id, so the button runs `{service_name="back"} | trace_id="<id>"`. `tracesToMetrics` links to the Prometheus data source. `nodeGraph.enabled: true` shows a service graph of the trace.

```yaml
  - name: Loki
    uid: loki
    type: loki
    access: proxy
    url: http://loki:3100
    jsonData:
      # A log line's trace_id opens that trace in Tempo: the click back the other way.
      derivedFields:
        - name: trace_id
          matcherRegex: '"trace_id":\s*"([0-9a-f]+)"'
          url: "$${__value.raw}"
          datasourceUid: tempo
          urlDisplayLabel: Open trace
```

The Loki data source. `derivedFields` define links inside a log line: `matcherRegex` is a regular expression that finds the value in the line (`"trace_id": "<hex digits>"`; the parentheses capture the id), `url: "$${__value.raw}"` passes the captured value on (the doubled `$$` stops Grafana's provisioning from treating it as an environment variable), `datasourceUid: tempo` says to open it in Tempo, and `urlDisplayLabel` is the link text. This is the click in the other direction: from a log line to its trace.

### `dashboards.yaml`

```yaml
apiVersion: 1

providers:
  - name: three-signals
    type: file
    disableDeletion: true
    allowUiUpdates: false
    options:
      path: /etc/grafana/dashboards
```

A **dashboard provider** named `three-signals` of `type: file` reads JSON files from `path: /etc/grafana/dashboards` (a read-only mount of `observability/grafana/dashboards`). `disableDeletion: true` stops anyone from deleting provisioned dashboards in the UI; `allowUiUpdates: false` stops saved edits.

### `three-signals.json`: the dashboard

A dashboard is one JSON object. The top level:

```json
{
  "uid": "three-signals",
  "title": "Three signals",
  "description": "From a latency spike to its trace to its logs.",
  "tags": [
    "observability",
    "opentelemetry"
  ],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "10s",
  "time": {
    "from": "now-15m",
    "to": "now"
  },
  "editable": false,
  "panels": [
```

`uid` is the stable id (the URL is `/d/three-signals`); `title`, `description` and `tags` are for humans and search. `timezone: "browser"` shows the viewer's time zone. `schemaVersion: 39` is the structure version for Grafana 11.2 and `version: 1` the dashboard's own revision. `refresh: "10s"` reloads every 10 seconds. `time` is the default range: the last 15 minutes. `editable: false` is a second guard against UI edits. `panels` starts the list.

**Panel 1, the p95 with exemplars.**

```json
    {
      "id": 1,
      "type": "timeseries",
      "title": "Order assembly p95 (seconds)",
      "description": "Start here. The dots are exemplars: click one on the spike to open the trace of a request that landed in that latency bucket.",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 0,
        "y": 0,
        "w": 12,
        "h": 9
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "histogram_quantile(0.95, sum by (le) (rate(order_assembly_duration_seconds_bucket[1m])))",
          "legendFormat": "p95",
          "exemplar": true
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "s",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      }
    },
```

`id` identifies the panel (the test checks that ids are unique). `type: "timeseries"` is a line graph. The `datasource` object names the data source by `type` and `uid`. `gridPos` places it on a 24-column grid: `x: 0, y: 0, w: 12, h: 9` is the top-left half, nine rows high. `targets` is the list of queries: `refId` names the query, `expr` is the PromQL (see `promql.md`), `legendFormat` is the legend text, and `"exemplar": true` asks Prometheus for exemplars along with the line. `fieldConfig.defaults.unit: "s"` shows values as seconds; `custom` sets the line width and the fill under the line; `overrides` is an empty list of per-series exceptions. `options` configures the legend (a list at the bottom) and the tooltip (`multi` shows all series).

**Panel 2, the share of slow assemblies.**

```json
    {
      "id": 2,
      "type": "timeseries",
      "title": "Share of assemblies slower than 1 s",
      "description": "The signal OrderAssemblySlowShareHigh alerts on (above 2% for a minute).",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 12,
        "y": 0,
        "w": 12,
        "h": 9
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "(sum(rate(order_assembly_duration_seconds_count[1m])) - sum(rate(order_assembly_duration_seconds_bucket{le=\"1.0\"}[1m]))) / sum(rate(order_assembly_duration_seconds_count[1m]))",
          "legendFormat": "slower than 1 s"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "percentunit",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      }
    },
```

Same structure at `x: 12, y: 0`. The expression is the alert's ratio over one minute. The unit `percentunit` turns 0.05 into 5%.

**Panel 3, assemblies per second by order size.**

```json
    {
      "id": 3,
      "type": "timeseries",
      "title": "Assemblies per second by order size",
      "description": "order.item_count_bucket has four possible values, so it is safe as a metric label.",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 0,
        "y": 9,
        "w": 12,
        "h": 8
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "sum by (order_item_count_bucket) (rate(order_assembly_duration_seconds_count[1m]))",
          "legendFormat": "{{order_item_count_bucket}} items"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "reqps",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      }
    },
```

At `x: 0, y: 9`. `legendFormat: "{{order_item_count_bucket}} items"` fills the legend from the label value. Unit `reqps` is requests per second.

**Panel 4, HTTP requests per second by service.**

```json
    {
      "id": 4,
      "type": "timeseries",
      "title": "HTTP server requests per second by service",
      "description": "",
      "datasource": {
        "type": "prometheus",
        "uid": "prometheus"
      },
      "gridPos": {
        "x": 12,
        "y": 9,
        "w": 12,
        "h": 8
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "prometheus",
            "uid": "prometheus"
          },
          "expr": "sum by (service_name) (rate(http_server_duration_milliseconds_count[1m]))",
          "legendFormat": "{{service_name}}"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "reqps",
          "custom": {
            "lineWidth": 2,
            "fillOpacity": 8
          }
        },
        "overrides": []
      },
      "options": {
        "legend": {
          "displayMode": "list",
          "placement": "bottom"
        },
        "tooltip": {
          "mode": "multi"
        }
      }
    },
```

At `x: 12, y: 9`. It uses the metric produced by the Flask instrumentation, grouped by `service_name`.

**Panel 5, the back service's logs.**

```json
    {
      "id": 5,
      "type": "logs",
      "title": "Back service logs",
      "description": "Each line is JSON with trace_id and span_id; a trace_id here is a link to the trace.",
      "datasource": {
        "type": "loki",
        "uid": "loki"
      },
      "gridPos": {
        "x": 0,
        "y": 17,
        "w": 12,
        "h": 10
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "loki",
            "uid": "loki"
          },
          "expr": "{service_name=\"back\"}",
          "queryType": "range"
        }
      ],
      "options": {
        "showTime": true,
        "wrapLogMessage": true,
        "enableLogDetails": true,
        "sortOrder": "Descending"
      }
    },
```

`type: "logs"` lists log lines. The data source is Loki. `queryType: "range"` asks for a time range. `options` show the time, wrap long lines, allow expanding a line (`enableLogDetails`, which is where the derived `trace_id` link appears) and sort newest first.

**Panel 6, slow traces.**

```json
    {
      "id": 6,
      "type": "table",
      "title": "Slow traces (over 1 s)",
      "description": "The same slow requests, found from the trace side.",
      "datasource": {
        "type": "tempo",
        "uid": "tempo"
      },
      "gridPos": {
        "x": 12,
        "y": 17,
        "w": 12,
        "h": 10
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "tempo",
            "uid": "tempo"
          },
          "queryType": "traceql",
          "query": "{ resource.service.name = \"back\" && duration > 1s }",
          "limit": 20,
          "tableType": "traces"
        }
      ]
    }
  ],
  "templating": {
    "list": []
  },
  "annotations": {
    "list": []
  }
}
```

`type: "table"` with the Tempo data source and `queryType: "traceql"`. `limit: 20` caps the rows and `tableType: "traces"` lists whole traces. The last lines are empty `templating` (no variables) and `annotations`.

### The Compose service

```yaml
  grafana:
    image: grafana/grafana:11.2.0
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?run make up, which creates .env from .env.example}
      GF_ANALYTICS_REPORTING_ENABLED: "false"
      GF_USERS_ALLOW_SIGN_UP: "false"
    volumes:
      - ./observability/grafana/provisioning:/etc/grafana/provisioning:ro
      - ./observability/grafana/dashboards:/etc/grafana/dashboards:ro
      - grafana-data:/var/lib/grafana
    ports:
      - "127.0.0.1:${GRAFANA_HOST_PORT:-23000}:3000"
    depends_on:
      tempo: { condition: service_healthy }
      prometheus: { condition: service_healthy }
      loki: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:3000/api/health | grep -q ok"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

`GF_SECURITY_ADMIN_PASSWORD` sets the admin password from `.env`; the `${...:?message}` form stops Compose when the variable is empty. `GF_ANALYTICS_REPORTING_ENABLED: "false"` turns off usage reports. `GF_USERS_ALLOW_SIGN_UP: "false"` stops self-registration. The three mounts: provisioning files, dashboards, and the named volume `grafana-data` at `/var/lib/grafana` (Grafana's own database; without it the first start migrates its database on every `up`, which is slow). It waits for the three data stores to be healthy. The health check calls `/api/health` and looks for the word `ok`.

## Try it yourself

Open <http://127.0.0.1:23000> (user `admin`, the password from `.env`), go to Dashboards and open "Three signals". After `make run`, hover the p95 panel: small dots are exemplars; click one and choose to open the trace.

```bash
curl -s -u "admin:$(grep ^GRAFANA_ADMIN_PASSWORD .env | cut -d= -f2)" http://127.0.0.1:23000/api/datasources | python3 -c "import json,sys; print(sorted(d['uid'] for d in json.load(sys.stdin)))"
```

Expected: `['loki', 'prometheus', 'tempo']`.

```bash
curl -s -u "admin:$(grep ^GRAFANA_ADMIN_PASSWORD .env | cut -d= -f2)" http://127.0.0.1:23000/api/datasources/uid/prometheus/health
```

Expected: a JSON message saying the Prometheus API was queried successfully.

```bash
.venv/bin/python -m pytest tests/observability -q
```

Expected: `14 passed`.

```bash
make health
```

Expected: eight `ok` lines, the last three about Grafana querying each data source.

## Mistakes people make

- **Editing a provisioned dashboard in the UI** and losing the change at the next restart. Here `allowUiUpdates: false` and `editable: false` make that explicit.
- **Using a data source's name in dashboards** instead of its `uid`. Names can change; uids are stable.
- **Default passwords.** The admin password here comes from `.env` and the compose file refuses to start without it.
- **Unlinked data sources.** Without `exemplarTraceIdDestinations`, exemplar dots are not clickable.

## Check yourself

1. Does Grafana store metrics, traces or logs? *No. It queries other stores.*
2. What is provisioning? *Configuring Grafana from files at start, instead of the UI.*
3. What does `access: proxy` mean for a data source? *Grafana's server makes the queries, not the browser.*
4. What is a panel's `gridPos`? *Its position and size on the 24-column grid.*
5. Why does the dashboard refer to data sources by `uid`? *The uid is stable even if the display name changes.*

## Further reading

- Grafana 11.2 documentation: https://grafana.com/docs/grafana/v11.2/
- Provisioning: https://grafana.com/docs/grafana/v11.2/administration/provisioning/
- Dashboard JSON model: https://grafana.com/docs/grafana/v11.2/dashboards/build-dashboards/view-dashboard-json-model/
- Data source HTTP API: https://grafana.com/docs/grafana/v11.2/developers/http_api/data_source/
- Configure via environment variables: https://grafana.com/docs/grafana/v11.2/setup-grafana/configure-grafana/#override-configuration-with-environment-variables
