"""Where the lab tooling finds the stack: URLs and the Grafana admin login, from the environment."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LabSettings:
    """Addresses of the published ports on this machine."""

    front_url: str
    prometheus_url: str
    tempo_url: str
    loki_url: str
    grafana_url: str
    grafana_user: str
    grafana_password: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> LabSettings:
        """Derive every URL from the same ``*_HOST_PORT`` variables the compose file publishes."""
        return cls(
            front_url=f"http://127.0.0.1:{env.get('FRONT_HOST_PORT', '28081')}",
            prometheus_url=f"http://127.0.0.1:{env.get('PROMETHEUS_HOST_PORT', '9090')}",
            tempo_url=f"http://127.0.0.1:{env.get('TEMPO_HOST_PORT', '3200')}",
            loki_url=f"http://127.0.0.1:{env.get('LOKI_HOST_PORT', '3100')}",
            grafana_url=f"http://127.0.0.1:{env.get('GRAFANA_HOST_PORT', '3000')}",
            grafana_user="admin",
            grafana_password=env.get("GRAFANA_ADMIN_PASSWORD", ""),
        )
