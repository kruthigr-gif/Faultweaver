import asyncio
import asyncpg
import hashlib
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from prometheus_fastapi_instrumentator import Instrumentator

from .auth import bearer_scheme, require_user
from .investigator_client import InvestigatorClient
from .models import (
    ActionPlan,
    AuditEvent,
    DryRunResult,
    IncidentRecord,
    IncidentRequest,
    PlanApproval,
    PlanApprovalRequest,
    PlanExecutionResult,
    PlanState,
)
from .observability import configure_observability
from .planner import propose_plan
from .policy import evaluate_plan
from .repository import PostgresRepository
from .kubernetes_executor import KubernetesExecutor, KubernetesExecutorError


logger = logging.getLogger(__name__)


def _execution_enabled() -> bool:
    return os.getenv("ORION_EXECUTION_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }


def _actor_id(claims) -> str:
    actor_id = claims.get("sub") if isinstance(claims, dict) else None
    if not actor_id:
        raise HTTPException(
            status_code=401,
            detail="An authenticated user is required for approval and execution.",
        )
    return actor_id


async def _recheck_policy(request: Request, incident, plan):
    executor = request.app.state.executor
    execution_enabled = _execution_enabled() and executor is not None
    staging_validation = None
    if execution_enabled:
        try:
            staging_validation = await asyncio.to_thread(
                executor.validate,
                plan,
            )
        except KubernetesExecutorError as exc:
            logger.warning(
                "ORION staging validation unavailable for plan_id=%s",
                plan.plan_id,
            )
            raise HTTPException(
                status_code=503,
                detail="Staging validation is unavailable; no execution was started.",
            ) from exc
    return evaluate_plan(
        incident,
        plan,
        staging_validation=staging_validation,
        executor_enabled=execution_enabled,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = None
    investigator_client = app.state.investigator_client
    owns_investigator_client = investigator_client is None
    try:
        if app.state.repository is None:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise RuntimeError("DATABASE_URL must be configured for ORION.")
            pool = await asyncpg.create_pool(
                database_url.replace("postgres://", "postgresql://", 1),
                min_size=1,
                max_size=5,
            )
            repository = PostgresRepository(pool)
            await repository.initialize()
            app.state.repository = repository

        if investigator_client is None:
            investigator_client = InvestigatorClient(
                os.getenv("INVESTIGATOR_URL", "http://investigator:80")
            )
            app.state.investigator_client = investigator_client

        if app.state.executor is None and _execution_enabled():
            app.state.executor = KubernetesExecutor.from_env()

        yield
    finally:
        if pool is not None:
            await pool.close()
        if owns_investigator_client and investigator_client is not None:
            await investigator_client.close()


def create_app(repository=None, investigator_client=None, executor=None) -> FastAPI:
    app = FastAPI(
        title="ORION Incident Control Plane",
        description=(
            "Authenticated incident intake, approval-gated canary validation, "
            "and isolated staging execution."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.investigator_client = investigator_client
    app.state.executor = executor

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request):
        repository = request.app.state.repository
        if repository is None:
            raise HTTPException(status_code=503, detail="ORION storage is not ready.")
        try:
            if not await repository.ping():
                raise HTTPException(status_code=503, detail="ORION storage is not ready.")
        except asyncpg.PostgresError as exc:
            raise HTTPException(status_code=503, detail="ORION storage is unavailable.") from exc
        return {"status": "ready"}

    @app.post("/orion/incidents", response_model=IncidentRecord)
    async def create_incident(
        body: IncidentRequest,
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        claims=Depends(require_user),
    ):
        alert_time = body.alert_time
        if alert_time.tzinfo is None:
            alert_time = alert_time.replace(tzinfo=timezone.utc)
        else:
            alert_time = alert_time.astimezone(timezone.utc)
        if alert_time > datetime.now(timezone.utc) + timedelta(seconds=60):
            raise HTTPException(status_code=422, detail="alert_time cannot be in the future")

        normalized = body.model_copy(update={"alert_time": alert_time})
        authorization = (
            f"{credentials.scheme} {credentials.credentials}"
            if credentials is not None
            else None
        )
        report = await request.app.state.investigator_client.investigate(
            normalized,
            authorization,
        )
        bucket = int(alert_time.timestamp()) // body.window_seconds
        fingerprint = hashlib.sha256(
            f"{body.environment}:{body.service}:{bucket}".encode("utf-8")
        ).hexdigest()
        incident = IncidentRecord(
            incident_id=f"inc-{uuid.uuid4().hex[:12]}",
            fingerprint=fingerprint,
            environment=body.environment,
            service=body.service,
            status={
                "insufficient_data": "investigate_only",
                "normal": "no_action",
                "anomaly": "investigating",
            }[report.detection.status],
            alert_time=alert_time,
            window_seconds=body.window_seconds,
            investigation=report,
            created_at=datetime.now(timezone.utc),
        )
        actor_id = claims.get("sub") if isinstance(claims, dict) else None
        stored = await request.app.state.repository.create_incident(incident, actor_id)
        logger.info(
            "ORION recorded incident_id=%s service=%s",
            stored.incident_id,
            stored.service,
        )
        return stored

    @app.get("/orion/incidents/{incident_id}", response_model=IncidentRecord)
    async def get_incident(
        incident_id: str,
        request: Request,
        _claims=Depends(require_user),
    ):
        incident = await request.app.state.repository.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        return incident

    @app.get("/orion/incidents/{incident_id}/audit", response_model=list[AuditEvent])
    async def get_incident_audit(
        incident_id: str,
        request: Request,
        _claims=Depends(require_user),
    ):
        if await request.app.state.repository.get_incident(incident_id) is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        return await request.app.state.repository.get_events(incident_id)

    @app.post(
        "/orion/incidents/{incident_id}/plans",
        response_model=ActionPlan,
    )
    async def create_plan(
        incident_id: str,
        request: Request,
        claims=Depends(require_user),
    ):
        repository = request.app.state.repository
        incident = await repository.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found.")

        candidate = propose_plan(incident)
        executor = request.app.state.executor
        execution_enabled = _execution_enabled() and executor is not None
        staging_validation = None
        if candidate.action != "investigate_only" and execution_enabled:
            try:
                staging_validation = await asyncio.to_thread(
                    executor.validate,
                    candidate,
                )
            except KubernetesExecutorError as exc:
                logger.warning(
                    "ORION staging validation unavailable for incident_id=%s",
                    incident_id,
                )
                raise HTTPException(
                    status_code=503,
                    detail="Staging validation is unavailable; no plan was created.",
                ) from exc
        decision = evaluate_plan(
            incident,
            candidate,
            staging_validation=staging_validation,
            executor_enabled=execution_enabled,
        )
        plan = ActionPlan(**candidate.model_dump(), policy=decision)
        actor_id = claims.get("sub") if isinstance(claims, dict) else None
        return await repository.create_plan(plan, actor_id)

    @app.get("/orion/plans/{plan_id}", response_model=ActionPlan)
    async def get_plan(
        plan_id: str,
        request: Request,
        _claims=Depends(require_user),
    ):
        plan = await request.app.state.repository.get_plan(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        return plan

    @app.get("/orion/plans/{plan_id}/state", response_model=PlanState)
    async def get_plan_state(
        plan_id: str,
        request: Request,
        _claims=Depends(require_user),
    ):
        if await request.app.state.repository.get_plan(plan_id) is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        state = await request.app.state.repository.get_plan_state(plan_id)
        if state is None:
            raise HTTPException(
                status_code=409,
                detail="Plan execution state has not been initialized.",
            )
        return state

    @app.post(
        "/orion/plans/{plan_id}/approve",
        response_model=PlanApproval,
    )
    async def approve_plan(
        plan_id: str,
        body: PlanApprovalRequest,
        request: Request,
        claims=Depends(require_user),
    ):
        actor_id = _actor_id(claims)
        repository = request.app.state.repository
        plan = await repository.get_plan(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        if plan.plan_hash != body.plan_hash:
            raise HTTPException(status_code=409, detail="Plan hash does not match.")
        if plan.policy.decision != "approval_required":
            raise HTTPException(
                status_code=409,
                detail="This plan is blocked or does not require approval.",
            )
        if not (_execution_enabled() and request.app.state.executor is not None):
            raise HTTPException(
                status_code=503,
                detail="The isolated staging executor is not enabled.",
            )
        incident = await repository.get_incident(plan.incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        current_policy = await _recheck_policy(
            request,
            incident,
            plan,
        )
        if current_policy.decision != "approval_required":
            await repository.block_plan(
                plan_id,
                actor_id,
                "; ".join(current_policy.reasons),
            )
            raise HTTPException(
                status_code=409,
                detail="Plan preconditions no longer pass; create a fresh investigation and plan.",
            )
        state = await repository.approve_plan(
            plan_id,
            body.plan_hash,
            actor_id,
        )
        if state is None:
            raise HTTPException(
                status_code=409,
                detail="Plan approval is no longer available.",
            )
        return PlanApproval(
            plan_id=plan_id,
            plan_hash=body.plan_hash,
            approved_by=state.approved_by,
            approved_at=state.approved_at,
        )

    @app.post(
        "/orion/plans/{plan_id}/execute",
        response_model=PlanExecutionResult,
    )
    async def execute_plan(
        plan_id: str,
        request: Request,
        claims=Depends(require_user),
    ):
        actor_id = _actor_id(claims)
        repository = request.app.state.repository
        plan = await repository.get_plan(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        executor = request.app.state.executor
        if not (_execution_enabled() and executor is not None):
            raise HTTPException(
                status_code=503,
                detail="The isolated staging executor is not enabled.",
            )
        if plan.policy.decision != "approval_required":
            raise HTTPException(
                status_code=409,
                detail="This plan is blocked or does not qualify for execution.",
            )

        state = await repository.get_plan_state(plan_id)
        if state is None:
            raise HTTPException(
                status_code=409,
                detail="Plan execution state has not been initialized.",
            )
        if state.execution_result is not None:
            return state.execution_result
        if state.status == "executing":
            raise HTTPException(
                status_code=409,
                detail="This plan is already being executed.",
            )
        if state.status != "approved":
            raise HTTPException(
                status_code=409,
                detail="Explicit plan approval is required before execution.",
            )
        incident = await repository.get_incident(plan.incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        current_policy = await _recheck_policy(
            request,
            incident,
            plan,
        )
        if current_policy.decision != "approval_required":
            await repository.block_plan(
                plan_id,
                actor_id,
                "; ".join(current_policy.reasons),
            )
            raise HTTPException(
                status_code=409,
                detail="Plan preconditions no longer pass; execution was blocked.",
            )

        state = await repository.claim_execution(plan_id)
        if state is None:
            current_state = await repository.get_plan_state(plan_id)
            if current_state is not None and current_state.status == "executing":
                raise HTTPException(
                    status_code=409,
                    detail="This plan is already being executed.",
                )
            raise HTTPException(
                status_code=409,
                detail="Explicit plan approval is required before execution.",
            )
        if state.execution_result is not None:
            return state.execution_result
        if state.status != "executing":
            raise HTTPException(
                status_code=409,
                detail="The plan is already being executed.",
            )

        try:
            result = await asyncio.to_thread(executor.execute, plan)
        except KubernetesExecutorError as exc:
            logger.exception(
                "ORION staging execution failed for plan_id=%s",
                plan_id,
            )
            failed_result = PlanExecutionResult(
                plan_id=plan_id,
                status="failed",
                canary_ready=False,
                rollout_succeeded=False,
                rollback_performed=False,
                rollback_succeeded=False,
                cleanup_succeeded=False,
                message=str(exc),
            )
            await repository.complete_execution(
                plan_id,
                "failed",
                failed_result.model_dump(mode="json"),
                actor_id,
            )
            raise HTTPException(
                status_code=502,
                detail="Staging execution failed; inspect the plan state and isolated namespace.",
            ) from exc

        execution_result = PlanExecutionResult(
            plan_id=plan_id,
            **result,
        )
        completed = await repository.complete_execution(
            plan_id,
            execution_result.status,
            execution_result.model_dump(mode="json"),
            actor_id,
        )
        if completed is None:
            raise HTTPException(
                status_code=409,
                detail="Execution state changed before completion was recorded.",
            )
        return execution_result

    @app.post("/orion/plans/{plan_id}/dry-run", response_model=DryRunResult)
    async def dry_run_plan(
        plan_id: str,
        request: Request,
        claims=Depends(require_user),
    ):
        plan = await request.app.state.repository.get_plan(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found.")
        if plan.policy is None:
            raise HTTPException(status_code=409, detail="Plan has no policy decision.")

        actor_id = claims.get("sub") if isinstance(claims, dict) else None
        await request.app.state.repository.record_dry_run(plan, actor_id)
        return DryRunResult(
            plan_id=plan.plan_id,
            decision=plan.policy.decision,
            checks=plan.policy.checks,
            reasons=plan.policy.reasons,
            message="Dry run only. No cluster connection or service mutation was attempted.",
        )

    configure_observability(app)
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
    return app


app = create_app()
