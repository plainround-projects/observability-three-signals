# YAML

## What it is

YAML is a text format for configuration data. It describes the same things as JSON (key/value maps, lists, strings, numbers, true/false, null) but uses **indentation** and dashes instead of braces and brackets, and allows `#` comments. Every Compose file, Prometheus configuration, Collector configuration, Grafana provisioning file, alert rule file and GitHub workflow in this repository is YAML.

## Why this repository uses it

Everything that configures the stack is YAML, so a learner meets the format in ten files. Learning it once explains all of them.

## Where it appears

| Path | What it configures |
| --- | --- |
| `docker-compose.yml` | The containers. |
| `observability/collector/config.yaml` | The Collector. |
| `observability/tempo/tempo.yaml`, `observability/loki/loki.yaml` | The two stores. |
| `observability/prometheus/prometheus.yml`, `rules/order_assembly.rules.yml`, `tests/order_assembly.rules.test.yml` | Prometheus, its alert and the alert's test. |
| `observability/grafana/provisioning/**/*.yaml` | Grafana data sources and the dashboard loader. |
| `.github/workflows/ci.yml` | The CI workflow. |
| `tests/observability/test_configuration_as_code.py` | Reads the files with PyYAML. |

## How it works underneath

- A **mapping** is a set of `key: value` lines at the same indentation. Indentation must use spaces, never tabs.
- A **sequence** (list) is a series of lines that start with `- `. A list item that is a mapping puts its first key on the dash line and the next keys aligned under it.
- A **flow** style is allowed for short values: `[a, b]` for a list and `{k: v}` for a mapping.
- Scalars (single values) can be unquoted (`22`, `true`, `ticket`), or quoted. A value like `"5000"` in quotes is a string, while `5000` is a number. Unquoted `yes`, `no`, `on`, `off` can be read as booleans by some parsers; quote them when you mean text.
- A multi-line string uses `|` (keep line breaks) or `>` (join lines). Prometheus alert expressions use `|`.
- **Anchors and merge keys.** `&name` marks a block so it can be reused; `*name` copies it; `<<: *name` merges the copied mapping into the current one. Compose files use it to share environment variables.
- A file can hold several documents separated by `---`. Not used here.

The parser turns the text into the same structures a program would build in memory (maps and lists), and the program then reads keys from them. The `tests/observability` tests use PyYAML's `safe_load` to do exactly that.

## Walkthrough of this repository's use

Each file is explained in its own note. This note shows the YAML features used and where.

**Mappings and nesting.** The Grafana loader is a mapping of mappings and one list:

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

`apiVersion: 1` is a key with a number. `providers:` has a list; the single item starts at `- name: three-signals` and its next keys (`type`, `disableDeletion`, ...) align under `name`. `options:` is a nested mapping with `path`.

**Inline lists and maps (flow style).** In the Collector file:

```yaml
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/tempo]
```

`receivers: [otlp]` is a list written on one line. Compose uses the inline map form for dependencies:

```yaml
    depends_on:
      tempo: { condition: service_healthy }
      prometheus: { condition: service_healthy }
      loki: { condition: service_healthy }
```

`tempo: { condition: service_healthy }` is a mapping with one key.

**Anchors and merge keys.** The Compose file defines the shared environment once and merges it twice:

```yaml
x-service-environment: &service-environment
  BIND_HOST: 0.0.0.0
```

```yaml
    environment:
      <<: *service-environment
      OTEL_SERVICE_NAME: back
```

`&service-environment` names the block, `<<: *service-environment` copies its keys into this mapping, and then keys that follow (`OTEL_SERVICE_NAME: back`) add or override.

**Quoting.** The Compose file quotes `"5000"` and `"8081"` because environment values must be text. Ports are quoted too:

```yaml
    ports:
      - "127.0.0.1:${OTLP_HTTP_HOST_PORT:-24318}:4318"
      - "127.0.0.1:${COLLECTOR_HEALTH_HOST_PORT:-23133}:13133"
```

Without the quotes a value such as `127.0.0.1:23133:13133` could be misread.

**Multi-line strings.** The alert expression uses `|`:

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

Everything indented under `expr: |` is one string with its line breaks kept.

**Strings with special characters.** A Grafana query contains `${...}` and quotes, so it is in single quotes:

```yaml
        query: '{service_name="${__span.tags["service.name"]}"} | trace_id="${__span.traceId}"'
```

**Comments** start with `#` and run to the end of the line; every file here uses them to explain the why.

**Reading it from code.** The configuration test loads files with `yaml.safe_load`, which returns dicts and lists:

```python
def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())
```

`safe_load` refuses to build arbitrary Python objects, which is why you use it and never `yaml.load` on files you did not write.

## Try it yourself

```bash
.venv/bin/python -c "import yaml; print(yaml.safe_load(open('observability/collector/config.yaml'))['service']['pipelines']['traces'])"
```

Expected: `{'receivers': ['otlp'], 'processors': ['memory_limiter', 'batch'], 'exporters': ['otlp/tempo']}`.

```bash
docker compose config | sed -n 1,25p
```

Expected: the Compose file with variables substituted and the anchor expanded: the `back` service shows every shared variable.

```bash
.venv/bin/python -c "import yaml; print(yaml.safe_load('a: \"5000\"\nb: 5000'))"
```

Expected: `{'a': '5000', 'b': 5000}`: the quoted value is text, the other a number.

```bash
.venv/bin/python -c "import yaml; yaml.safe_load('a:\n\tb: 1')"
```

Expected: a `ScannerError` about the tab character: tabs are not allowed.

```bash
.venv/bin/python -m pytest tests/observability -q
```

Expected: `14 passed`.

## Mistakes people make

- **Tabs.** YAML forbids them for indentation.
- **Wrong indentation level** quietly moves a key to a different parent.
- **Unquoted special characters.** A value containing `: ` or starting with `{`, `*` or `&` must be quoted.
- **Numbers that should be strings.** A port mapping like `"127.0.0.1:23000:3000"` must be quoted, or some parsers read `22:22` as a sexagesimal number.
- **Duplicate keys**, where the last one silently wins.

## Check yourself

1. How does YAML show that one key is inside another? *By indenting it further with spaces.*
2. What is the difference between `5000` and `"5000"`? *The first is a number; the second is a string.*
3. What does `<<: *service-environment` do? *It copies the anchored mapping's keys into this mapping.*
4. Can you indent YAML with tabs? *No.*
5. What does `|` after a key mean? *The following indented lines are one multi-line string with line breaks kept.*

## Further reading

- YAML 1.2 specification: https://yaml.org/spec/1.2.2/
- PyYAML documentation: https://pyyaml.org/wiki/PyYAMLDocumentation
