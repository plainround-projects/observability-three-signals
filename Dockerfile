# One image for both services: the compose file picks the command (front or back) and the service name.
FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

RUN useradd --system --uid 10001 --no-create-home app
USER app

# opentelemetry-instrument applies the automatic instrumentation (Flask, requests, psycopg) and reads the
# OTEL_* variables; the compose file sets them per service.
CMD ["opentelemetry-instrument", "three-signals-front"]
