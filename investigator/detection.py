import datetime
import math
import statistics
from typing import Any

if __package__:
    from .config import SERVICE_HANDLERS
else:
    from config import SERVICE_HANDLERS


class Detector:
    """Detect latency anomalies against a service's recent baseline."""

    def __init__(self, telemetry_client: Any):
        self.telemetry = telemetry_client

    @staticmethod
    def _to_timestamp(iso: str) -> float:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()

    def _query_latency(self, service: str, end_ts: float, window_seconds: int) -> float | None:
        handler = SERVICE_HANDLERS.get(service)
        if handler is None:
            raise ValueError(f"Unsupported service: {service}")

        query = (
            "sum(rate(http_request_duration_seconds_sum"
            f'{{job="{service}",handler=~"{handler}"}}[{window_seconds}s])) / '
            "sum(rate(http_request_duration_seconds_count"
            f'{{job="{service}",handler=~"{handler}"}}[{window_seconds}s]))'
        )
        result = self.telemetry.query_prometheus(query, time=str(int(end_ts)))
        values = result.get("data", {}).get("result", [])
        if not values:
            return None

        value = float(values[0]["value"][1])
        return value if math.isfinite(value) else None

    def assess(self, service: str, alert_time_iso: str) -> dict:
        """Return latency measurements and an anomaly status for one service."""
        alert_ts = self._to_timestamp(alert_time_iso)
        current_latency = self._query_latency(service, alert_ts, 60)
        baseline_samples = []
        for minutes_before in range(2, 6):
            sample_end = alert_ts - minutes_before * 60
            latency = self._query_latency(service, sample_end, 60)
            if latency is not None:
                baseline_samples.append(latency)

        if current_latency is None:
            return {
                "status": "insufficient_data",
                "current_latency_seconds": None,
                "baseline_latency_seconds": None,
                "baseline_samples": len(baseline_samples),
                "threshold_seconds": None,
                "message": "No request latency samples were available for the alert window.",
            }

        if len(baseline_samples) < 3:
            return {
                "status": "insufficient_data",
                "current_latency_seconds": current_latency,
                "baseline_latency_seconds": None,
                "baseline_samples": len(baseline_samples),
                "threshold_seconds": None,
                "message": (
                    "At least three historical latency samples are required to establish a baseline."
                ),
            }

        baseline = statistics.median(baseline_samples)
        threshold = max(baseline * 2, baseline + 0.05)
        anomalous = current_latency > threshold
        return {
            "status": "anomaly" if anomalous else "normal",
            "current_latency_seconds": current_latency,
            "baseline_latency_seconds": baseline,
            "baseline_samples": len(baseline_samples),
            "threshold_seconds": threshold,
            "message": (
                "Latency exceeded the service baseline threshold."
                if anomalous
                else "Latency is within the service baseline threshold."
            ),
        }

    def is_anomaly(self, service: str, alert_time_iso: str) -> bool:
        """Return false when latency or its baseline cannot be measured."""
        return self.assess(service, alert_time_iso)["status"] == "anomaly"
