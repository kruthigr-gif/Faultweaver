import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orion.staging_config import (
    STAGING_CLUSTER_NAME,
    STAGING_CONTEXT,
    STAGING_NAMESPACE,
    STAGING_IMAGE,
)


def _run(command: list[str], cwd: Path = ROOT):
    subprocess.run(command, cwd=cwd, check=True)


def _kubeconfig_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is required for the Windows staging kubeconfig.")
    directory = Path(local_app_data) / "SentinelSRE"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "kind-staging.kubeconfig"


def _kind_clusters() -> set[str]:
    result = subprocess.run(
        ["kind", "get", "clusters"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def main():
    kubeconfig = _kubeconfig_path()
    clusters = _kind_clusters()
    if STAGING_CLUSTER_NAME in clusters:
        if not kubeconfig.is_file():
            raise RuntimeError(
                "The staging cluster already exists but its isolated kubeconfig is missing; "
                "refusing to alter another cluster."
            )
    else:
        _run(
            [
                "kind",
                "create",
                "cluster",
                "--name",
                STAGING_CLUSTER_NAME,
                "--kubeconfig",
                str(kubeconfig),
                "--wait",
                "120s",
            ]
        )

    _run(
        [
            "docker",
            "build",
            "-t",
            STAGING_IMAGE,
            "-f",
            "services/inventory_service/Dockerfile",
            "services",
        ]
    )
    _run(
        [
            "kind",
            "load",
            "docker-image",
            STAGING_IMAGE,
            "--name",
            STAGING_CLUSTER_NAME,
        ]
    )
    _run(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            "--context",
            STAGING_CONTEXT,
            "apply",
            "-k",
            "orion/k8s/staging",
        ]
    )
    _run(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            "--context",
            STAGING_CONTEXT,
            "--namespace",
            STAGING_NAMESPACE,
            "rollout",
            "restart",
            "deployment/inventory-service",
        ]
    )
    _run(
        [
            "kubectl",
            "--kubeconfig",
            str(kubeconfig),
            "--context",
            STAGING_CONTEXT,
            "--namespace",
            STAGING_NAMESPACE,
            "rollout",
            "status",
            "deployment/inventory-service",
            "--timeout=180s",
        ]
    )
    print("Isolated SentinelSRE staging workload is healthy.")
    print(f"Kubeconfig: {kubeconfig}")
    print("The existing kubectl context was not changed.")


if __name__ == "__main__":
    main()
