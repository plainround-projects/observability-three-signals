import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from three_signals.domain.errors import BackendTimeoutError, BackendUnavailableError, OrderNotFoundError
from three_signals.domain.orders import OrderId
from three_signals.infrastructure.http_back_client import HttpBackClient, build_session

GOOD = {
    "order": {"id": 1, "customer": "customer-0001"},
    "items": [{"id": 1, "sku": "SKU-001", "quantity": 1, "unit_price_cents": 5}],
}


def _serve(behavior):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            status, body = behavior(len(calls))
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, calls


@pytest.fixture
def serve():
    servers = []

    def start(behavior):
        server, calls = _serve(behavior)
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", calls

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _client(url, retries=0, timeout=2.0):
    return HttpBackClient(url, build_session(retries), timeout)


def test_a_good_response_becomes_an_order(serve):
    url, calls = serve(lambda n: (200, GOOD))
    order = _client(url).fetch_order(OrderId(1))
    assert order.header.customer_name == "customer-0001"
    assert calls == ["/order/1"]


def test_404_becomes_order_not_found(serve):
    url, _ = serve(lambda n: (404, {"error": "order_not_found"}))
    with pytest.raises(OrderNotFoundError):
        _client(url).fetch_order(OrderId(9))


def test_a_server_error_becomes_backend_unavailable(serve):
    url, _ = serve(lambda n: (500, {}))
    with pytest.raises(BackendUnavailableError):
        _client(url).fetch_order(OrderId(1))


def test_a_503_is_retried_with_backoff_until_it_succeeds(serve):
    url, calls = serve(lambda n: (503, {}) if n < 3 else (200, GOOD))
    assert _client(url, retries=3).fetch_order(OrderId(1)).header.order_id == OrderId(1)
    assert len(calls) == 3


def test_an_unreadable_body_becomes_backend_unavailable(serve):
    url, _ = serve(lambda n: (200, b"not json"))
    with pytest.raises(BackendUnavailableError):
        _client(url).fetch_order(OrderId(1))


def test_a_slow_back_service_becomes_a_timeout(serve):
    import time

    def slow(n):
        time.sleep(0.5)
        return 200, GOOD

    url, _ = serve(slow)
    with pytest.raises(BackendTimeoutError):
        _client(url, timeout=0.1).fetch_order(OrderId(1))


def test_a_refused_connection_becomes_backend_unavailable():
    with pytest.raises(BackendUnavailableError):
        HttpBackClient("http://127.0.0.1:9", requests.Session(), 0.5).fetch_order(OrderId(1))
