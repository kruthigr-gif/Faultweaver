import math
import statistics


def summarize_detection(observations: list[dict]) -> dict:
    true_positive = false_positive = true_negative = false_negative = 0
    insufficient = 0
    latencies = []

    for observation in observations:
        status = observation["status"]
        if status == "insufficient_data":
            insufficient += 1
            continue
        if status not in {"anomaly", "normal"}:
            raise ValueError(f"Unsupported detection status: {status}")
        expected = observation["expected_anomaly"]
        detected = status == "anomaly"
        if expected and detected:
            true_positive += 1
        elif expected:
            false_negative += 1
        elif detected:
            false_positive += 1
        else:
            true_negative += 1

        latency = observation.get("investigation_latency_ms")
        if latency is not None and math.isfinite(latency):
            latencies.append(float(latency))

    evaluated = true_positive + false_positive + true_negative + false_negative
    precision_denominator = true_positive + false_positive
    negative_denominator = false_positive + true_negative
    recall_denominator = true_positive + false_negative
    ordered_latencies = sorted(latencies)
    p95_index = max(0, math.ceil(0.95 * len(ordered_latencies)) - 1)

    return {
        "evaluated_samples": evaluated,
        "insufficient_data_samples": insufficient,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "precision": (
            true_positive / precision_denominator
            if precision_denominator
            else None
        ),
        "false_positive_rate": (
            false_positive / negative_denominator
            if negative_denominator
            else None
        ),
        "recall": (
            true_positive / recall_denominator
            if recall_denominator
            else None
        ),
        "investigation_latency_ms": {
            "samples": len(latencies),
            "mean": statistics.fmean(latencies) if latencies else None,
            "p95": ordered_latencies[p95_index] if ordered_latencies else None,
        },
    }
