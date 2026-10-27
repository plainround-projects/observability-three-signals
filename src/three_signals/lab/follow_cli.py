"""``three-signals-follow``: walk from the latency spike to the slow span to its log lines and print each click."""

from __future__ import annotations

import argparse
import os
import sys

from three_signals.lab.config import LabSettings
from three_signals.lab.follow import follow_with_patience, render
from three_signals.lab.stores import LokiClient, PrometheusClient, StoreError, TempoClient


def main(argv: list[str] | None = None) -> int:
    """Print the investigation; exit 1 when a click leads nowhere."""
    parser = argparse.ArgumentParser(description="Follow a slow request from the p95 panel to its logs.")
    parser.add_argument("--wait", type=float, default=180.0, help="seconds to keep retrying while telemetry arrives")
    args = parser.parse_args(argv)
    settings = LabSettings.from_env(os.environ)
    try:
        investigation = follow_with_patience(
            PrometheusClient(settings.prometheus_url),
            TempoClient(settings.tempo_url),
            LokiClient(settings.loki_url),
            patience_seconds=args.wait,
        )
    except StoreError as error:
        print(f"the trail went cold: {error}", file=sys.stderr)
        return 1
    print(render(investigation))
    return 0


if __name__ == "__main__":
    sys.exit(main())
