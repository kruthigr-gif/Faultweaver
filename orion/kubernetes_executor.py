import copy
import json
import logging
import subprocess
from pathlib import Path

from .models import ActionPlan, PolicyCheck
from .staging_config import (
    STAGING_CLUSTER_NAME,
    STAGING_CONTEXT,
    STAGING_DEPLOYMENTS,
    STAGING_IMAGE,
    STAGING_NAMESPACE,
)


logger = logging.getLogger(__name__)
EXECUTOR_USER = (
    f"system:serviceaccount:{STAGING_NAMESPACE}:orion-executor"
)


class KubernetesExecutorError(RuntimeError):
    pass


class KubernetesExecutor:
    def __init__(self, kubeconfig: str | Path):
        self.kubeconfig = Path(kubeconfig).expanduser().resolve()
        if not self.kubeconfig.is_file():
            raise KubernetesExecutorError(
                "The isolated ORION staging kubeconfig does not exist."
            )

    @classmethod
    def from_env(cls):
        import os

        kubeconfig = os.getenv("ORION_KUBECONFIG")
        context = os.getenv("ORION_KUBE_CONTEXT")
        namespace = os.getenv("ORION_STAGING_NAMESPACE")
        cluster = os.getenv("ORION_STAGING_CLUSTER")
        if (
            not kubeconfig
            or context != STAGING_CONTEXT
            or namespace != STAGING_NAMESPACE
            or cluster != STAGING_CLUSTER_NAME
        ):
            raise KubernetesExecutorError(
                "ORION execution requires the dedicated local staging context and namespace."
            )
        return cls(kubeconfig)

    def _command(self, impersonate: bool) -> list[str]:
        command = [
            "kubectl",
            "--kubeconfig",
            str(self.kubeconfig),
            "--context",
            STAGING_CONTEXT,
            "--namespace",
            STAGING_NAMESPACE,
        ]
        if impersonate:
            command.extend(["--as", EXECUTOR_USER])
        return command

    def _run(
        self,
        *args: str,
        impersonate: bool = True,
        input_text: str | None = None,
        timeout: int = 30,
    ) -> str:
        try:
            result = subprocess.run(
                self._command(impersonate) + list(args),
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise KubernetesExecutorError(
                "kubectl is required for the local staging executor."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise KubernetesExecutorError(
                "A Kubernetes operation exceeded its safety timeout."
            ) from exc
        if result.returncode:
            logger.warning(
                "Staging kubectl command failed with exit code %s.",
                result.returncode,
            )
            raise KubernetesExecutorError(
                "A Kubernetes staging operation failed."
            )
        return result.stdout

    @staticmethod
    def _decode_object(value: str) -> dict:
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise KubernetesExecutorError(
                "Kubernetes returned an invalid object."
            ) from exc

    def _namespace_is_owned(self) -> bool:
        raw = self._run(
            "get",
            "namespace",
            STAGING_NAMESPACE,
            "-o",
            "json",
            impersonate=False,
        )
        namespace = self._decode_object(raw)
        labels = namespace.get("metadata", {}).get("labels", {})
        return labels.get("orion.sre/cluster") == STAGING_CLUSTER_NAME

    def _get_deployment(self, name: str) -> dict | None:
        raw = self._run(
            "get",
            "deployment",
            name,
            "-o",
            "json",
            "--ignore-not-found",
        )
        if not raw.strip():
            return None
        return self._decode_object(raw)

    def validate(self, plan: ActionPlan) -> PolicyCheck:
        target = plan.target
        if (
            target is None
            or target.cluster != STAGING_CLUSTER_NAME
            or target.namespace != STAGING_NAMESPACE
            or STAGING_DEPLOYMENTS.get(target.service) != target.deployment
        ):
            return PolicyCheck(
                name="staging_validation",
                passed=False,
                detail="Plan target is outside the fixed local staging allowlist.",
            )

        if not self._namespace_is_owned():
            return PolicyCheck(
                name="staging_validation",
                passed=False,
                detail="The target namespace is not marked as the isolated ORION staging namespace.",
            )

        deployment = self._get_deployment(target.deployment)
        if deployment is None:
            return PolicyCheck(
                name="staging_validation",
                passed=False,
                detail="The allowlisted staging deployment does not exist.",
            )

        metadata = deployment.get("metadata", {})
        spec = deployment.get("spec", {})
        status = deployment.get("status", {})
        labels = metadata.get("labels", {})
        replicas = spec.get("replicas", 0)
        strategy = spec.get("strategy", {})
        rolling = strategy.get("rollingUpdate", {})
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        images = {container.get("image") for container in containers}
        ready = (
            status.get("readyReplicas", 0) == replicas
            and status.get("updatedReplicas", 0) == replicas
        )
        validated = (
            labels.get("orion.sre/managed") == "true"
            and replicas >= 2
            and ready
            and strategy.get("type") == "RollingUpdate"
            and str(rolling.get("maxUnavailable")) in {"0", "0%"}
            and str(rolling.get("maxSurge")) in {"1", "1%"}
            and images == {STAGING_IMAGE}
        )
        if not validated:
            return PolicyCheck(
                name="staging_validation",
                passed=False,
                detail="Staging requires the managed local image, at least two ready replicas, and maxUnavailable=0/maxSurge=1.",
            )

        validation_patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "orion.sre/approved-plan-hash": plan.plan_hash
                        }
                    }
                }
            }
        }
        try:
            self._run(
                "patch",
                "deployment",
                target.deployment,
                "--type=merge",
                "-p",
                json.dumps(validation_patch),
                "--dry-run=server",
                "-o",
                "json",
            )
        except KubernetesExecutorError:
            return PolicyCheck(
                name="staging_validation",
                passed=False,
                detail="Kubernetes could not validate the proposed patch with server-side dry-run.",
            )
        return PolicyCheck(
            name="staging_validation",
            passed=True,
            detail=(
                "The isolated inventory deployment is healthy and the proposed patch passed Kubernetes server-side dry-run."
            ),
        )

    def _create_canary(self, plan: ActionPlan, canary_name: str):
        deployment = self._get_deployment(plan.target.deployment)
        if deployment is None:
            raise KubernetesExecutorError(
                "The staging deployment disappeared before canary creation."
            )

        template = copy.deepcopy(deployment["spec"]["template"])
        pod_metadata = template.setdefault("metadata", {})
        pod_labels = dict(pod_metadata.get("labels", {}))
        pod_labels.pop("app", None)
        pod_labels["orion.sre/canary"] = canary_name
        pod_metadata["labels"] = pod_labels
        canary = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": canary_name,
                "namespace": STAGING_NAMESPACE,
                "labels": {
                    "orion.sre/managed": "true",
                    "orion.sre/plan-id": plan.plan_id,
                },
            },
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": {"orion.sre/canary": canary_name}
                },
                "template": template,
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                },
            },
        }
        self._run(
            "apply",
            "-f",
            "-",
            input_text=json.dumps(canary),
        )

    def _delete_canary(self, canary_name: str):
        self._run(
            "delete",
            "deployment",
            canary_name,
            "--ignore-not-found=true",
            "--wait=true",
            timeout=60,
        )

    def _wait_ready(self, name: str, replicas: int, timeout: int):
        self._run(
            "rollout",
            "status",
            f"deployment/{name}",
            f"--timeout={timeout}s",
            timeout=timeout + 10,
        )
        deployment = self._get_deployment(name)
        if deployment is None:
            raise KubernetesExecutorError(
                "The staging deployment disappeared during health validation."
            )
        status = deployment.get("status", {})
        if (
            status.get("readyReplicas", 0) != replicas
            or status.get("updatedReplicas", 0) != replicas
        ):
            raise KubernetesExecutorError(
                "The staging deployment did not pass its readiness checks."
            )

    def _rollback_main(self, deployment_name: str, replicas: int, result: dict):
        result["rollback_performed"] = True
        try:
            self._run(
                "rollout",
                "undo",
                f"deployment/{deployment_name}",
                timeout=30,
            )
            self._wait_ready(deployment_name, replicas=replicas, timeout=120)
            result["status"] = "rolled_back"
            result["rollback_succeeded"] = True
            result["message"] = (
                "The staging rollout failed health checks and the previous revision was restored."
            )
        except KubernetesExecutorError:
            result["status"] = "rollback_failed"
            result["message"] = (
                "The staging rollout failed and automatic rollback could not be confirmed; operator intervention is required."
            )

    def execute(self, plan: ActionPlan) -> dict:
        validation = self.validate(plan)
        if not validation.passed:
            raise KubernetesExecutorError(
                "Staging validation no longer passes; no rollout was started."
            )

        deployment_name = plan.target.deployment
        canary_name = None
        result = {
            "status": "failed",
            "canary_ready": False,
            "rollout_succeeded": False,
            "rollback_performed": False,
            "rollback_succeeded": False,
            "cleanup_succeeded": True,
        }
        canary_name = f"inventory-canary-{plan.plan_id[-8:]}"
        try:
            self._create_canary(plan, canary_name)
            try:
                self._wait_ready(canary_name, replicas=1, timeout=90)
                result["canary_ready"] = True
            except KubernetesExecutorError:
                result["status"] = "canary_failed"
                result["message"] = (
                    "The canary did not become healthy; the serving staging deployment was not changed."
                )
            else:
                deployment = self._get_deployment(deployment_name)
                if deployment is None:
                    raise KubernetesExecutorError(
                        "The staging deployment disappeared before rollout."
                    )
                desired_replicas = deployment.get("spec", {}).get("replicas", 0)
                if desired_replicas < 2:
                    raise KubernetesExecutorError(
                        "The staging deployment no longer meets the replica safety minimum."
                    )
                patch = {
                    "metadata": {
                        "resourceVersion": deployment["metadata"]["resourceVersion"]
                    },
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "orion.sre/approved-plan-hash": plan.plan_hash
                                }
                            }
                        }
                    },
                }
                try:
                    self._run(
                        "patch",
                        "deployment",
                        deployment_name,
                        "--type=merge",
                        "-p",
                        json.dumps(patch),
                    )
                except KubernetesExecutorError:
                    current = self._get_deployment(deployment_name)
                    annotations = (
                        current.get("spec", {})
                        .get("template", {})
                        .get("metadata", {})
                        .get("annotations", {})
                        if current is not None
                        else {}
                    )
                    if annotations.get("orion.sre/approved-plan-hash") != plan.plan_hash:
                        raise
                    self._rollback_main(
                        deployment_name,
                        desired_replicas,
                        result,
                    )
                else:
                    try:
                        self._wait_ready(
                            deployment_name,
                            replicas=desired_replicas,
                            timeout=120,
                        )
                        result["status"] = "succeeded"
                        result["rollout_succeeded"] = True
                        result["message"] = (
                            "Canary health passed and the approved staging rolling restart completed."
                        )
                    except KubernetesExecutorError:
                        self._rollback_main(
                            deployment_name,
                            desired_replicas,
                            result,
                        )
        finally:
            try:
                self._delete_canary(canary_name)
            except KubernetesExecutorError:
                result["cleanup_succeeded"] = False
                result["status"] = "cleanup_failed"
                result["message"] = (
                    "The canary could not be removed automatically; inspect the isolated staging namespace."
                )
        return result
