import os
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_FILE = Path(__file__).with_name("scenarios.yaml")
FAULT_COMPOSE_FILE = Path(__file__).with_name("fault-injection.yml")


def load_scenarios():
    with SCENARIOS_FILE.open(encoding="utf-8") as scenario_file:
        return yaml.safe_load(scenario_file)["scenarios"]


def find_scenario(scenario_id):
    for scenario in load_scenarios():
        if scenario["scenario_id"] == scenario_id:
            return scenario
    raise ValueError(f"Scenario {scenario_id!r} not found")


def run_compose(service, latency_ms, error_code):
    environment = os.environ.copy()
    environment["LATENCY_MS"] = str(latency_ms)
    environment["FORCE_ERROR"] = str(error_code) if error_code else ""
    command = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        str(FAULT_COMPOSE_FILE.relative_to(PROJECT_ROOT)),
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        service,
    ]
    subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)


def wait_for_service(timeout_seconds=30):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = requests.get("http://127.0.0.1:8001/health", timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    raise TimeoutError("inventory-service did not become healthy within 30 seconds")


def inject_fault(scenario_id):
    scenario = find_scenario(scenario_id)
    if scenario["fault_service"] != "inventory-service":
        raise ValueError("Fault scenarios currently support inventory-service only")

    fault_type = scenario["fault_type"]
    latency_ms = 0
    error_code = None
    if fault_type == "downstream_latency":
        latency_ms = int(scenario.get("latency_ms", 1000))
    elif fault_type == "error_response":
        error_code = int(scenario.get("error_code", 500))
    elif fault_type == "combined_latency_error":
        latency_ms = int(scenario.get("latency_ms", 1000))
        error_code = int(scenario.get("error_code", 500))
    else:
        raise ValueError(f"Unsupported fault type: {fault_type}")

    duration = int(scenario["duration_seconds"])
    if duration <= 0:
        raise ValueError("Fault duration must be positive")

    start_offset = int(scenario.get("start_offset_seconds", 0))
    if start_offset > 0:
        print(f"Waiting {start_offset}s before injecting {scenario_id}.")
        time.sleep(start_offset)

    try:
        run_compose(scenario["fault_service"], latency_ms, error_code)
        wait_for_service()
        print(f"Injected {scenario_id} for {duration}s.")
        time.sleep(duration)
    finally:
        run_compose("inventory-service", 0, None)
        wait_for_service()
        print("Fault cleared; inventory-service restarted with default settings.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python inject.py <scenario_id>", file=sys.stderr)
        sys.exit(2)
    try:
        inject_fault(sys.argv[1])
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"Fault injection failed: {exc}", file=sys.stderr)
        sys.exit(1)
