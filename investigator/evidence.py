import datetime
import uuid

import requests

if __package__:
    from .config import SERVICE_HANDLERS
    from .telemetry_clients import TelemetryClient
else:
    from config import SERVICE_HANDLERS
    from telemetry_clients import TelemetryClient


class EvidenceCollector:
    """Collect service-scoped evidence from the configured telemetry backends."""

    def __init__(self, telemetry_client: TelemetryClient):
        self.telemetry = telemetry_client

    @staticmethod
    def _ts_to_iso(timestamp: float) -> str:
        return (
            datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _metric_value(response: dict) -> float | None:
        results = response.get("data", {}).get("result", [])
        if not results:
            return None
        return float(results[0]["value"][1])

    @staticmethod
    def _make_evidence(
        source: str,
        service: str,
        signal: str,
        start_iso: str,
        end_iso: str,
        observation: str,
        reference: str,
        score: float = 0,
        available: bool = True,
    ) -> dict:
        return {
            "evidence_id": f"{source[:2]}-{uuid.uuid4().hex[:6]}",
            "source": source,
            "service": service,
            "signal": signal,
            "window_start": start_iso,
            "window_end": end_iso,
            "observation": observation,
            "reference": reference,
            "score": score,
            "available": available,
        }

    def collect(
        self,
        service: str,
        alert_time_iso: str,
        window_seconds: int,
        latency_assessment: dict,
    ) -> list[dict]:
        handler = SERVICE_HANDLERS.get(service)
        if handler is None:
            raise ValueError(f"Unsupported service: {service}")

        alert_ts = datetime.datetime.fromisoformat(
            alert_time_iso.replace("Z", "+00:00")
        ).timestamp()
        start_ts = alert_ts - window_seconds
        start_iso = self._ts_to_iso(start_ts)
        end_iso = self._ts_to_iso(alert_ts)
        evidence = []

        latency = latency_assessment.get("current_latency_seconds")
        if latency is None:
            latency_observation = latency_assessment["message"]
        else:
            baseline = latency_assessment.get("baseline_latency_seconds")
            baseline_text = (
                f"; baseline {baseline:.3f}s" if baseline is not None else ""
            )
            latency_observation = (
                f"Average request latency was {latency:.3f}s{baseline_text}. "
                f"{latency_assessment['message']}"
            )
        evidence.append(
            self._make_evidence(
                "prometheus",
                service,
                "latency",
                start_iso,
                end_iso,
                latency_observation,
                "http://localhost:9090",
                score=4 if latency_assessment["status"] == "anomaly" else 0,
                available=True,
            )
        )

        error_query = (
            "sum(increase(http_requests_total"
            f'{{job="{service}",handler="{handler}",status=~"4xx|5xx"}}'
            f'[{window_seconds}s])) or vector(0)'
        )
        try:
            error_count = self._metric_value(
                self.telemetry.query_prometheus(error_query, time=str(int(alert_ts)))
            )
            if error_count is None:
                error_observation = "No request error data was returned."
                error_score = 0
                available = False
            else:
                error_observation = f"{error_count:.0f} HTTP errors in the window."
                error_score = 5 if error_count > 0 else 0
                available = True
            evidence.append(
                self._make_evidence(
                    "prometheus",
                    service,
                    "errors",
                    start_iso,
                    end_iso,
                    error_observation,
                    "http://localhost:9090",
                    score=error_score,
                    available=available,
                )
            )
        except requests.RequestException as exc:
            evidence.append(
                self._make_evidence(
                    "prometheus",
                    service,
                    "errors",
                    start_iso,
                    end_iso,
                    f"Prometheus error query failed: {exc}",
                    "",
                    available=False,
                )
            )

        log_query = (
            f'{{service_name="{service}"}} '
            '| severity_text=~"ERROR|FATAL"'
        )
        try:
            logs = self.telemetry.query_loki(
                log_query,
                start=str(int(start_ts * 1e9)),
                end=str(int(alert_ts * 1e9)),
            )
            log_count = sum(
                len(stream.get("values", []))
                for stream in logs.get("data", {}).get("result", [])
            )
            evidence.append(
                self._make_evidence(
                    "loki",
                    service,
                    "error_logs",
                    start_iso,
                    end_iso,
                    f"Found {log_count} error-level log entries.",
                    "http://localhost:3100",
                    score=3 if log_count else 0,
                )
            )
        except requests.RequestException as exc:
            evidence.append(
                self._make_evidence(
                    "loki",
                    service,
                    "error_logs",
                    start_iso,
                    end_iso,
                    f"Loki query failed: {exc}",
                    "",
                    available=False,
                )
            )

        try:
            traces = self.telemetry.search_tempo(
                service,
                int(start_ts),
                int(alert_ts),
            )
            trace_count = len(traces.get("traces", []))
            evidence.append(
                self._make_evidence(
                    "tempo",
                    service,
                    "traces",
                    start_iso,
                    end_iso,
                    f"Found {trace_count} traces.",
                    "http://localhost:3200",
                )
            )
        except requests.RequestException as exc:
            evidence.append(
                self._make_evidence(
                    "tempo",
                    service,
                    "traces",
                    start_iso,
                    end_iso,
                    f"Tempo search failed: {exc}",
                    "",
                    available=False,
                )
            )

        return evidence
