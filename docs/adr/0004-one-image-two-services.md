# 4. One image, two services

Status: accepted, 2026-10-27

## Context

The lesson's compose file builds `./front` and `./back` separately. Both services are small, share the domain model,
the order JSON codec, the logging and the OpenTelemetry adapters, and are always released together.

## Decision

One package, one `Dockerfile`, one image (`observability-three-signals:dev`). The compose file selects the command
(`three-signals-front` or `three-signals-back`), and the environment gives each its `OTEL_SERVICE_NAME`. The dependency
rule between layers is enforced across both services by one import-linter contract.

## Alternatives considered

- **Two packages and two images.** Matches "two services of your own" literally and lets them be versioned apart, at the
  cost of duplicating or publishing the shared code.

## Consequences

- Both services still deploy, scale and fail independently: they are different containers with different health checks.
- Splitting them later means moving `interfaces/front_*` and `interfaces/back_*` (and what only each uses) into their own
  packages; the layer boundaries already make that a move, not a rewrite.
