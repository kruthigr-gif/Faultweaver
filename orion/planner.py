import hashlib
import json
import uuid
from datetime import datetime, timezone

from .models import IncidentRecord, PlanProposal, PlanTarget
from .staging_config import STAGING_CLUSTER_NAME, STAGING_DEPLOYMENTS, STAGING_NAMESPACE


def _has_corroborating_error_evidence(
    incident: IncidentRecord,
    service: str,
) -> bool:
    positive_signals = {
        evidence.signal
        for evidence in incident.investigation.evidence
        if evidence.available
        and evidence.service == service
        and evidence.score > 0
    }
    return {"errors", "error_logs"}.issubset(positive_signals)


def _plan_hash(
    incident_id: str,
    action: str,
    target: PlanTarget | None,
    rationale: str,
    preconditions: list[str],
    risk: str,
) -> str:
    value = {
        "schema_version": "1.0",
        "incident_id": incident_id,
        "action": action,
        "target": target.model_dump(mode="json") if target else None,
        "rationale": rationale,
        "preconditions": preconditions,
        "risk": risk,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def propose_plan(incident: IncidentRecord) -> PlanProposal:
    lead = incident.investigation.ranked_causes[0] if incident.investigation.ranked_causes else None
    candidate_service = lead.service if lead else incident.service
    corroborated_error = _has_corroborating_error_evidence(
        incident,
        candidate_service,
    )
    can_propose_restart = (
        incident.investigation.detection.status == "anomaly"
        and lead is not None
        and candidate_service in STAGING_DEPLOYMENTS
        and lead.confidence == "high"
        and corroborated_error
    )

    if not can_propose_restart:
        action = "investigate_only"
        target = None
        rationale = (
            "Current evidence does not meet the deterministic threshold for an action proposal. "
            "Continue investigation; no service change is proposed."
        )
        preconditions = ["Collect fresh, corroborating telemetry before considering remediation."]
        risk = "none"
    else:
        action = "restart_deployment"
        target = PlanTarget(
            cluster=STAGING_CLUSTER_NAME,
            namespace=STAGING_NAMESPACE,
            deployment=STAGING_DEPLOYMENTS[candidate_service],
            service=candidate_service,
        )
        rationale = (
            "A high-confidence lead has corroborating HTTP-error and error-log signals. "
            "This is only a candidate; those signals do not prove that a restart is safe."
        )
        preconditions = [
            "Confirm a failing readiness or rollout condition in the target cluster.",
            "Confirm a healthy prior revision and sufficient ready replicas.",
            "Validate the exact plan in an isolated staging namespace.",
            "Obtain explicit approval bound to this plan hash.",
        ]
        risk = "medium"

    plan_hash = _plan_hash(
        incident.incident_id,
        action,
        target,
        rationale,
        preconditions,
        risk,
    )
    return PlanProposal(
        plan_id=f"plan-{uuid.uuid4().hex[:12]}",
        plan_hash=plan_hash,
        incident_id=incident.incident_id,
        action=action,
        target=target,
        rationale=rationale,
        preconditions=preconditions,
        risk=risk,
        created_at=datetime.now(timezone.utc),
    )
