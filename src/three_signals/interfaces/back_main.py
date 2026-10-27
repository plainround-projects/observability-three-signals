"""Composition root of the back service: read settings, build the adapters, serve."""

from __future__ import annotations

import os
import random

from opentelemetry import metrics, trace
from psycopg_pool import ConnectionPool

from three_signals.application.assemble_order import AssembleOrder
from three_signals.infrastructure.fault import RandomSlowQueryPlanner
from three_signals.infrastructure.logging_setup import configure_logging
from three_signals.infrastructure.otel_telemetry import OtelAssemblyTelemetry
from three_signals.infrastructure.postgres_orders import PostgresOrderRepository
from three_signals.infrastructure.settings import BackSettings
from three_signals.interfaces.back_app import create_back_app
from three_signals.interfaces.server import run_server


def main() -> None:
    """Start the back service."""
    settings = BackSettings.from_env(os.environ)
    configure_logging(settings.log_level, settings.service_name)
    pool = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=settings.pool_max_size,
        timeout=5.0,
        kwargs={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        open=False,
    )
    pool.open(wait=True, timeout=30.0)
    repository = PostgresOrderRepository(pool, settings.slow_query_warn_seconds)
    planner = RandomSlowQueryPlanner(
        settings.slow_query_probability, settings.slow_query_seconds, random.Random(settings.slow_query_seed)
    )
    telemetry = OtelAssemblyTelemetry(trace.get_tracer("three_signals.back"), metrics.get_meter("three_signals.back"))

    def check_database() -> None:
        with pool.connection(timeout=2.0) as connection:
            connection.execute("SELECT 1")

    app = create_back_app(AssembleOrder(repository, planner, telemetry), check_database)
    run_server(app, settings.bind_host, settings.port, on_close=[pool.close])
