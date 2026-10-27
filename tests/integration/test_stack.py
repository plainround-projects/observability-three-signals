"""Against the stack that `make up` started: every part answers and the services reach the database."""

from __future__ import annotations

import os
import subprocess

import pytest
import requests

from three_signals.lab.config import LabSettings
from three_signals.lab.stores import GrafanaClient, LokiClient, PrometheusClient, TempoClient

pytestmark = pytest.mark.integration

SETTINGS = LabSettings.from_env(os.environ)


def _psql(sql: str, user: str = "postgres") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", user, "-d", "orders", "-At", "-c", sql],
        capture_output=True,
        text=True,
        check=False,
    )


def test_grafana_can_query_tempo_prometheus_and_loki():
    grafana = GrafanaClient(SETTINGS.grafana_url, SETTINGS.grafana_user, SETTINGS.grafana_password)
    assert grafana.datasource_uids() == ["loki", "prometheus", "tempo"]
    for uid in ("prometheus", "tempo", "loki"):
        ok, message = grafana.datasource_health(uid)
        assert ok, f"{uid}: {message}"


def test_each_backend_reports_ready():
    assert TempoClient(SETTINGS.tempo_url).ready()
    assert PrometheusClient(SETTINGS.prometheus_url).ready()
    assert LokiClient(SETTINGS.loki_url).ready()


def test_the_seed_data_is_1000_orders_and_3000_items():
    assert _psql("SELECT count(*) FROM orders").stdout.strip() == "1000"
    assert _psql("SELECT count(*) FROM order_items").stdout.strip() == "3000"
    five_item_orders = _psql("SELECT count(*) FROM (SELECT 1 FROM order_items GROUP BY order_id HAVING count(*) = 5) s")
    assert five_item_orders.stdout.strip() == "200"


def test_the_services_role_can_read_but_not_write():
    assert _psql("SELECT count(*) FROM orders", user="orders_reader").stdout.strip() == "1000"
    denied = _psql("DELETE FROM orders WHERE id = 1", user="orders_reader")
    assert denied.returncode != 0
    assert "permission denied" in denied.stderr


def test_an_order_flows_through_front_back_and_the_database():
    response = requests.get(f"{SETTINGS.front_url}/order/3", timeout=15)
    assert response.status_code == 200
    body = response.json()
    assert body["order"]["customer"] == "customer-0003"
    assert body["order"]["item_count_bucket"] == "1-2"
    assert [item["sku"] for item in body["items"]] == ["SKU-047", "SKU-004"]
    assert len(response.headers["X-Trace-Id"]) == 32


def test_bad_and_unknown_orders_get_the_documented_statuses():
    assert requests.get(f"{SETTINGS.front_url}/order/0", timeout=15).status_code == 400
    assert requests.get(f"{SETTINGS.front_url}/order/999999", timeout=15).status_code == 404
    assert requests.get(f"{SETTINGS.front_url}/order/abc", timeout=15).status_code == 404


def test_prometheus_scrapes_the_collector():
    prometheus = PrometheusClient(SETTINGS.prometheus_url)
    assert prometheus.scalar('up{job="otel-collector"}') == 1
    assert prometheus.scalar('up{job="otel-collector-internal"}') == 1
