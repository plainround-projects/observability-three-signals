import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from three_signals.lab.scenario import run_scenario


def _server(slow_ids=()):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            order_id = int(self.path.rsplit("/", 1)[1])
            seen.append(order_id)
            status = 404 if order_id in slow_ids else 200
            self.send_response(status)
            self.send_header("X-Trace-Id", f"{order_id:032x}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_the_scenario_sends_the_requested_count_with_seeded_ids_and_collects_trace_ids():
    server, seen = _server()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        first = run_scenario(url, 40, 1000.0, seed=3, sleep=lambda seconds: None)
        again = run_scenario(url, 40, 1000.0, seed=3, sleep=lambda seconds: None)
    finally:
        server.shutdown()
    assert len(first.records) == 40
    assert [r.order_id for r in first.records] == [r.order_id for r in again.records]
    assert all(r.trace_id == f"{r.order_id:032x}" for r in first.records)
    assert not first.failed
    assert len(seen) == 80


def test_failures_are_reported_and_a_dead_server_counts_as_status_zero():
    server, _ = _server(slow_ids=set(range(1, 1001)))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        result = run_scenario(url, 5, 1000.0, seed=1, sleep=lambda seconds: None)
    finally:
        server.shutdown()
    assert len(result.failed) == 5
    dead = run_scenario("http://127.0.0.1:9", 2, 1000.0, seed=1, timeout_seconds=0.3, sleep=lambda seconds: None)
    assert [record.status for record in dead.records] == [0, 0]
