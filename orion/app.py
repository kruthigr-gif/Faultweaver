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
)
from .observability import configure_observability
from .planner import propose_plan
from .policy import evaluate_plan
from .repository import PostgresRepository


logger = logging.getLogger(__name__)


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

        yield
    finally:
        if pool is not None:
            await pool.close()
        if owns_investigator_client and investigator_client is not None:
            await investigator_client.close()


def create_app(repository=None, investigator_client=None) -> FastAPI:
    app = FastAPI(
        title="ORION Incident Control Plane",
        description=(
            "Authenticated incident intake and policy-evaluated dry-run planning. "
            "This release has no remediation executor."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.investigator_client = investigator_client

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
        decision = evaluate_plan(incident, candidate)
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
