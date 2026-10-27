"""``three-signals-stack-health``: every backend answers, and Grafana can query each of its three data sources."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

from three_signals.lab.config import LabSettings
from three_signals.lab.stores import GrafanaClient, LokiClient, PrometheusClient, StoreError, TempoClient

DATASOURCES = ("prometheus", "tempo", "loki")


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> int:
    """Print one line per check and exit 1 when any fails."""
    settings = LabSettings.from_env(os.environ)
    collector_port = os.environ.get("COLLECTOR_HEALTH_HOST_PORT", "23133")
    checks: list[tuple[str, bool]] = [
        ("front /healthz", _http_ok(f"{settings.front_url}/healthz")),
        ("collector health_check", _http_ok(f"http://127.0.0.1:{collector_port}/")),
        ("tempo /ready", TempoClient(settings.tempo_url).ready()),
        ("prometheus /-/healthy", PrometheusClient(settings.prometheus_url).ready()),
        ("loki /ready", LokiClient(settings.loki_url).ready()),
    ]
    grafana = GrafanaClient(settings.grafana_url, settings.grafana_user, settings.grafana_password)
    for uid in DATASOURCES:
        try:
            ok, message = grafana.datasource_health(uid)
        except StoreError as error:
            ok, message = False, str(error)
        checks.append((f"grafana queries {uid}: {message}", ok))
    for name, ok in checks:
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
