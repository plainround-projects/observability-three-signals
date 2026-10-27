"""``three-signals-scenario``: send steady traffic to the front service and summarize what came back."""

from __future__ import annotations

import argparse
import os
import statistics
import sys

from three_signals.lab.config import LabSettings
from three_signals.lab.scenario import run_scenario


def main(argv: list[str] | None = None) -> int:
    """Run the scenario; exit 1 when more than the allowed share of requests failed."""
    parser = argparse.ArgumentParser(description="Send steady traffic to the front service.")
    parser.add_argument("--requests", type=int, default=600)
    parser.add_argument("--rate", type=float, default=10.0, help="requests per second")
    parser.add_argument("--seed", type=int, default=7, help="seed for the order ids requested")
    parser.add_argument("--url", default=LabSettings.from_env(os.environ).front_url)
    parser.add_argument(
        "--max-failure-share", type=float, default=0.05, help="exit 1 when more than this share of requests failed"
    )
    args = parser.parse_args(argv)

    result = run_scenario(args.url, args.requests, args.rate, args.seed)
    durations = sorted(record.duration_seconds for record in result.records)
    p95 = durations[int(0.95 * (len(durations) - 1))]
    print(
        f"sent {len(result.records)} requests in {result.finished_at - result.started_at:.1f}s: "
        f"{len(result.failed)} failed, {len(result.slow)} slow (>= 1 s), "
        f"median {statistics.median(durations) * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms"
    )
    return 1 if len(result.failed) > args.max_failure_share * len(result.records) else 0


if __name__ == "__main__":
    sys.exit(main())
