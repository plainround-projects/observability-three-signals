"""Lint the files that configure the stack: the compose file, the Collector, the rules and the dashboards."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "observability"
DATASOURCE_UIDS = {"prometheus", "tempo", "loki"}


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return dict(_yaml(ROOT / "docker-compose.yml"))


def test_every_third_party_image_is_pinned_to_a_version(compose):
    images = [service["image"] for service in compose["services"].values() if "build" not in service]
    assert len(images) >= 6
    for image in images:
        assert ":" in image, image
        assert not image.endswith(":latest"), image


def test_the_base_image_is_pinned_by_digest():
    first_line = next(line for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("FROM"))
    assert re.search(r"@sha256:[0-9a-f]{64}$", first_line)


def test_every_published_port_is_bound_to_loopback(compose):
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} on every interface"


def test_every_variable_the_compose_file_reads_is_in_env_example(compose):
    text = (ROOT / "docker-compose.yml").read_text()
    used = set(re.findall(r"\$\{([A-Z_]+)", text))
    declared = {line.split("=", 1)[0] for line in (ROOT / ".env.example").read_text().splitlines() if "=" in line}
    assert used <= declared, sorted(used - declared)


def test_the_services_run_as_a_non_root_user_and_the_image_has_a_health_check(compose):
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER app$", dockerfile, re.MULTILINE)
    for name in ("front", "back"):
        assert "healthcheck" in compose["services"][name]


def test_the_collector_pipelines_use_only_defined_components_with_the_memory_limiter_first_and_batch_last():
    config = _yaml(OBSERVABILITY / "collector" / "config.yaml")
    for signal, pipeline in config["service"]["pipelines"].items():
        assert all(name in config["receivers"] for name in pipeline["receivers"]), signal
        assert all(name in config["exporters"] for name in pipeline["exporters"]), signal
        assert pipeline["processors"][0] == "memory_limiter", signal
        assert pipeline["processors"][-1] == "batch", signal
        assert all(name in config["processors"] for name in pipeline["processors"]), signal


def test_the_collector_exposes_prometheus_metrics_with_exemplars():
    exporter = _yaml(OBSERVABILITY / "collector" / "config.yaml")["exporters"]["prometheus"]
    assert exporter["enable_open_metrics"] is True


def test_prometheus_stores_exemplars_and_scrapes_the_collector(compose):
    assert "--enable-feature=exemplar-storage" in compose["services"]["prometheus"]["command"]
    scrape = _yaml(OBSERVABILITY / "prometheus" / "prometheus.yml")["scrape_configs"]
    assert "otel-collector:8889" in [t for job in scrape for c in job["static_configs"] for t in c["targets"]]


def test_loki_accepts_structured_metadata_for_the_otlp_trace_ids():
    assert _yaml(OBSERVABILITY / "loki" / "loki.yaml")["limits_config"]["allow_structured_metadata"] is True


def test_every_alert_has_a_severity_a_summary_and_a_runbook():
    for path in (OBSERVABILITY / "prometheus" / "rules").glob("*.rules.yml"):
        for group in _yaml(path)["groups"]:
            for rule in group["rules"]:
                assert rule["labels"]["severity"] in {"page", "ticket"}, rule["alert"]
                assert {"summary", "runbook"} <= set(rule["annotations"]), rule["alert"]


def test_provisioned_datasources_are_the_three_the_dashboard_uses():
    sources = _yaml(OBSERVABILITY / "grafana" / "provisioning" / "datasources" / "datasources.yaml")["datasources"]
    assert {source["uid"] for source in sources} == DATASOURCE_UIDS
    prometheus = next(source for source in sources if source["uid"] == "prometheus")
    assert prometheus["jsonData"]["exemplarTraceIdDestinations"][0]["datasourceUid"] == "tempo"


def _panels(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    return [panel for panel in dashboard["panels"] if panel["type"] != "row"]


@pytest.fixture(scope="module")
def dashboard() -> dict[str, Any]:
    return dict(json.loads((OBSERVABILITY / "grafana" / "dashboards" / "three-signals.json").read_text()))


def test_dashboard_panel_ids_are_unique_and_every_panel_has_a_title_and_a_known_datasource(dashboard):
    panels = _panels(dashboard)
    assert len({panel["id"] for panel in panels}) == len(panels)
    for panel in panels:
        assert panel["title"]
        assert panel["datasource"]["uid"] in DATASOURCE_UIDS
        for target in panel["targets"]:
            assert target["datasource"]["uid"] in DATASOURCE_UIDS


def test_dashboard_has_the_latency_panel_with_exemplars_and_a_logs_panel(dashboard):
    panels = {panel["title"]: panel for panel in _panels(dashboard)}
    latency = next(panel for title, panel in panels.items() if "p95" in title)
    assert any(target.get("exemplar") for target in latency["targets"])
    assert "order_assembly_duration_seconds_bucket" in json.dumps(latency["targets"])
    assert any(panel["type"] == "logs" for panel in panels.values())


def test_dashboard_queries_only_use_metrics_the_stack_produces(dashboard):
    known = re.compile(r"order_assembly_duration_seconds|http_server_|http_client_|otelcol_|up\b")
    for panel in _panels(dashboard):
        for target in panel["targets"]:
            expression = target.get("expr", "")
            for metric in re.findall(r"\b([a-z][a-z0-9_]{6,})\s*[{\[(]", expression):
                if metric in {"histogram_quantile", "sum", "rate", "increase"}:
                    continue
                assert known.match(metric), f"{panel['title']}: {metric}"
