from pathlib import Path
import time
import requests


BASE_URL = "http://127.0.0.1:9000"
TOKEN_URL = "http://127.0.0.1:8081/realms/sre-local/protocol/openid-connect/token"


def local_env():
    env_path = Path(__file__).resolve().parents[2] / ".env"
    values = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    return values


def get_local_token():
    values = local_env()
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "client_id": "sre-api",
            "username": values["SRE_LOCAL_USERNAME"],
            "password": values["SRE_LOCAL_PASSWORD"],
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def wait_for_service(url, timeout=30):
    """Poll the given URL until it returns a 200 or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def test_investigator_end_to_end():
    # Ensure the investigator API is reachable
    assert wait_for_service(f"{BASE_URL}/health"), "Investigator health endpoint not reachable"

    assert requests.post(
        f"{BASE_URL}/investigate",
        json={"alert_time": "2026-01-01T00:00:00Z", "service": "order-service"},
        timeout=10,
    ).status_code == 401
    headers = {"Authorization": f"Bearer {get_local_token()}"}

    # Send an investigation request (using a recent timestamp)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "alert_time": now,
        "service": "order-service",
        "window_seconds": 300,
    }
    resp = requests.post(f"{BASE_URL}/investigate", json=payload, headers=headers)
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    # Basic sanity checks on the response structure
    assert "ranked_causes" in data, "Response missing 'ranked_causes' key"
    assert data["detection"]["status"] in {
        "anomaly",
        "normal",
        "insufficient_data",
    }
    assert data["evidence"], "Response should include collected telemetry evidence"
    # The primary service should appear in the ranking (even with zero evidence)
    services = [item["service"] for item in data["ranked_causes"]]
    assert "order-service" in services
