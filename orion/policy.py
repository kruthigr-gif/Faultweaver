from datetime import datetime, timezone

from .models import IncidentRecord, PlanProposal, PolicyCheck, PolicyDecision


def evaluate_plan(incident: IncidentRecord, plan: PlanProposal) -> PolicyDecision:
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
                and plan.target.cluster == "sentinelsre-local"
                and plan.target.namespace == "sre-staging"
                and plan.target.service in {
                    "order-service",
                    "inventory-service",
                    "payment-service",
                }
            ),
            detail="Target must match the fixed local cluster and staging namespace.",
        ),
        PolicyCheck(
            name="fresh_evidence",
            passed=evidence_is_fresh,
            detail="Investigation evidence must be no more than five minutes old.",
        ),
        PolicyCheck(
            name="staging_validation",
            passed=False,
            detail="Kubernetes staging validation is not implemented in this release.",
        ),
        PolicyCheck(
            name="executor",
            passed=False,
            detail="No Kubernetes executor is configured; mutations are disabled.",
        ),
    ]
    failed = [check.name for check in checks if not check.passed]
    return PolicyDecision(
        decision="blocked",
        checks=checks,
        reasons=[
            "Mutating actions are unavailable in this observe-and-dry-run release.",
            "Failed checks: " + ", ".join(failed),
        ],
    )
