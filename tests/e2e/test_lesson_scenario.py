"""The lesson's steps against the real stack: send traffic, then query Tempo, Prometheus and Loki for the evidence."""

from __future__ import annotations

import json
import os
import time

import pytest

from three_signals.lab.config import LabSettings
from three_signals.lab.follow import Investigation, follow_with_patience, render
from three_signals.lab.scenario import ScenarioResult, run_scenario
from three_signals.lab.stores import GrafanaClient, LokiClient, PrometheusClient, SpanView, TempoClient

pytestmark = pytest.mark.e2e

SETTINGS = LabSettings.from_env(os.environ)
PROMETHEUS = PrometheusClient(SETTINGS.prometheus_url)
TEMPO = TempoClient(SETTINGS.tempo_url)
LOKI = LokiClient(SETTINGS.loki_url)


@pytest.fixture(scope="module")
def scenario() -> ScenarioResult:
    result = run_scenario(SETTINGS.front_url, request_count=400, rate_per_second=20.0, seed=11)
    time.sleep(25)  # the 5 s metric export, the 5 s scrape and the 1 s batch, with margin
    return result


@pytest.fixture(scope="module")
def investigation(scenario: ScenarioResult) -> Investigation:
    result = follow_with_patience(PROMETHEUS, TEMPO, LOKI, patience_seconds=240)
    print(render(result))
    return result


def _spans_of(trace_id: str) -> list[SpanView]:
    return TEMPO.wait_for_trace(trace_id, timeout_seconds=60)


def test_step_1_grafana_queries_all_three_backends():
    grafana = GrafanaClient(SETTINGS.grafana_url, SETTINGS.grafana_user, SETTINGS.grafana_password)
    assert all(grafana.datasource_health(uid)[0] for uid in ("prometheus", "tempo", "loki"))


def test_the_scenario_got_a_200_for_every_request_and_met_the_planted_slow_query(scenario):
    assert not scenario.failed
    assert 5 <= len(scenario.slow) <= 60
    assert all(len(record.trace_id) == 32 for record in scenario.records)


def test_step_2_one_request_is_one_trace_with_spans_from_front_back_and_the_database(scenario):
    fast = next(record for record in scenario.records if record.duration_seconds < 0.5)
    spans = _spans_of(fast.trace_id)
    assert {span.service for span in spans} == {"front", "back"}
    selects = [span for span in spans if span.name.startswith("SELECT")]
    assert len(selects) == 2
    by_id = {span.span_id: span for span in spans}
    assert sum(span.parent_span_id == "" for span in spans) == 1
    assert all(span.parent_span_id in by_id for span in spans if span.parent_span_id)


def test_step_3_the_manual_span_carries_the_bucket_and_the_histogram_has_a_p95(scenario):
    spans = _spans_of(scenario.records[0].trace_id)
    manual = next(span for span in spans if span.name == "assemble-order-response")
    assert manual.attributes["order.item_count_bucket"] in {"1-2", "3-5"}
    assert manual.service == "back"
    p95 = PROMETHEUS.scalar(
        "histogram_quantile(0.95, sum by (le) (rate(order_assembly_duration_seconds_bucket[5m])))", default=-1.0
    )
    assert p95 > 0


def test_step_4_a_trace_finds_exactly_its_log_lines_and_a_log_line_finds_its_trace(scenario):
    trace_id = scenario.records[1].trace_id
    spans = _spans_of(trace_id)
    now = time.time()
    lines = LOKI.query(f'{{service_name="back"}} | trace_id="{trace_id}"', now - 600, now + 60)
    assert len(lines) >= 2
    bodies = [json.loads(line.line) for line in lines]
    assert {body["trace_id"] for body in bodies} == {trace_id}
    span_ids = {span.span_id for span in spans}
    assert {body["span_id"] for body in bodies} <= span_ids
    assert {line.structured_metadata.get("trace_id") for line in lines} == {trace_id}
    other = LOKI.query(f'{{service_name="back"}} | trace_id!="{trace_id}"', now - 600, now + 60, limit=5)
    assert other
    assert all(trace_id not in line.line for line in other)
    assert TEMPO.trace(json.loads(other[0].line)["trace_id"]) is not None


def test_step_5_the_walk_from_the_spike_ends_at_the_slow_order_items_span_and_its_log_lines(investigation):
    assert investigation.peak_p95_seconds >= 0.5
    assert investigation.exemplar.value >= 1.0
    assert investigation.services_in_trace == {"front", "back"}
    assert investigation.slow_span.duration_ms >= 1000
    assert "order_items" in str(investigation.slow_span.attributes["db.statement"])
    messages = [json.loads(line.line)["msg"] for line in investigation.log_lines]
    assert "order_items query started" in messages
    assert "slow query finished" in messages
    assert investigation.reverse_trace_id == investigation.exemplar.trace_id


def test_the_dashboard_is_provisioned_with_the_exemplar_panel():
    import requests

    response = requests.get(
        f"{SETTINGS.grafana_url}/api/dashboards/uid/three-signals",
        auth=(SETTINGS.grafana_user, SETTINGS.grafana_password),
        timeout=15,
    )
    assert response.status_code == 200
    titles = [panel["title"] for panel in response.json()["dashboard"]["panels"]]
    assert any("p95" in title for title in titles)
