# 1. Configure the OpenTelemetry SDK with `opentelemetry-instrument` and environment variables

Status: accepted, 2026-10-27

## Context

Both services must export traces, metrics and logs over OTLP, with a service name and version, and the lesson's second
step is "auto-instrument first": see what the HTTP server, HTTP client and database driver libraries give for free. The
exporter endpoint, protocol and resource attributes will differ per environment.

## Decision

The container runs `opentelemetry-instrument three-signals-front` (or `-back`). The agent creates the tracer, meter and
logger providers from `OTEL_*` variables and patches Flask, `requests` and psycopg. The application code only uses the
API: `trace.get_tracer`, `metrics.get_meter`, and a logging handler it attaches to the provider the agent created.

## Alternatives considered

- **Build the providers in code.** Gives a place to unit test the wiring, but puts endpoints and exporters into the
  application, which is the coupling the Collector exists to avoid.
- **Run the agent only in some environments.** Rejected: the lesson compares what the instrumentation gives for free with
  what manual spans add, which only works if the agent is always on.

## Consequences

- Changing the Collector's address or the sampling is a one-line change in `docker-compose.yml`.
- The wiring (`OTEL_*` values) is tested by the end-to-end run rather than by a unit test.
- The processes must not fork after start-up, because the exporters' threads would not survive a fork. That is why the
  services run on waitress (one process, a thread pool) and not on a pre-forking server.
