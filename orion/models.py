from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ServiceName = Literal["order-service", "inventory-service", "payment-service"]


class IncidentRequest(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    environment: Literal["local-compose"] = "local-compose"
    service: ServiceName
    alert_time: datetime
    window_seconds: int = Field(default=300, ge=60, le=3600)


class Detection(BaseModel):
    status: Literal["anomaly", "normal", "insufficient_data"]
    current_latency_seconds: float | None = None
    baseline_latency_seconds: float | None = None
    baseline_samples: int = Field(ge=0)
    threshold_seconds: float | None = None
    message: str


class Evidence(BaseModel):
    evidence_id: str
    source: Literal["prometheus", "loki", "tempo"]
    service: ServiceName
    signal: str
    window_start: str
    window_end: str
    observation: str
    reference: str
    score: float = 0
    available: bool = True


class RankedCause(BaseModel):
    service: ServiceName
    cause: str
    confidence: Literal["high", "medium", "low"]
    evidence_ids: list[str]


class InvestigationReport(BaseModel):
    incident_id: str
    detected_at: datetime
    affected_services: list[str]
    detection: Detection
    evidence: list[Evidence]
    ranked_causes: list[RankedCause]
    alternatives: list[str]
    unknowns: list[str]
    recommended_checks: list[str]
    remediation: dict[str, str]


class IncidentRecord(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    incident_id: str
    fingerprint: str
    environment: Literal["local-compose"]
    service: ServiceName
    status: Literal["investigating", "investigate_only", "no_action"]
    alert_time: datetime
    window_seconds: int
    investigation: InvestigationReport
    created_at: datetime


class PlanTarget(BaseModel):
    cluster: Literal["sentinelsre-local", "sentinelsre-staging"]
    namespace: Literal["sre-staging"]
    deployment: str
    service: ServiceName


class PolicyCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class PolicyDecision(BaseModel):
    decision: Literal["no_action", "blocked", "approval_required"]
    execution_enabled: bool = False
    checks: list[PolicyCheck]
    reasons: list[str]


class PlanProposal(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: str
    plan_hash: str
    incident_id: str
    action: Literal["investigate_only", "restart_deployment"]
    target: PlanTarget | None = None
    rationale: str
    preconditions: list[str]
    risk: Literal["none", "low", "medium"]
    created_at: datetime


class ActionPlan(PlanProposal):
    policy: PolicyDecision


class DryRunResult(BaseModel):
    plan_id: str
    decision: Literal["no_action", "blocked", "approval_required"]
    executed: Literal[False] = False
    would_mutate_cluster: Literal[False] = False
    checks: list[PolicyCheck]
    reasons: list[str]
    message: str


class PlanApprovalRequest(BaseModel):
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlanApproval(BaseModel):
    plan_id: str
    plan_hash: str
    approved: Literal[True] = True
    approved_by: str
    approved_at: datetime


class PlanExecutionResult(BaseModel):
    plan_id: str
    status: Literal[
        "succeeded",
        "canary_failed",
        "rolled_back",
        "rollback_failed",
        "cleanup_failed",
        "failed",
    ]
    executed: Literal[True] = True
    canary_ready: bool
    rollout_succeeded: bool
    rollback_performed: bool
    rollback_succeeded: bool
    cleanup_succeeded: bool
    message: str


class PlanState(BaseModel):
    plan_id: str
    status: Literal[
        "blocked",
        "no_action",
        "pending_approval",
        "approved",
        "executing",
        "succeeded",
        "canary_failed",
        "rolled_back",
        "rollback_failed",
        "cleanup_failed",
        "failed",
    ]
    approved_by: str | None = None
    approved_at: datetime | None = None
    execution_result: PlanExecutionResult | None = None


class AuditEvent(BaseModel):
    event_id: int
    incident_id: str
    plan_id: str | None = None
    event_type: str
    actor_id: str | None = None
    details: dict
    created_at: datetime
