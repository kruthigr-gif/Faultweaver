import pytest
from unittest.mock import MagicMock
from investigator.detection import Detector

class MockTelemetry:
    def __init__(self, short_response, baseline_response):
        self.responses = [short_response, baseline_response]
        self.call = 0
        self.queries = []
    def query_prometheus(self, query, time):
        # Return the predefined response based on call order (first = short term, second = baseline)
        self.queries.append(query)
        resp = self.responses[0] if self.call == 0 else self.responses[1]
        self.call += 1
        return resp


def test_detector_anomaly_true():
    short_response = {"data": {"result": [{"value": [0, "4.0"]}]}}
    baseline_response = {"data": {"result": [{"value": [0, "1.0"]}]}}
    mock = MockTelemetry(short_response, baseline_response)
    detector = Detector(mock)
    assert detector.is_anomaly("order-service", "2023-01-01T00:02:00Z")


def test_detector_anomaly_false():
    short_response = {"data": {"result": [{"value": [0, "0.5"]}]}}
    # Baseline will be empty (no data), causing detector to return False
    baseline_response = {"data": {"result": []}}
    mock = MockTelemetry(short_response, baseline_response)
    detector = Detector(mock)
    assert not detector.is_anomaly("order-service", "2023-01-01T00:02:00Z")
    assert mock.queries[0].startswith("sum(rate(http_request_duration_seconds_sum")
    assert 'job="order-service",handler=~"/orders"' in mock.queries[0]


def test_detector_reports_insufficient_data_without_baseline():
    response = {"data": {"result": []}}
    detector = Detector(MockTelemetry(response, response))

    assessment = detector.assess("order-service", "2023-01-01T00:02:00Z")

    assert assessment["status"] == "insufficient_data"
    assert assessment["current_latency_seconds"] is None


def test_inventory_detector_includes_order_reservation_and_stock_requests():
    response = {"data": {"result": [{"value": [0, "0.1"]}]}}
    mock = MockTelemetry(response, response)
    detector = Detector(mock)

    detector.assess("inventory-service", "2023-01-01T00:02:00Z")

    assert 'job="inventory-service",handler=~"/(stock|reserve)"' in mock.queries[0]


def test_detector_rejects_unknown_service():
    response = {"data": {"result": []}}
    detector = Detector(MockTelemetry(response, response))

    with pytest.raises(ValueError, match="Unsupported service"):
        detector.assess("unknown-service", "2023-01-01T00:02:00Z")
