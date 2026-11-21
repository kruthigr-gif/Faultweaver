import json

from orion.kubernetes_executor import KubernetesExecutor, KubernetesExecutorError
from orion.models import ActionPlan, PlanTarget
from orion.staging_config import (
    STAGING_CLUSTER_NAME,
    STAGING_IMAGE,
    STAGING_NAMESPACE,
)


def make_plan():
    return ActionPlan(
        plan_id="plan-1234567890abcdef",
        plan_hash="a" * 64,
        incident_id="inc-local",
        action="restart_deployment",
        target=PlanTarget(
            cluster=STAGING_CLUSTER_NAME,
            namespace=STAGING_NAMESPACE,
            deployment="inventory-service",
            service="inventory-service",
        ),
        rationale="Test plan.",
        preconditions=[],
        risk="medium",
        policy={
            "decision": "approval_required",
            "execution_enabled": False,
            "checks": [],
            "reasons": [],
        },
        created_at="2026-11-21T00:00:00Z",
    )


def make_deployment():
    return {
        "metadata": {
            "labels": {"orion.sre/managed": "true"},
            "resourceVersion": "42",
        },
        "spec": {
            "replicas": 2,
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
            },
            "template": {
                "metadata": {"labels": {"app": "inventory-service"}},
                "spec": {
                    "containers": [
                        {
                            "name": "inventory",
                            "image": STAGING_IMAGE,
                            "readinessProbe": {
                                "httpGet": {"path": "/health", "port": 80}
                            },
                        }
                    ]
                },
            },
        },
        "status": {"readyReplicas": 2, "updatedReplicas": 2},
    }


class FakeKubectlExecutor(KubernetesExecutor):
    def __init__(self, kubeconfig):
        super().__init__(kubeconfig)
        self.commands = []
        self.waits = []
        self.fail_canary = False
        self.fail_main_rollout = False

    def validate(self, plan):
        return super().validate(plan)

    def _namespace_is_owned(self):
        return True

    def _get_deployment(self, name):
        if name == "inventory-service":
            return make_deployment()
        return {"status": {"readyReplicas": 1, "updatedReplicas": 1}}

    def _run(self, *args, **kwargs):
        self.commands.append((args, kwargs))
        if args[:2] == ("get", "namespace"):
            return json.dumps(
                {"metadata": {"labels": {"orion.sre/cluster": STAGING_CLUSTER_NAME}}}
            )
        return ""

    def _wait_ready(self, name, replicas, timeout):
        self.waits.append((name, replicas, timeout))
        if name.startswith("inventory-canary-") and self.fail_canary:
            raise KubernetesExecutorError("Canary failed.")
        if name == "inventory-service" and self.fail_main_rollout:
            if sum(wait[0] == name for wait in self.waits) == 1:
                raise KubernetesExecutorError("Rollout failed.")


def test_staging_validation_rejects_unhealthy_or_unmanaged_target(tmp_path):
    kubeconfig = tmp_path / "kind.kubeconfig"
    kubeconfig.write_text("test-only")
    executor = FakeKubectlExecutor(kubeconfig)

    check = executor.validate(make_plan())

    assert check.passed is True
    deployment = make_deployment()
    deployment["status"]["readyReplicas"] = 1
    executor._get_deployment = lambda _: deployment
    assert executor.validate(make_plan()).passed is False


def test_canary_failure_does_not_restart_the_staging_deployment(tmp_path):
    kubeconfig = tmp_path / "kind.kubeconfig"
    kubeconfig.write_text("test-only")
    executor = FakeKubectlExecutor(kubeconfig)
    executor.fail_canary = True

    result = executor.execute(make_plan())

    assert result["status"] == "canary_failed"
    assert result["rollout_succeeded"] is False
    assert any(command[0][:2] == ("delete", "deployment") for command in executor.commands)
    assert not any(
        command[0][:2] == ("patch", "deployment")
        and "--dry-run=server" not in command[0]
        for command in executor.commands
    )


def test_failed_rollout_automatically_restores_the_prior_revision(tmp_path):
    kubeconfig = tmp_path / "kind.kubeconfig"
    kubeconfig.write_text("test-only")
    executor = FakeKubectlExecutor(kubeconfig)
    executor.fail_main_rollout = True

    result = executor.execute(make_plan())

    assert result["status"] == "rolled_back"
    assert result["rollback_performed"] is True
    assert result["rollback_succeeded"] is True
    assert any(command[0][:2] == ("rollout", "undo") for command in executor.commands)
