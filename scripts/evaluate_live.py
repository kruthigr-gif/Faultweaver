import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from investigator.evaluation import summarize_detection
from loadgen.run_load import get_local_token


INVESTIGATOR_URL = "http://127.0.0.1:9000"
PROMETHEUS_URL = "http://127.0.0.1:9090"
LOAD_SECONDS_AFTER_FAULT = 45
RECOVERY_TIMEOUT_SECONDS = 120


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["Content-Type"] = "application/json"
    return session


def _investigate(session, alert_time: datetime) -> tuple[dict, float]:
    session.headers["Authorization"] = (
        f"Bearer {get_local_token()['access_token']}"
    )
    started = time.perf_counter()
    response = session.post(
        f"{INVESTIGATOR_URL}/investigate",
        json={
            "alert_time": alert_time.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "service": "inventory-service",
            "window_seconds": 300,
        },
        timeout=30,
    )
    response.raise_for_status()
    latency_ms = (time.perf_counter() - started) * 1000
    return response.json(), latency_ms


def _observation(
    report: dict,
    latency_ms: float,
    expected_anomaly: bool,
    expected_error_evidence: bool = False,
) -> dict:
    evidence = [
        item
        for item in report.get("evidence", [])
        if item.get("service") == "inventory-service"
        and item.get("signal") in {"errors", "error_logs"}
        and item.get("available", True)
        and item.get("score", 0) > 0
    ]
    error_signals = {item["signal"] for item in evidence}
    return {
        "status": report["detection"]["status"],
        "latency": {
            "current_seconds": report["detection"]["current_latency_seconds"],
            "baseline_seconds": report["detection"]["baseline_latency_seconds"],
            "baseline_samples": report["detection"]["baseline_samples"],
            "threshold_seconds": report["detection"]["threshold_seconds"],
            "message": report["detection"]["message"],
        },
        "expected_anomaly": expected_anomaly,
        "investigation_latency_ms": latency_ms,
        "expected_error_evidence": expected_error_evidence,
        "error_evidence_detected": {
            "errors",
            "error_logs",
        }.issubset(error_signals),
        "ranked_lead": (
            report["ranked_causes"][0]["service"]
            if report.get("ranked_causes")
            else None
        ),
    }


def _start_load(seconds: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "loadgen" / "run_load.py"),
            "--rps",
            "2",
            "--duration",
            str(seconds),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _terminate_process(process: subprocess.Popen):
    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


def _wait_process(process: subprocess.Popen, name: str):
    try:
        output, _ = process.communicate(
            timeout=RECOVERY_TIMEOUT_SECONDS + 180
        )
    except subprocess.TimeoutExpired as exc:
        _terminate_process(process)
        raise RuntimeError(f"{name} did not finish within its safety timeout.") from exc
    if process.returncode:
        detail = output.strip()
        suffix = f" Output: {detail[-1000:]}" if detail else ""
        raise RuntimeError(
            f"{name} failed with exit code {process.returncode}.{suffix}"
        )


def _run_fault_scenario(
    scenario: dict,
    session: requests.Session,
) -> tuple[dict, float | None]:
    load_duration = (
        int(scenario["start_offset_seconds"])
        + int(scenario["duration_seconds"])
        + LOAD_SECONDS_AFTER_FAULT
    )
    load_process = _start_load(load_duration)
    fault_process = None
    try:
        fault_process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "faults" / "inject.py"),
                scenario["scenario_id"],
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(int(scenario["start_offset_seconds"]) + 25)
        report, latency_ms = _investigate(session, datetime.now(timezone.utc))
        observation = _observation(
            report,
            latency_ms,
            expected_anomaly=bool(scenario["expected_latency_anomaly"]),
            expected_error_evidence=bool(scenario["expect_error_evidence"]),
        )
        _wait_process(fault_process, scenario["scenario_id"])
        recovery_seconds = (
            _wait_for_error_recovery()
            if scenario.get("measure_recovery")
            else None
        )
        _wait_process(load_process, "load generator")
        return observation, recovery_seconds
    finally:
        if fault_process is not None and fault_process.poll() is None:
            _terminate_process(fault_process)
        if load_process.poll() is None:
            _terminate_process(load_process)


def _wait_for_error_recovery() -> float | None:
    query = (
        'sum(increase(http_requests_total{job="inventory-service",'
        'handler=~"/(stock|reserve)",status=~"4xx|5xx"}[30s]))'
    )
    started = time.monotonic()
    consecutive_zeroes = 0
    while time.monotonic() - started < RECOVERY_TIMEOUT_SECONDS:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=10,
        )
        response.raise_for_status()
        values = response.json().get("data", {}).get("result", [])
        errors = float(values[0]["value"][1]) if values else 0
        if errors == 0:
            consecutive_zeroes += 1
            if consecutive_zeroes == 2:
                return time.monotonic() - started
        else:
            consecutive_zeroes = 0
        time.sleep(10)
    return None


def _load_scenarios() -> dict[str, dict]:
    path = ROOT / "faults" / "scenarios.yaml"
    with path.open(encoding="utf-8") as file:
        return {
            scenario["scenario_id"]: scenario
            for scenario in yaml.safe_load(file)["scenarios"]
        }


def _reset_local_inventory():
    result = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "sre",
            "-d",
            "sre",
            "-c",
            "UPDATE inventory SET stock=10000 WHERE item_id IN ('itemA','itemB');",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Could not reset the local demo inventory; verify the PostgreSQL Compose service."
        )


def run_evaluation(baseline_seconds: int, normal_samples: int) -> dict:
    if baseline_seconds < 360:
        raise ValueError("At least 360 seconds of baseline traffic is required.")
    if normal_samples < 2:
        raise ValueError("At least two normal observations are required.")

    _reset_local_inventory()
    try:
        return _run_live_evaluation(baseline_seconds, normal_samples)
    finally:
        _reset_local_inventory()


def _run_live_evaluation(
    baseline_seconds: int,
    normal_samples: int,
) -> dict:
    session = _session()
    load_process = _start_load(
        baseline_seconds + (normal_samples - 1) * 60
    )
    observations = []
    try:
        print(
            f"Building live baseline for {baseline_seconds} seconds.",
            flush=True,
        )
        time.sleep(baseline_seconds)
        normal_interval = 60
        for index in range(normal_samples):
            if index:
                time.sleep(normal_interval)
            report, latency_ms = _investigate(
                session,
                datetime.now(timezone.utc),
            )
            observations.append(
                _observation(
                    report,
                    latency_ms,
                    expected_anomaly=False,
                )
            )
        _wait_process(load_process, "baseline traffic generator")
    finally:
        if load_process.poll() is None:
            load_process.terminate()
            load_process.wait(timeout=10)

    scenarios = _load_scenarios()
    recoveries = []
    error_scenarios = 0
    error_evidence_hits = 0
    for scenario_id in (
        "inventory_latency_01",
        "inventory_error_01",
        "inventory_combined_01",
    ):
        scenario = scenarios[scenario_id]
        print(f"Running live scenario {scenario_id}.", flush=True)
        observation, recovery = _run_fault_scenario(scenario, session)
        observations.append(observation)
        if observation["expected_error_evidence"]:
            error_scenarios += 1
            error_evidence_hits += int(observation["error_evidence_detected"])
        if recovery is not None:
            recoveries.append(recovery)

    old_alert_time = datetime.now(timezone.utc) - timedelta(days=31)
    no_data_report, no_data_latency = _investigate(session, old_alert_time)
    no_data_observation = _observation(
        no_data_report,
        no_data_latency,
        expected_anomaly=False,
    )
    summary = summarize_detection(observations)
    return {
        "source": "live Docker Compose telemetry and controlled fault injection",
        "warning": "Small local scenario counts are demonstrations, not production-grade statistical guarantees.",
        "detection_metrics": summary,
        "error_evidence": {
            "expected_scenarios": error_scenarios,
            "detected_scenarios": error_evidence_hits,
            "recall": (
                error_evidence_hits / error_scenarios
                if error_scenarios
                else None
            ),
        },
        "recovery_seconds_after_fault_clear": recoveries,
        "insufficient_telemetry": {
            "status": no_data_report["detection"]["status"],
            "investigation_latency_ms": no_data_latency,
        },
        "observations": observations,
        "no_data_observation": no_data_observation,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate the investigator against live local traffic and faults."
    )
    parser.add_argument("--baseline-seconds", type=int, default=360)
    parser.add_argument("--normal-samples", type=int, default=3)
    args = parser.parse_args()
    try:
        result = run_evaluation(args.baseline_seconds, args.normal_samples)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
