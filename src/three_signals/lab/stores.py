"""Small HTTP clients for Prometheus, Tempo, Loki and Grafana, returning plain typed values."""

from __future__ import annotations

import base64
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import requests


class StoreError(RuntimeError):
    """A backend answered with an error or something this client cannot read."""


@dataclass(frozen=True, slots=True)
class Exemplar:
    """One histogram sample that carries the trace it was measured in."""

    trace_id: str
    value: float
    timestamp: float


@dataclass(frozen=True, slots=True)
class SpanView:
    """The parts of a span the investigation needs."""

    name: str
    service: str
    span_id: str
    parent_span_id: str
    duration_ms: float
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogLine:
    """One log entry from Loki."""

    timestamp_ns: int
    line: str
    labels: Mapping[str, str]
    structured_metadata: Mapping[str, str]


def _get(url: str, **kwargs: Any) -> requests.Response:
    try:
        response = requests.get(url, timeout=kwargs.pop("timeout", 60), **kwargs)
    except requests.RequestException as error:
        raise StoreError(f"GET {url} failed: {error}") from error
    return response


def _id_to_hex(value: str) -> str:
    """Tempo returns ids either as hex or as base64 bytes; normalize to lowercase hex."""
    stripped = value.strip()
    if len(stripped) in (16, 32) and all(char in "0123456789abcdefABCDEF" for char in stripped):
        return stripped.lower()
    return base64.b64decode(stripped + "=" * (-len(stripped) % 4)).hex()


def _attribute_value(wrapper: Mapping[str, Any]) -> Any:
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in wrapper:
            return wrapper[key]
    return None


class PrometheusClient:
    """Instant queries, exemplar queries and readiness."""

    def __init__(self, base_url: str) -> None:
        """Take the Prometheus base URL."""
        self._base = base_url.rstrip("/")

    def ready(self) -> bool:
        """True when Prometheus reports itself healthy."""
        return _get(f"{self._base}/-/healthy").status_code == 200

    def query(self, expression: str) -> list[tuple[Mapping[str, str], float]]:
        """Run an instant query and return (labels, value) pairs."""
        body = self._json(_get(f"{self._base}/api/v1/query", params={"query": expression}))
        return [(item["metric"], float(item["value"][1])) for item in body["data"]["result"]]

    def scalar(self, expression: str, default: float = 0.0) -> float:
        """Run an instant query expected to return at most one series and return its value."""
        result = self.query(expression)
        return result[0][1] if result else default

    def query_range(self, expression: str, start: float, end: float, step_seconds: float) -> list[tuple[float, float]]:
        """Run a range query expected to return one series and return its (time, value) points."""
        params = {"query": expression, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": f"{step_seconds:.0f}"}
        body = self._json(_get(f"{self._base}/api/v1/query_range", params=params))
        result = body["data"]["result"]
        if not result:
            return []
        return [(float(when), float(value)) for when, value in result[0]["values"] if value != "NaN"]

    def exemplars(self, selector: str, start: float, end: float) -> list[Exemplar]:
        """Return every exemplar stored for the selector between two Unix times, newest last."""
        params = {"query": selector, "start": f"{start:.3f}", "end": f"{end:.3f}"}
        body = self._json(_get(f"{self._base}/api/v1/query_exemplars", params=params))
        found = [
            Exemplar(item["labels"]["trace_id"], float(item["value"]), float(item["timestamp"]))
            for series in body["data"]
            for item in series["exemplars"]
            if "trace_id" in item["labels"]
        ]
        return sorted(found, key=lambda exemplar: exemplar.timestamp)

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        if response.status_code != 200:
            raise StoreError(f"Prometheus answered {response.status_code}: {response.text[:200]}")
        body: dict[str, Any] = response.json()
        if body.get("status") != "success":
            raise StoreError(f"Prometheus query failed: {body}")
        return body


class TempoClient:
    """Trace lookup by id, tag search and readiness."""

    def __init__(self, base_url: str) -> None:
        """Take the Tempo base URL."""
        self._base = base_url.rstrip("/")

    def ready(self) -> bool:
        """True when Tempo reports itself ready."""
        response = _get(f"{self._base}/ready")
        return response.status_code == 200 and "ready" in response.text

    def trace(self, trace_id: str) -> list[SpanView] | None:
        """Return the spans of one trace, or ``None`` while Tempo does not know it (yet)."""
        response = _get(f"{self._base}/api/traces/{trace_id}", headers={"Accept": "application/json"})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise StoreError(f"Tempo answered {response.status_code} for trace {trace_id}: {response.text[:200]}")
        return self.parse_trace(response.json())

    @staticmethod
    def parse_trace(document: Mapping[str, Any]) -> list[SpanView]:
        """Flatten Tempo's batches into span views."""
        spans: list[SpanView] = []
        for batch in document.get("batches", document.get("resourceSpans", [])):
            resource = {
                attribute["key"]: _attribute_value(attribute["value"])
                for attribute in batch.get("resource", {}).get("attributes", [])
            }
            for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])):
                for span in scope.get("spans", []):
                    start = int(span["startTimeUnixNano"])
                    end = int(span["endTimeUnixNano"])
                    spans.append(
                        SpanView(
                            name=span["name"],
                            service=str(resource.get("service.name", "")),
                            span_id=_id_to_hex(span["spanId"]),
                            parent_span_id=_id_to_hex(span["parentSpanId"]) if span.get("parentSpanId") else "",
                            duration_ms=(end - start) / 1e6,
                            attributes={a["key"]: _attribute_value(a["value"]) for a in span.get("attributes", [])},
                        )
                    )
        return spans

    def search(self, traceql: str, start: float, end: float, limit: int = 50) -> list[str]:
        """Return the ids of traces matching a TraceQL query in the time range."""
        params = {"q": traceql, "start": int(start), "end": int(end) + 1, "limit": limit}
        response = _get(f"{self._base}/api/search", params=params)
        if response.status_code != 200:
            raise StoreError(f"Tempo search answered {response.status_code}: {response.text[:200]}")
        return [item["traceID"] for item in response.json().get("traces", [])]

    def wait_for_trace(self, trace_id: str, timeout_seconds: float = 60.0) -> list[SpanView]:
        """Poll until Tempo returns the trace, or raise ``StoreError`` after the timeout."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                spans = self.trace(trace_id)
            except StoreError:
                spans = None  # a slow or restarting Tempo is retried until the deadline
            if spans:
                return spans
            time.sleep(1.0)
        raise StoreError(f"trace {trace_id} did not appear in Tempo within {timeout_seconds:.0f}s")


class LokiClient:
    """LogQL range queries and readiness."""

    def __init__(self, base_url: str) -> None:
        """Take the Loki base URL."""
        self._base = base_url.rstrip("/")

    def ready(self) -> bool:
        """True when Loki reports itself ready."""
        response = _get(f"{self._base}/ready")
        return response.status_code == 200 and "ready" in response.text

    def query(self, logql: str, start: float, end: float, limit: int = 1000) -> list[LogLine]:
        """Run a LogQL range query and return the lines oldest first."""
        params = {
            "query": logql,
            "start": int(start * 1e9),
            "end": int(end * 1e9),
            "limit": limit,
            "direction": "forward",
        }
        response = _get(f"{self._base}/loki/api/v1/query_range", params=params)
        if response.status_code != 200:
            raise StoreError(f"Loki answered {response.status_code}: {response.text[:200]}")
        lines: list[LogLine] = []
        for stream in response.json()["data"]["result"]:
            for entry in stream["values"]:
                metadata = entry[2] if len(entry) > 2 else {}
                structured = metadata.get("structuredMetadata", metadata) if isinstance(metadata, dict) else {}
                lines.append(LogLine(int(entry[0]), entry[1], stream["stream"], structured))
        return sorted(lines, key=lambda line: line.timestamp_ns)


class GrafanaClient:
    """Datasource health checks through Grafana's own API."""

    def __init__(self, base_url: str, user: str, password: str) -> None:
        """Take the Grafana base URL and the admin credentials."""
        self._base = base_url.rstrip("/")
        self._auth = (user, password)

    def datasource_health(self, uid: str) -> tuple[bool, str]:
        """Ask Grafana to query one datasource; returns (ok, message).

        Prometheus and Loki have a backend health check. Tempo's data source has none in Grafana 11.2 (the API answers
        404, "Method not implemented"), so it is queried through Grafana's data source proxy with a real search, which
        is what the "Save & test" button does.
        """
        if uid == "tempo":
            proxied = _get(f"{self._base}/api/datasources/proxy/uid/tempo/api/search?limit=1", auth=self._auth)
            return proxied.status_code == 200, f"Tempo answered {proxied.status_code} through Grafana's proxy"
        response = _get(f"{self._base}/api/datasources/uid/{uid}/health", auth=self._auth)
        message = ""
        try:
            message = str(response.json().get("message", ""))
        except ValueError:
            message = response.text[:200]
        return response.status_code == 200, message

    def datasource_uids(self) -> list[str]:
        """The uids of every provisioned datasource."""
        response = _get(f"{self._base}/api/datasources", auth=self._auth)
        if response.status_code != 200:
            raise StoreError(f"Grafana answered {response.status_code}")
        return sorted(item["uid"] for item in response.json())
