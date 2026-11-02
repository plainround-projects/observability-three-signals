# PromQL

## What it is

PromQL is the query language of Prometheus. A query is an **expression** that produces numbers from stored series. You type it in the Prometheus UI, in a Grafana panel, or in an alert rule. There is no `SELECT`: you name a metric, narrow it with label matchers in braces, and apply functions.

## Why this repository uses it

Every number you see in the lesson is a PromQL expression: the p95 panel, the "share slower than 1 s" panel, the alert, and the first step of the click path in code. Reading them is how you know what the dashboard really measures.

## Where it appears

| Path | Expression |
| --- | --- |
| `observability/grafana/dashboards/three-signals.json` | One expression per metric panel (panels 1 to 4). |
| `observability/prometheus/rules/order_assembly.rules.yml` | The alert expression. |
| `src/three_signals/lab/follow.py` | `P95_EXPRESSION`, the query the walk starts from. |
| `src/three_signals/lab/health_cli.py`, `tests/integration/test_stack.py` | `up{job="..."}` health queries. |
| `tests/e2e/test_lesson_scenario.py` | A p95 over five minutes. |

## How it works underneath

- A **selector** `metric{label="value"}` picks series. `=` is equal, `!=` is not equal, `=~` matches a regular expression.
- A **range vector** `metric[1m]` is the last minute of points of each selected series.
- `rate(counter[1m])` is the per-second increase of a counter, averaged over the range; it handles counter resets.
- `sum(...)` adds series together. `sum by (label) (...)` keeps the named label and adds the rest. `sum without (label)` drops the named label.
- `histogram_quantile(0.95, sum by (le) (rate(x_bucket[1m])))` estimates the 95th percentile from histogram buckets: it finds the bucket where 95% of observations have been counted and interpolates inside it. The result is an estimate that depends on the bucket boundaries.
- Arithmetic between two results matches series by identical label sets; `sum(...)` without `by` leaves one series with no labels, which makes `sum(a) / sum(b)` match.
- A comparison such as `> 0.02` filters: the expression returns a value only where the comparison is true; for an alert, "returns something" means "fire".
- An **instant query** evaluates at one time. A **range query** evaluates at many times (`start`, `end`, `step`) and is what a graph uses.

## Walkthrough of this repository's use

### The metric names

The application records one histogram named `order_assembly_duration_seconds` (see `opentelemetry.md`). The Collector's Prometheus exporter publishes it as three series: `order_assembly_duration_seconds_bucket` (with an `le` label), `order_assembly_duration_seconds_sum` and `order_assembly_duration_seconds_count`. The label `order_item_count_bucket` carries the order-size bucket (the span attribute `order.item_count_bucket`, with dots changed to underscores). The label `service_name` comes from the resource. The OpenTelemetry Flask instrumentation also produces `http_server_duration_milliseconds_*`.

### The p95 query

```python
P95_EXPRESSION = "histogram_quantile(0.95, sum by (le) (rate(order_assembly_duration_seconds_bucket[1m])))"
```

Read it from the inside out. `order_assembly_duration_seconds_bucket[1m]` is the last minute of points of every bucket series. `rate(...)` turns each cumulative counter into "per second" over that minute. `sum by (le) (...)` adds up the series of all order-size buckets, keeping only the `le` label, so there is one series per bucket edge. `histogram_quantile(0.95, ...)` finds the bucket where 95 percent of observations are reached and interpolates inside it. The result is the 95th percentile of assembly time, in seconds. It is an estimate: with the buckets 0.5, 1.0, 2.5, the answer can only fall somewhere inside one of those ranges.

The same expression is the first panel of the dashboard (`"expr"` in the JSON), with `"exemplar": true` so Grafana also asks for the exemplars.

```python
EXEMPLAR_SELECTOR = "order_assembly_duration_seconds_bucket"
SLOW_SPAN_SECONDS = 1.0
```

`EXEMPLAR_SELECTOR` is the plain metric name, because exemplars are attached to the bucket series. `SLOW_SPAN_SECONDS` is the one-second threshold.

### The share of slow assemblies

```yaml
        expr: |
          (
            sum(rate(order_assembly_duration_seconds_count[2m]))
            - sum(rate(order_assembly_duration_seconds_bucket{le="1.0"}[2m]))
          )
          / sum(rate(order_assembly_duration_seconds_count[2m]))
          > 0.02
```

`rate(..._count[2m])` is all assemblies per second. `rate(..._bucket{le="1.0"}[2m])` is those that took at most one second (`{le="1.0"}` is a label matcher). The difference is the slow ones. `/ sum(...)` divides by the total. `> 0.02` is a filter: the whole expression returns a value only while the share exceeds two percent. Because `sum(...)` with no `by` produces one series with no labels, the two sides match without extra work.

The dashboard's second panel has the same ratio over one minute (without the filter) so you can see the line cross two percent.

### The other panels

- Panel 3: `sum by (order_item_count_bucket) (rate(order_assembly_duration_seconds_count[1m]))` keeps one line per order-size bucket: assemblies per second by size. Only four label values exist, which is why this label is safe.
- Panel 4: `sum by (service_name) (rate(http_server_duration_milliseconds_count[1m]))` is requests per second, per service.

### Health queries

`up{job="otel-collector"}` is the synthetic series Prometheus writes for each scrape: 1 if the target answered, 0 if not. The integration test asserts it equals 1 for the two Collector jobs.

## Try it yourself

```bash
curl -s --get "http://127.0.0.1:29090/api/v1/query" --data-urlencode 'query=up' | python3 -c "import json,sys; print([(r['metric']['job'], r['value'][1]) for r in json.load(sys.stdin)['data']['result']])"
```

Expected (needs `make up`): three jobs, each with `'1'`.

```bash
curl -s --get "http://127.0.0.1:29090/api/v1/query" --data-urlencode 'query=sum by (order_item_count_bucket) (rate(order_assembly_duration_seconds_count[1m]))' | python3 -m json.tool | head -20
```

Expected (during or just after `make run`): up to three results (the seed gives orders of 1 to 5 items, so the buckets are `1-2` and `3-5`), each with a small number of requests per second.

```bash
curl -s --get "http://127.0.0.1:29090/api/v1/query" --data-urlencode 'query=histogram_quantile(0.95, sum by (le) (rate(order_assembly_duration_seconds_bucket[5m])))'
```

Expected: one result whose value is the estimated p95 in seconds.

Open <http://127.0.0.1:29090/graph>, paste the share expression from the rule file, and press Execute: you see the same number the alert uses.

## Mistakes people make

- **`rate()` on a histogram `_bucket` without summing by `le`** first.
- **Averaging percentiles** across instances. Sum the buckets, then take the quantile.
- **Dividing by zero** when traffic stops: the result is `NaN`, and a comparison with NaN is false.
- **Forgetting that quoting rules** differ in JSON (`\"`) and YAML.

## Check yourself

1. What does `rate(x[1m])` return? *The per-second increase of a counter averaged over the last minute.*
2. What does `sum by (le) (...)` keep? *Only the `le` label; other labels are added together.*
3. How do you estimate the p95 from a histogram? *`histogram_quantile(0.95, sum by (le) (rate(..._bucket[...])))`.*
4. What does `metric[5m]` mean? *A range vector: all points of the last five minutes.*
5. What does `>` do in an alert expression? *Filters results to those greater than the number; a non-empty result fires the alert.*

## Further reading

- Querying basics: https://prometheus.io/docs/prometheus/2.54/querying/basics/
- Functions: https://prometheus.io/docs/prometheus/2.54/querying/functions/
- Operators: https://prometheus.io/docs/prometheus/2.54/querying/operators/
- Histograms and summaries: https://prometheus.io/docs/practices/histograms/
