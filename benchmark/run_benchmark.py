import datetime
import json
import subprocess
import sys
from pathlib import Path

import requests

from loadgen.run_load import get_local_token


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INVESTIGATOR_URL = "http://127.0.0.1:9000"
LOAD_GENERATOR = PROJECT_ROOT / "loadgen" / "run_load.py"
FAULT_INJECTOR = PROJECT_ROOT / "faults" / "inject.py"


def start_load(duration: int):
    return subprocess.Popen(
        [
            sys.executable,
            str(LOAD_GENERATOR),
            "--rps",
            "2",
            "--duration",
            str(duration),
            "--workers",
            "1",
        ],
        cwd=PROJECT_ROOT,
    )


def run_baseline():
    print("Generating five minutes of baseline order traffic...")
    result = subprocess.run(
        [
            sys.executable,
            str(LOAD_GENERATOR),
            "--rps",
            "2",
            "--duration",
            "300",
            "--workers",
            "1",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    return result.returncode


def call_investigator(alert_time: str, service: str = "order-service", window: int = 300):
    token = get_local_token()
    response = requests.post(
        f"{INVESTIGATOR_URL}/investigate",
        json={
            "alert_time": alert_time,
            "service": service,
            "window_seconds": window,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=(2, 30),
    )
    response.raise_for_status()
    return response.json()


def main():
    run_baseline()
    workload = start_load(duration=120)
    try:
        print("Injecting inventory latency while order traffic continues...")
        subprocess.run(
            [
                sys.executable,
                str(FAULT_INJECTOR),
                "inventory_latency_01",
            ],
            cwd=PROJECT_ROOT,
            check=True,
        )

        alert_time = (
            datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        report = call_investigator(alert_time)
        print(json.dumps(report, indent=2))
        if report["detection"]["status"] != "anomaly":
            raise RuntimeError(
                "The injected latency was not detected: "
                f"{report['detection']['status']}"
            )
        print("Benchmark passed: the investigator detected the latency anomaly.")
    finally:
        workload.wait()
        if workload.returncode:
            raise subprocess.CalledProcessError(workload.returncode, workload.args)


if __name__ == "__main__":
    main()
