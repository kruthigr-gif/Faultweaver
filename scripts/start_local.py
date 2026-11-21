import secrets
import subprocess
import sys
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
SECRETS = (
    "GRAFANA_ADMIN_PASSWORD",
    "POSTGRES_PASSWORD",
    "KEYCLOAK_ADMIN_PASSWORD",
    "SRE_LOCAL_PASSWORD",
)


def read_env():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
    return values


def write_env(values):
    lines = [
        "# Local-only secrets. Do not commit this file.",
        "GRAFANA_ADMIN_USER=admin",
        "KEYCLOAK_ADMIN=localadmin",
        "SRE_LOCAL_USERNAME=local-user",
    ]
    lines.extend(f"{key}={values[key]}" for key in SECRETS)
    temporary = ENV_FILE.with_suffix(".env.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(ENV_FILE)


def compose(*args, check=True):
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def prepare_local_secrets():
    values = read_env()
    changed = False
    for key in SECRETS:
        value = values.get(key, "")
        if not value or value.startswith("replace-with-") or value in {"admin", "change-me"}:
            values[key] = secrets.token_urlsafe(32)
            changed = True
    values["GRAFANA_ADMIN_USER"] = "admin"
    values["KEYCLOAK_ADMIN"] = "localadmin"
    values["SRE_LOCAL_USERNAME"] = "local-user"
    if changed or not ENV_FILE.exists():
        write_env(values)
    return values, changed


def wait_for_keycloak(timeout=240):
    url = "http://127.0.0.1:8081/realms/sre-local/.well-known/openid-configuration"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=3)
            if response.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(3)
    raise TimeoutError("Local Keycloak did not become ready within four minutes")


def provision_local_user(values):
    base = "http://127.0.0.1:8081"
    admin = requests.post(
        f"{base}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": "localadmin",
            "password": values["KEYCLOAK_ADMIN_PASSWORD"],
        },
        timeout=15,
    )
    admin.raise_for_status()
    headers = {"Authorization": f"Bearer {admin.json()['access_token']}"}
    realm_api = f"{base}/admin/realms/sre-local/users"
    lookup = requests.get(
        realm_api,
        params={"username": "local-user", "exact": "true"},
        headers=headers,
        timeout=15,
    )
    lookup.raise_for_status()
    users = lookup.json()
    credentials = {
        "type": "password",
        "value": values["SRE_LOCAL_PASSWORD"],
        "temporary": False,
    }
    if users:
        user_id = users[0]["id"]
        response = requests.put(
            f"{realm_api}/{user_id}",
            headers=headers,
            json={
                "enabled": True,
                "email": "local-user@localhost",
                "emailVerified": True,
                "firstName": "Local",
                "lastName": "User",
                "requiredActions": [],
            },
            timeout=15,
        )
        response.raise_for_status()
        response = requests.put(
            f"{realm_api}/{user_id}/reset-password",
            headers=headers,
            json=credentials,
            timeout=15,
        )
    else:
        response = requests.post(
            realm_api,
            headers=headers,
            json={
                "username": "local-user",
                "enabled": True,
                "email": "local-user@localhost",
                "emailVerified": True,
                "firstName": "Local",
                "lastName": "User",
                "requiredActions": [],
                "credentials": [credentials],
            },
            timeout=15,
        )
    response.raise_for_status()


def main():
    values, rotated = prepare_local_secrets()

    if rotated:
        current = compose("ps", "--status", "running", "-q", "postgres", check=False)
        if current.stdout.strip():
            compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "sre",
                "-d",
                "sre",
                "-c",
                f"ALTER USER sre WITH PASSWORD '{values['POSTGRES_PASSWORD']}';",
            )

    compose("up", "--build", "-d")

    if rotated:
        compose(
            "exec",
            "-T",
            "grafana",
            "grafana-cli",
            "admin",
            "reset-admin-password",
            values["GRAFANA_ADMIN_PASSWORD"],
        )

    wait_for_keycloak()
    provision_local_user(values)
    print("Local SRE stack is ready.")
    print("Keycloak admin username: localadmin")
    print("Application username: local-user")
    print("Generated passwords are stored in the ignored .env file.")
    print("Open README.md for local sign-in and API instructions.")


if __name__ == "__main__":
    try:
        main()
    except (requests.RequestException, subprocess.CalledProcessError, TimeoutError) as exc:
        print(f"Local setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
