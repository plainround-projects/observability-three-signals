# Prometheus (v2.54.1), its rules and promtool

## What it is

Prometheus is a database for metrics and a program that **scrapes** them. A **scrape** is an HTTP GET that Prometheus makes every few seconds to a **target** (an address that publishes its current numbers as plain text). Prometheus stores each number with its time stamp in a **time series database** (TSDB). A **time series** is one metric name plus one exact set of **labels** (key/value pairs such as `service_name="back"`), with a list of (time, value) points.

You ask Prometheus questions in **PromQL**, its query language. Prometheus can also evaluate **rules** on a schedule: **recording rules** save a result as a new metric; **alerting rules** fire an alert when an expression returns something for long enough. This repository pins Prometheus v2.54.1.

## Why this repository uses it

Prometheus is where the second signal lives. It scrapes the numbers that the Collector publishes, stores the latency histogram with its **exemplars** (the trace ids), answers the p95 query behind the first panel, and evaluates the alert rule. The repository also tests that alert rule offline with `promtool`, so a rule typo fails a build and not a night.

## Where it appears

| Path | What it does |
| --- | --- |
| `observability/prometheus/prometheus.yml` | The server configuration: how often to scrape, which targets, where the rules are. |
| `observability/prometheus/rules/order_assembly.rules.yml` | The alert rule `OrderAssemblySlowShareHigh`. |
| `observability/prometheus/tests/order_assembly.rules.test.yml` | The promtool unit test of that rule. |
| `docker-compose.yml` (service `prometheus`) | The pinned image, command-line flags, mounts and health check. |
| `Makefile` (`lint-config`, `test-rules`) | Run `promtool check config` and `promtool test rules` in a throw-away container. |
| `src/three_signals/lab/stores.py` | `PrometheusClient`, the HTTP API client (see `requests.md`). |

## How it works underneath

Prometheus is **pull-based**: the targets do not send; Prometheus asks. In this repository the application sends OTLP to the Collector, the Collector converts it to the Prometheus text format on port 8889, and Prometheus scrapes that port every `scrape_interval`.

Metric types:

- A **counter** only goes up (reset to zero when a process restarts). Its name usually ends in `_total`.
- A **gauge** goes up and down.
- A **histogram** is stored as several series: `<name>_bucket{le="X"}` counts observations less than or equal to X (cumulative), `<name>_sum` is the sum of all observations, and `<name>_count` is how many there were. `le` means "less than or equal". The bucket `le="+Inf"` equals the count.

**Exemplars** are stored in a separate in-memory area enabled by `--enable-feature=exemplar-storage`, and are only readable if the scrape used the OpenMetrics format.

An **alert** goes through states: *inactive* (the expression returns nothing), *pending* (it returns something, but not for the `for:` duration yet), *firing*. A `for: 1m` means the expression must stay true across evaluations for a full minute. Prometheus sends firing alerts to Alertmanager (not used in this repository); here you see them in the UI.

`promtool` is the command-line companion. `promtool check config` validates the main file and the rule files it lists. `promtool test rules` runs a **unit test** for rules: you give fake input series and the time points to check, and it tells you whether the alert fires. The test never contacts a real Prometheus.

Storage lives in a folder (`--storage.tsdb.path`). Data older than `--storage.tsdb.retention.time` is deleted.

## Walkthrough of this repository's use

### `prometheus.yml`

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

rule_files:
  - /etc/prometheus/rules/*.rules.yml
```

`global` sets defaults. `scrape_interval: 5s` makes Prometheus fetch every target every five seconds (the usual production value is 15 to 60 seconds; five keeps the lab quick). `evaluation_interval: 5s` is how often the rules are evaluated. `rule_files` lists the rule files; the pattern `/etc/prometheus/rules/*.rules.yml` is a glob, and the folder is mounted there by Compose.

```yaml
scrape_configs:
  # The Collector's Prometheus exporter: the application metrics, with exemplars.
  - job_name: otel-collector
    static_configs:
      - targets: ["otel-collector:8889"]
  # The Collector's own health: accepted and refused spans, exporter queue size.
  - job_name: otel-collector-internal
    static_configs:
      - targets: ["otel-collector:8888"]
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
```

`scrape_configs` is the list of jobs. Each has a `job_name` (becomes the label `job`) and `static_configs` with a fixed list of `targets` (`host:port`; the path defaults to `/metrics`). Job `otel-collector` scrapes `otel-collector:8889`, the metrics exporter: this is where the application's histograms come from. Job `otel-collector-internal` scrapes `otel-collector:8888`, the Collector's own health (accepted and refused spans, exporter queues). Job `prometheus` scrapes the server itself at `localhost:9090`. Every scrape also writes a synthetic series `up{job="..."}`: 1 if the scrape worked, 0 if not; the integration test checks it.

### The Compose service

```yaml
  prometheus:
    image: prom/prometheus:v2.54.1
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=2d
      - --enable-feature=exemplar-storage
    volumes:
      - ./observability/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./observability/prometheus/rules:/etc/prometheus/rules:ro
      - prometheus-data:/prometheus
    ports:
      - "127.0.0.1:${PROMETHEUS_HOST_PORT:-29090}:9090"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O - http://localhost:9090/-/healthy"]
      interval: 10s
      timeout: 10s
      retries: 60
      start_period: 30s
```

`image: prom/prometheus:v2.54.1` pins the version. The `command` list is the flags: `--config.file` is the configuration; `--storage.tsdb.path=/prometheus` is the data folder (a named volume `prometheus-data`, so data survives a container restart); `--storage.tsdb.retention.time=2d` deletes data older than two days; `--enable-feature=exemplar-storage` turns on exemplar storage, which is off by default. Without this flag the dots on the latency panel would never appear. The two bind mounts are read-only. The port 9090 inside is published at host 29090 on the loopback address only. The health check runs `wget -q -O - http://localhost:9090/-/healthy` inside the container: `wget` is a download program, `-q` is quiet, `-O -` writes the body to standard output; it exits non-zero if the request fails, so Docker sees "unhealthy". Interval 10 s, timeout 10 s, 60 retries and a 30 s grace period (`start_period`, during which failures do not count) allow a slow first start on a loaded machine.

### `order_assembly.rules.yml`: the alert

```yaml
groups:
  - name: order-assembly
    rules:
```

A rule file has `groups`; each group has a `name` and a list of `rules`. Rules in a group are evaluated together at each evaluation interval.

```yaml
      # The share of order assemblies that took longer than one second, over two minutes. A share is steadier than a
      # p95 here: with one request in twenty slow, the p95 sits right on the edge and flips between fast and slow.
```

The comment explains the design choice: alert on the **share** of slow assemblies, not on the p95. With one request in twenty slow, the p95 sits exactly on the edge of the slow bucket and flips between fast and slow from minute to minute. A share (about 5 percent) is steady and clearly above a 2 percent threshold.

```yaml
      - alert: OrderAssemblySlowShareHigh
        expr: |
          (
            sum(rate(order_assembly_duration_seconds_count[2m]))
            - sum(rate(order_assembly_duration_seconds_bucket{le="1.0"}[2m]))
          )
          / sum(rate(order_assembly_duration_seconds_count[2m]))
          > 0.02
        for: 1m
```

`alert: OrderAssemblySlowShareHigh` names the alert. `expr: |` starts a multi-line PromQL expression (see `promql.md`): `sum(rate(..._count[2m]))` is requests per second; `sum(rate(..._bucket{le="1.0"}[2m]))` is requests per second that finished within one second; the difference is the rate of slow ones; dividing by the total rate gives the share; `> 0.02` keeps it only when above two percent. `for: 1m` requires that to hold for a full minute before the alert goes from pending to firing, which avoids alerts on one noisy sample.

```yaml
        labels:
          severity: ticket
        annotations:
          summary: More than 2% of order assemblies take longer than one second.
          description: "{{ $value | humanizePercentage }} of order assemblies over the last 2 minutes took longer than 1 s."
          runbook: Open the order assembly p95 panel, click an exemplar dot on the spike, and read the slowest span's logs.
```

`labels` are attached to the alert (`severity: ticket`, meaning it needs a ticket, not a 3 a.m. page). `annotations` are free text: `summary`, a `description` that uses the template `{{ $value | humanizePercentage }}` (the expression's value, written as a percentage) and a `runbook` line that says what to do. A test checks that every alert has a severity and a runbook.

### `order_assembly.rules.test.yml`: the offline test

```yaml
# Replays two scenarios through the rule file with promtool: 60 requests a minute, first with 5% slower than a
# second (the planted fault) and then with none slower. Run with: make test-rules
rule_files:
  - ../rules/order_assembly.rules.yml

evaluation_interval: 15s

tests:
```

`rule_files` points at the rule file (relative to the test file). `evaluation_interval: 15s` is how often promtool evaluates. `tests:` is a list of test cases.

```yaml
  - name: one request in twenty slower than a second fires the alert after its for-duration
    interval: 15s
    input_series:
      # 15 requests per 15 s scrape; 95% (14.25 on average) land at or below 1.0 s. Cumulative counters.
      - series: 'order_assembly_duration_seconds_count{order_item_count_bucket="3-5"}'
        values: "0+15x40"
      - series: 'order_assembly_duration_seconds_bucket{le="1.0",order_item_count_bucket="3-5"}'
        values: "0+14.25x40"
      - series: 'order_assembly_duration_seconds_bucket{le="+Inf",order_item_count_bucket="3-5"}'
        values: "0+15x40"
```

The first case, "one request in twenty slower than a second fires the alert after its for-duration". `interval: 15s` is the spacing of the fake samples. `input_series` creates three fake series. The notation `"0+15x40"` means: start at 0, add 15 each step, for 40 steps (so 0, 15, 30, ... 600). The `_count` series grows by 15 per 15 seconds, the `le="1.0"` bucket by 14.25 (95 percent of 15), and `le="+Inf"` by 15. So 5 percent of requests are slower than a second.

```yaml
    alert_rule_test:
      - eval_time: 30s
        alertname: OrderAssemblySlowShareHigh
        exp_alerts: []
      - eval_time: 5m
        alertname: OrderAssemblySlowShareHigh
        exp_alerts:
          - exp_labels:
              severity: ticket
            exp_annotations:
              summary: More than 2% of order assemblies take longer than one second.
              description: "5% of order assemblies over the last 2 minutes took longer than 1 s."
              runbook: Open the order assembly p95 panel, click an exemplar dot on the spike, and read the slowest span's logs.
```

`alert_rule_test` says what to expect at given times. At 30 s the alert must not be firing (`exp_alerts: []`): the `for` duration has not passed. At 5 minutes it must fire with exactly these labels and annotations; the description shows `5%`.

```yaml
  - name: fast traffic keeps the alert silent
    interval: 15s
    input_series:
      - series: 'order_assembly_duration_seconds_count{order_item_count_bucket="1-2"}'
        values: "0+15x40"
      - series: 'order_assembly_duration_seconds_bucket{le="1.0",order_item_count_bucket="1-2"}'
        values: "0+15x40"
      - series: 'order_assembly_duration_seconds_bucket{le="+Inf",order_item_count_bucket="1-2"}'
        values: "0+15x40"
    alert_rule_test:
      - eval_time: 5m
        alertname: OrderAssemblySlowShareHigh
        exp_alerts: []
```

The second case: all requests finish within a second (`le="1.0"` grows as fast as the count), so at 5 minutes no alert is expected.

### How `make` runs them

```makefile
test-rules: ## replay a scenario through promtool and assert the alert fires (and stays silent when it should)
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  test rules /etc/prometheus/tests/order_assembly.rules.test.yml
```

`test-rules` runs `promtool test rules` inside the Prometheus image (`--entrypoint promtool` replaces the image's default program with the `promtool` command-line tool that ships in it), mounting the `observability/prometheus` folder read-only.

```makefile
lint-config: ## promtool check on the Prometheus config and rules; the Collector validates its config
	docker run --rm --entrypoint promtool -v "$(CURDIR)/observability/prometheus:/etc/prometheus:ro" $(PROMETHEUS_IMAGE) \
	  check config /etc/prometheus/prometheus.yml
```

`lint-config` runs `promtool check config`, which validates `prometheus.yml` and every rule file it lists.

## Try it yourself

```bash
make test-rules
```

Expected: `SUCCESS`.

```bash
make lint-config
```

Expected: lines that start with `SUCCESS:` (the config is valid, the rule file was found and its rules parse), and nothing from the Collector's validation.

```bash
curl -s "http://127.0.0.1:29090/api/v1/targets" | python3 -c "import json,sys; print([(t['labels']['job'], t['health']) for t in json.load(sys.stdin)['data']['activeTargets']])"
```

Expected (needs `make up`): three jobs, each `up`.

```bash
curl -s "http://127.0.0.1:29090/api/v1/rules" | python3 -c "import json,sys; print([r['name'] for g in json.load(sys.stdin)['data']['groups'] for r in g['rules']])"
```

Expected: `['OrderAssemblySlowShareHigh']`.

```bash
curl -s "http://127.0.0.1:29090/api/v1/query_exemplars?query=order_assembly_duration_seconds_bucket&start=$(($(date +%s)-900))&end=$(date +%s)" | head -c 400
```

Expected (after `make run`): JSON with `exemplars` that have a `trace_id` label.

## Mistakes people make

- **High-cardinality labels.** Every distinct label value creates a series. Never label by user id or order id.
- **Using `rate()` on a gauge** or `avg()` of a quantile.
- **Too short a range in `rate(x[30s])`** with a scrape interval close to it: needs at least two samples inside the range.
- **Trusting the p95 of a barely loaded system.** One slow request out of twenty sits right on the p95 edge; a ratio like "share slower than 1 s" is steadier.
- **Reloading a bad config.** Run `promtool check config` first. `make lint-config` does.

## Check yourself

1. Does Prometheus receive data or fetch it? *It fetches (scrapes) it.*
2. What is a time series? *A metric name plus a set of labels, with its points over time.*
3. What are the three series a histogram makes? *`_bucket` (with `le`), `_sum` and `_count`.*
4. What does `for: 1m` mean on an alert? *The expression must be true continuously for one minute before the alert fires.*
5. What does `promtool test rules` need that a real Prometheus supplies in production? *Nothing; you give it fake input series.*

## Further reading

- Prometheus v2.54 documentation: https://prometheus.io/docs/prometheus/2.54/
- Configuration: https://prometheus.io/docs/prometheus/2.54/configuration/configuration/
- Alerting rules: https://prometheus.io/docs/prometheus/2.54/configuration/alerting_rules/
- Unit testing for rules: https://prometheus.io/docs/prometheus/2.54/configuration/unit_testing_rules/
- Metric types: https://prometheus.io/docs/concepts/metric_types/
- Exemplars: https://prometheus.io/docs/prometheus/2.54/feature_flags/#exemplars-storage
- OpenMetrics: https://prometheus.io/docs/instrumenting/exposition_formats/#openmetrics-text-format
