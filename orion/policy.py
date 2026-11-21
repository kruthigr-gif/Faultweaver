from datetime import datetime, timezone

from .models import IncidentRecord, PlanProposal, PolicyCheck, PolicyDecision
from .staging_config import (
    STAGING_CLUSTER_NAME,
    STAGING_DEPLOYMENTS,
    STAGING_NAMESPACE,
)


def evaluate_plan(
    incident: IncidentRecord,
    plan: PlanProposal,
    staging_validation: PolicyCheck | None = None,
    executor_enabled: bool = False,
) -> PolicyDecision:
    if plan.action == "investigate_only":
        return PolicyDecision(
            decision="no_action",
            checks=[
                PolicyCheck(
                    name="mutation",
                    passed=True,
                    detail="No mutating action is proposed.",
                )
            ],
            reasons=["The evidence does not justify an automated action."],
        )

    detected_at = incident.investigation.detected_at
    evidence_is_fresh = (
        detected_at.tzinfo is not None
        and 0
        <= (
            datetime.now(timezone.utc)
            - detected_at.astimezone(timezone.utc)
        ).total_seconds()
        <= 300
    )
    checks = [
        PolicyCheck(
            name="environment",
            passed=incident.environment == "local-compose",
            detail="Incident evidence originated from the local Compose demo.",
        ),
        PolicyCheck(
            name="action_allowlist",
            passed=plan.action == "restart_deployment",
            detail="Only the fixed restart_deployment proposal is recognized.",
        ),
        PolicyCheck(
            name="fixed_target",
            passed=(
                plan.target is not None
                and plan.target.cluster == STAGING_CLUSTER_NAME
                and plan.target.namespace == STAGING_NAMESPACE
                and STAGING_DEPLOYMENTS.get(plan.target.service)
                == plan.target.deployment
            ),
            detail="Target must match the fixed local inventory deployment and staging namespace.",
        ),
        PolicyCheck(
            name="fresh_evidence",
            passed=evidence_is_fresh,
            detail="Investigation evidence must be no more than five minutes old.",
        ),
        staging_validation
        or PolicyCheck(
            name="staging_validation",
            passed=False,
            detail="No isolated Kubernetes staging validation is configured.",
        ),
        PolicyCheck(
            name="executor",
            passed=executor_enabled,
            detail=(
                "The isolated staging executor is configured; explicit approval is still required."
                if executor_enabled
                else "No Kubernetes executor is configured; mutations are disabled."
            ),
        ),
    ]
    failed = [check.name for check in checks if not check.passed]
    return PolicyDecision(
        decision="approval_required" if not failed else "blocked",
        checks=checks,
        reasons=[
            (
                "All automatic checks passed; explicit human approval is required."
                if not failed
                else "The mutating action is blocked by policy."
            ),
            *(
                ["Failed checks: " + ", ".join(failed)]
                if failed
                else []
            ),
        ],
    )
