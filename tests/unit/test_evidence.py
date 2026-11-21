from investigator.evidence import EvidenceCollector


class MockTelemetry:
    def __init__(self):
        self.prometheus_queries = []
        self.loki_queries = []

    def query_prometheus(self, query, time):
        self.prometheus_queries.append(query)
        return {"data": {"result": [{"value": [time, "2"]}]}}

    def query_loki(self, query, start, end):
        assert 'service_name="inventory-service"' in query
        self.loki_queries.append(query)
        return {
            "data": {
                "result": [
                    {"values": [["1", "database connection failed"]]}
                ]
            }
        }

    def search_tempo(self, service, start, end):
        assert service == "inventory-service"
        assert start < end
        return {"traces": [{"traceID": "trace-1"}]}


def test_collector_queries_real_metric_labels_and_scores_evidence():
    telemetry = MockTelemetry()
    collector = EvidenceCollector(telemetry)
    assessment = {
        "status": "anomaly",
        "current_latency_seconds": 2.0,
        "baseline_latency_seconds": 0.1,
        "threshold_seconds": 0.2,
        "message": "Latency exceeded the service baseline threshold.",
    }

    evidence = collector.collect(
        "inventory-service",
        "2026-11-20T16:00:00Z",
        300,
        assessment,
    )

    assert [item["signal"] for item in evidence] == [
        "latency",
        "errors",
        "error_logs",
        "traces",
    ]
    assert evidence[0]["score"] == 4
    assert evidence[1]["score"] == 5
    assert evidence[2]["score"] == 3
    assert "job=\"inventory-service\",handler=\"/stock\"" in telemetry.prometheus_queries[0]
    assert 'severity_text=~"ERROR|FATAL"' in telemetry.loki_queries[0]
