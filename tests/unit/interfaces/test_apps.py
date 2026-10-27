import pytest

from three_signals.application.assemble_order import AssembleOrder
from three_signals.application.fetch_order import FetchOrder
from three_signals.domain.errors import BackendTimeoutError, BackendUnavailableError, OrderNotFoundError
from three_signals.domain.orders import Order, OrderHeader, OrderId, OrderItem
from three_signals.interfaces.back_app import create_back_app
from three_signals.interfaces.front_app import create_front_app

ORDER = Order(OrderHeader(OrderId(3), "customer-0003"), (OrderItem(1, "SKU-001", 2, 500),))


class StubAssemble(AssembleOrder):
    def __init__(self, outcome):
        self._outcome = outcome

    def execute(self, order_id):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class StubFetch(FetchOrder):
    def __init__(self, outcome):
        self._outcome = outcome

    def execute(self, order_id):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _back(outcome=ORDER, check=lambda: None):
    return create_back_app(StubAssemble(outcome), check).test_client()


def _front(outcome=ORDER):
    return create_front_app(StubFetch(outcome)).test_client()


@pytest.mark.parametrize("make", [_back, _front])
def test_get_order_returns_the_order_as_json(make):
    response = make().get("/order/3")
    assert response.status_code == 200
    assert response.get_json()["order"]["customer"] == "customer-0003"
    assert response.get_json()["items"][0]["sku"] == "SKU-001"


@pytest.mark.parametrize("make", [_back, _front])
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (OrderNotFoundError(9), 404, "order_not_found"),
        (BackendTimeoutError("slow"), 504, "backend_timeout"),
        (BackendUnavailableError("down"), 502, "backend_unavailable"),
        (RuntimeError("bug"), 500, "internal_error"),
    ],
)
def test_named_errors_map_to_json_problems(make, error, status, code):
    response = make(error).get("/order/3")
    assert response.status_code == status
    assert response.get_json()["error"] == code


def test_order_id_zero_is_a_bad_request_and_a_word_is_not_found():
    client = _back()
    assert client.get("/order/0").status_code == 400
    assert client.get("/order/abc").status_code == 404
    assert client.get("/order/abc").get_json()["error"] == "not_found"


def test_back_health_reports_the_database():
    assert _back().get("/healthz").get_json() == {"status": "ok", "database": "up"}

    def broken():
        raise ConnectionError("down")

    response = _back(check=broken).get("/healthz")
    assert response.status_code == 503
    assert response.get_json()["database"] == "down"


def test_front_health_is_ok():
    assert _front().get("/healthz").get_json() == {"status": "ok"}


def test_every_response_inside_a_span_carries_the_trace_id_header(memory_telemetry):
    client = _back()
    tracer = memory_telemetry.tracer_provider.get_tracer("test")
    with tracer.start_as_current_span("request") as span:
        response = client.get("/order/3")
        expected = f"{span.get_span_context().trace_id:032x}"
    assert response.headers["X-Trace-Id"] == expected
