# Docker Compose

## What it is

Docker Compose is a tool that starts several containers together from one file. The file is usually named `docker-compose.yml`. In it you list **services** (each service is one container definition: which image, which command, which environment variables, which ports, which volumes), and Compose creates a private network for them, starts them in the right order, and lets you stop them all with one command.

This repository uses **Compose v2**, which you run as `docker compose` (with a space). It is part of the Docker command-line program. The older v1 was a separate program called `docker-compose` with a hyphen.

## Why this repository uses it

`make up` has to start eight containers in the right order with the right settings, and `make down` has to remove them without leaving anything behind. The Compose file is that description. It is also documentation: every version, port and environment variable of the lab is in one file.

## Where it appears

| Path | What it does |
| --- | --- |
| `docker-compose.yml` | The whole stack. |
| `Makefile` | `up`, `down`, `db`, `queries` call `docker compose`. |
| `tests/observability/test_configuration_as_code.py` | Lints it: pinned images, loopback ports, variables declared in `.env.example`. |
| `tests/integration/test_stack.py` | Uses `docker compose exec` to run SQL. |
| `.github/workflows/ci.yml` | Prints the container logs when a run fails. |

## How it works underneath

`docker compose up` reads the YAML file, resolves `${VARIABLE}` placeholders from your shell and from a file named `.env` in the same folder, and builds a plan. For every service it creates a container. For the whole project it creates one **network**. Each container joins the network under its service name, and Docker runs a small DNS server on that network. So the container `back` can call `http://postgres:5432` and the name `postgres` resolves to the database container's address. These names only exist inside the network.

A **published port** (`"127.0.0.1:23000:3000"`) opens a door from your machine into a container: connections to port 23000 on your machine's own loopback address are forwarded to port 3000 inside the container. Without the `127.0.0.1:` part, Docker would listen on every network interface and other computers could connect.

`depends_on` with `condition: service_healthy` makes Compose wait until the other service's health check passes before starting this one. `--wait` makes `docker compose up` itself wait until all started services are healthy (or running, when they have no health check).

A **named volume** listed at the bottom of the file is created once and kept between runs. `docker compose down -v` stops the containers, removes them and the network, and deletes the named volumes.

A **project name** (the `name:` key, or the folder name by default) is a prefix Compose puts on every container, network and volume it creates, so two projects do not clash.

## Walkthrough of this repository's use

### The header and the shared environment

```yaml
# The whole lab on one laptop: two services, a database, a Collector and the three backends behind Grafana.
# Every host port is bound to 127.0.0.1 only. Versions are pinned to the ones the lesson names.
name: observability-three-signals
```

Two comments say what the file is and the two rules: every host port is bound to `127.0.0.1`, and versions are pinned. `name: observability-three-signals` sets the project name, which prefixes the names of the containers, the network and the volumes.

```yaml
x-service-environment: &service-environment
  BIND_HOST: 0.0.0.0
  OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4318
  OTEL_EXPORTER_OTLP_PROTOCOL: http/protobuf
  OTEL_TRACES_EXPORTER: otlp
  OTEL_METRICS_EXPORTER: otlp
  OTEL_LOGS_EXPORTER: otlp
  OTEL_METRIC_EXPORT_INTERVAL: "5000"
  OTEL_RESOURCE_ATTRIBUTES: service.version=0.1.0,deployment.environment=local
  OTEL_PYTHON_FLASK_EXCLUDED_URLS: healthz
  OTEL_PYTHON_REQUESTS_EXCLUDED_URLS: healthz
  LOG_LEVEL: INFO
```

`x-service-environment` is an **extension field**: a top-level key starting with `x-` that Compose ignores. `&service-environment` after the colon is a YAML **anchor** that names this mapping so other places can reuse it. The variables are the OpenTelemetry settings both services share (explained in `opentelemetry.md`) plus `BIND_HOST: 0.0.0.0` (listen on all interfaces inside the container, so Docker can forward to it) and `LOG_LEVEL: INFO`. The value `"5000"` is quoted because Compose environment values must be strings.

### Services

```yaml
services:
  postgres:
    image: postgres:16.4-alpine
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?run make up, which creates .env from .env.example}
      POSTGRES_DB: orders
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      # TCP, not the socket: the temporary server that runs the init scripts listens on the socket only.
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U postgres -d orders"]
      interval: 3s
      timeout: 3s
      retries: 20
```

`services:` starts the list. **postgres**: image, environment (the superuser password from `.env` and the database name), the volume, and the health check, covered in `postgresql.md`. In `healthcheck`, `test: ["CMD-SHELL", "..."]` runs the string with the container's shell, `interval` is the pause between checks, `timeout` how long one check may take, and `retries` how many failures in a row mark the container unhealthy.

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

**otel-collector**: covered in `opentelemetry-collector.md`. The `ports` entries read `"127.0.0.1:${OTLP_HTTP_HOST_PORT:-24318}:4318"`: bind address, then the host port (`${NAME:-default}` uses `24318` unless `.env` sets another), then the container port. The `depends_on` entries use the short map form `tempo: { condition: service_healthy }`.

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

**tempo**: image `grafana/tempo:2.6.1`, config mounted read-only, data volume `tempo-data` at `/var/tempo`, port 3200 at host 23200, and a health check that looks for the word `ready`. The long `retries: 60` and `start_period: 30s` allow for a slow first start on a busy laptop.

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

**prometheus**: the flags and mounts are explained in `prometheus.md`.

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

**loki**: explained in `loki.md`.

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

**grafana**: explained in `grafana.md`. Note the `depends_on`: Grafana waits for all three data stores so the provisioned data sources work on first load.

```yaml
  back:
    build: .
    image: observability-three-signals:dev
    command: ["opentelemetry-instrument", "three-signals-back"]
    environment:
      <<: *service-environment
      OTEL_SERVICE_NAME: back
      BACK_PORT: "8081"
      DATABASE_URL: postgresql://orders_reader:${ORDERS_READER_PASSWORD:?run make up}@postgres:5432/orders
      SLOW_QUERY_PROBABILITY: ${SLOW_QUERY_PROBABILITY:-0.05}
      SLOW_QUERY_SECONDS: ${SLOW_QUERY_SECONDS:-1.5}
      SLOW_QUERY_SEED: ${SLOW_QUERY_SEED:-}
    depends_on:
      postgres: { condition: service_healthy }
      otel-collector: { condition: service_started }
    healthcheck:
      test: ["CMD", "three-signals-health", "--url", "http://localhost:8081/healthz", "--timeout", "10"]
      interval: 10s
      timeout: 20s
      retries: 30
      start_period: 60s
    stop_grace_period: 15s
```

**back**. `build: .` builds the image from the `Dockerfile` in this folder; `image: observability-three-signals:dev` names the result, so `front` can reuse it. `command` overrides the image's default command to start the back service under `opentelemetry-instrument`. `environment` begins with `<<: *service-environment`, the YAML **merge key**, which copies all shared variables, and then adds this service's own: `OTEL_SERVICE_NAME: back`, `BACK_PORT: "8081"`, the `DATABASE_URL` with the read-only role's password from `.env`, and the three settings of the planted fault, each with a default (`${SLOW_QUERY_PROBABILITY:-0.05}`, `${SLOW_QUERY_SECONDS:-1.5}`, `${SLOW_QUERY_SEED:-}` where empty means "not seeded"). `depends_on` waits for a healthy database and for the Collector to have started (the Collector has no health check in its image). The health check runs the service's own probe `three-signals-health --url http://localhost:8081/healthz --timeout 10` in the exec form `["CMD", ...]` (no shell). `timeout: 20s`, `retries: 30` and `start_period: 60s` are generous because a loaded host can be slow. `stop_grace_period: 15s` gives the container 15 seconds after `SIGTERM` before `SIGKILL`.

```yaml
  front:
    build: .
    image: observability-three-signals:dev
    command: ["opentelemetry-instrument", "three-signals-front"]
    environment:
      <<: *service-environment
      OTEL_SERVICE_NAME: front
      FRONT_PORT: "8080"
      BACK_URL: http://back:8081
    ports:
      - "127.0.0.1:${FRONT_HOST_PORT:-28081}:8080"
    depends_on:
      back: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "three-signals-health", "--url", "http://localhost:8080/healthz", "--timeout", "10"]
      interval: 10s
      timeout: 20s
      retries: 30
      start_period: 60s
    stop_grace_period: 15s
```

**front**: the same image and shape. `BACK_URL: http://back:8081` is the back service's address on the Compose network. It is the only service with a published port (host 28081, loopback). `depends_on: back: { condition: service_healthy }` starts it only when the back service can reach its database.

### Volumes

```yaml
volumes:
  postgres-data:
  tempo-data:
  prometheus-data:
  loki-data:
  grafana-data:
```

The five **named volumes**, each created on first use and deleted by `make down` (`docker compose down -v`). Only the name is needed; the driver defaults to local storage.

### The Makefile side

```makefile
# The database first (the services connect as a role that make db creates), then everything else.
up: .env $(STAMP) ## build and start the whole stack, load the schema and seed data, wait until healthy
	docker compose up -d --wait postgres
	$(MAKE) --no-print-directory db
	docker compose up -d --build --wait --wait-timeout 1800
	$(MAKE) --no-print-directory health
```

`make up` starts the database first (`up -d --wait postgres`: `-d` runs in the background, `--wait` blocks until it is healthy), loads the schema and seed (`make db`), then starts everything else (`up -d --build --wait --wait-timeout 1800`: build the image if needed, wait up to 30 minutes for all health checks), then runs `make health`. `--no-print-directory` hides Make's "entering directory" messages. The order matters because the services connect as a role that `make db` creates.

```makefile
down: ## stop the stack and delete its data
	docker compose down -v
```

`make down` runs `docker compose down -v`.

## Try it yourself

```bash
docker compose config --quiet && echo valid
```

Expected: `valid` (this checks the file after variable substitution; it needs `.env`, which `make up` creates).

```bash
docker compose ps
```

Expected (after `make up`): eight services, all `healthy` or `running`.

```bash
docker compose config | grep -A3 "published"
```

Expected: each published port shows `host_ip: 127.0.0.1`.

```bash
docker compose exec front python -c "import socket; print(socket.gethostbyname('back'))"
```

Expected: a private address such as `172.x.x.x`: the service name resolved by Compose's DNS.

```bash
docker compose stop back; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:28081/order/3; docker compose start back
```

Expected: `502` (the front service cannot reach the back service). The second request after the back service is healthy again returns `200`.

## Mistakes people make

- **Expecting `depends_on` to wait for readiness.** The short form only waits for the container to *start*. Use `condition: service_healthy` together with a health check.
- **Publishing a port on every interface.** `"3000:3000"` listens on all interfaces. Prefix `127.0.0.1:` unless other machines must connect.
- **Putting secrets in the YAML.** Keep them in `.env` (which is git-ignored) and reference them as `${NAME}`. This repository uses `${NAME:?message}` so Compose stops with a clear message when a required variable is missing.
- **Forgetting `-v` on `down`.** Without it the volumes survive, and a later `up` starts with the old data. Sometimes that is wanted; for a repeatable lab it is not.
- **Using `localhost` between containers.** Use the service name.
- **Reading the YAML indentation wrongly.** One space too many silently makes a key belong to another block. Lint the file (`docker compose config` prints the final, resolved version).

## Check yourself

1. How does one container find another by name? *Compose puts all services on one network with a DNS server; the service name resolves to that container's address.*
2. What does `127.0.0.1:23000:3000` mean? *Port 3000 inside the container is reachable only from your own machine, on port 23000.*
3. What is the difference between `depends_on: [db]` and `depends_on: {db: {condition: service_healthy}}`? *The first waits for the container to start; the second waits until its health check passes.*
4. What does `docker compose down -v` delete that `docker compose down` keeps? *The named volumes, and so the data stored in them.*
5. What does `${NAME:?text}` do when `NAME` is empty or unset? *Compose stops and prints the text as an error.*

## Further reading

- Compose file reference: https://docs.docker.com/reference/compose-file/
- `docker compose up`: https://docs.docker.com/reference/cli/docker/compose/up/
- Compose networking: https://docs.docker.com/compose/how-tos/networking/
- Variable interpolation: https://docs.docker.com/reference/compose-file/interpolation/
