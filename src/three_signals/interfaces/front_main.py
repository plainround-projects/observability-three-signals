"""Composition root of the front service: read settings, build the adapters, serve."""

from __future__ import annotations

import os

from three_signals.application.fetch_order import FetchOrder
from three_signals.infrastructure.http_back_client import HttpBackClient, build_session
from three_signals.infrastructure.logging_setup import configure_logging
from three_signals.infrastructure.settings import FrontSettings
from three_signals.interfaces.front_app import create_front_app
from three_signals.interfaces.server import run_server


def main() -> None:
    """Start the front service."""
    settings = FrontSettings.from_env(os.environ)
    configure_logging(settings.log_level, settings.service_name)
    session = build_session(settings.back_retries)
    client = HttpBackClient(settings.back_url, session, settings.back_timeout_seconds)
    app = create_front_app(FetchOrder(client))
    run_server(app, settings.bind_host, settings.port, on_close=[session.close])
