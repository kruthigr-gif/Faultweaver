import os
import sys
from pathlib import Path
from urllib.parse import quote

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orion.staging_config import (
    STAGING_CLUSTER_NAME,
    STAGING_CONTEXT,
    STAGING_NAMESPACE,
)

ENV_FILE = ROOT / ".env"


def _read_local_env() -> dict[str, str]:
    values = {}
    if not ENV_FILE.is_file():
        raise RuntimeError("Run scripts/start_local.py first to create local credentials.")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def main():
    values = _read_local_env()
    password = values.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("POSTGRES_PASSWORD is missing from the ignored local .env file.")

    kubeconfig = (
        Path(os.environ["LOCALAPPDATA"])
        / "SentinelSRE"
        / "kind-staging.kubeconfig"
    )
    if not kubeconfig.is_file():
        raise RuntimeError(
            "Run scripts/prepare_kind_staging.py before starting the staging API."
        )

    os.environ.update(
        {
            "DATABASE_URL": (
                "postgresql://sre:"
                + quote(password, safe="")
                + "@127.0.0.1:5432/sre"
            ),
            "INVESTIGATOR_URL": "http://127.0.0.1:9000",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4317",
            "AUTH_ENABLED": "true",
            "OIDC_ISSUER": "http://localhost:8081/realms/sre-local",
            "OIDC_AUDIENCE": "sre-api",
            "OIDC_JWKS_URL": (
                "http://127.0.0.1:8081/realms/sre-local/"
                "protocol/openid-connect/certs"
            ),
            "OIDC_ALGORITHM": "RS256",
            "ORION_EXECUTION_ENABLED": "true",
            "ORION_KUBECONFIG": str(kubeconfig),
            "ORION_KUBE_CONTEXT": STAGING_CONTEXT,
            "ORION_STAGING_NAMESPACE": STAGING_NAMESPACE,
            "ORION_STAGING_CLUSTER": STAGING_CLUSTER_NAME,
        }
    )
    print("Starting the approval-gated ORION staging API on http://127.0.0.1:9101.")
    print("Execution is restricted to the isolated local kind staging namespace.")
    uvicorn.run("orion.app:app", host="127.0.0.1", port=9101)


if __name__ == "__main__":
    main()
