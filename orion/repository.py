import json
from pathlib import Path

from .models import ActionPlan, AuditEvent, IncidentRecord


def _decode_json(value):
    return json.loads(value) if isinstance(value, str) else value


class PostgresRepository:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        migrations = sorted(
            (Path(__file__).with_name("migrations")).glob("*.sql")
        )
        async with self.pool.acquire() as connection:
            for migration in migrations:
                await connection.execute(
                    migration.read_text(encoding="utf-8")
                )

    async def ping(self) -> bool:
        return await self.pool.fetchval("SELECT 1") == 1

    async def create_incident(
        self,
        incident: IncidentRecord,
        actor_id: str | None,
    ) -> IncidentRecord:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    INSERT INTO orion_incidents(
                        incident_id, fingerprint, environment, service,
                        alert_time, created_at, incident_json
                    )
                    VALUES($1, $2, $3, $4, $5, $6, $7::jsonb)
                    ON CONFLICT(fingerprint) DO NOTHING
                    RETURNING incident_id, incident_json
                    """,
                    incident.incident_id,
                    incident.fingerprint,
                    incident.environment,
                    incident.service,
                    incident.alert_time,
                    incident.created_at,
                    incident.model_dump_json(),
                )
                inserted = row is not None
                if not inserted:
                    row = await connection.fetchrow(
                        """
                        SELECT incident_id, incident_json
                        FROM orion_incidents
                        WHERE fingerprint=$1
                        """,
                        incident.fingerprint,
                    )
                stored = IncidentRecord.model_validate(
                    _decode_json(row["incident_json"])
                )
                await connection.execute(
                    """
                    INSERT INTO orion_audit_events(
                        incident_id, event_type, actor_id, details
                    )
                    VALUES($1, $2, $3, $4::jsonb)
                    """,
                    stored.incident_id,
                    "incident_created" if inserted else "incident_deduplicated",
                    actor_id,
                    json.dumps({"service": stored.service}),
                )
        return stored

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        row = await self.pool.fetchrow(
            """
            SELECT incident_json
            FROM orion_incidents
            WHERE incident_id=$1
            """,
            incident_id,
        )
        if row is None:
            return None
        return IncidentRecord.model_validate(_decode_json(row["incident_json"]))

    async def create_plan(
        self,
        plan: ActionPlan,
        actor_id: str | None,
    ) -> ActionPlan:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    INSERT INTO orion_plans(
                        plan_id, incident_id, plan_hash, created_at, plan_json
                    )
                    VALUES($1, $2, $3, $4, $5::jsonb)
                    ON CONFLICT(incident_id, plan_hash) DO NOTHING
                    RETURNING plan_id, plan_json
                    """,
                    plan.plan_id,
                    plan.incident_id,
                    plan.plan_hash,
                    plan.created_at,
                    plan.model_dump_json(),
                )
                inserted = row is not None
                if not inserted:
                    row = await connection.fetchrow(
                        """
                        SELECT plan_id, plan_json
                        FROM orion_plans
                        WHERE incident_id=$1 AND plan_hash=$2
                        """,
                        plan.incident_id,
                        plan.plan_hash,
                    )
                stored = ActionPlan.model_validate(_decode_json(row["plan_json"]))
                await connection.execute(
                    """
                    INSERT INTO orion_audit_events(
                        incident_id, plan_id, event_type, actor_id, details
                    )
                    VALUES($1, $2, $3, $4, $5::jsonb)
                    """,
                    stored.incident_id,
                    stored.plan_id,
                    "plan_proposed" if inserted else "plan_reused",
                    actor_id,
                    json.dumps(
                        {
                            "action": stored.action,
                            "policy_decision": stored.policy.decision,
                            "plan_hash": stored.plan_hash,
                        }
                    ),
                )
                state = {
                    "approval_required": "pending_approval",
                    "blocked": "blocked",
                    "no_action": "no_action",
                }[stored.policy.decision]
                await connection.execute(
                    """
                    INSERT INTO orion_plan_state(plan_id, status)
                    VALUES($1, $2)
                    ON CONFLICT(plan_id) DO NOTHING
                    """,
                    stored.plan_id,
                    state,
                )
        return stored

    async def get_plan(self, plan_id: str) -> ActionPlan | None:
        row = await self.pool.fetchrow(
            "SELECT plan_json FROM orion_plans WHERE plan_id=$1",
            plan_id,
        )
        if row is None:
            return None
        return ActionPlan.model_validate(_decode_json(row["plan_json"]))

    async def get_plan_state(self, plan_id: str):
        from .models import PlanState, PlanExecutionResult

        row = await self.pool.fetchrow(
            """
            SELECT plan_id, status, approved_by, approved_at, execution_result
            FROM orion_plan_state
            WHERE plan_id=$1
            """,
            plan_id,
        )
        if row is None:
            return None
        execution = _decode_json(row["execution_result"])
        return PlanState(
            plan_id=row["plan_id"],
            status=row["status"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            execution_result=(
                PlanExecutionResult.model_validate(execution)
                if execution is not None
                else None
            ),
        )

    async def approve_plan(
        self,
        plan_id: str,
        plan_hash: str,
        actor_id: str,
    ):
        from .models import PlanState

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                plan = await connection.fetchrow(
                    "SELECT incident_id, plan_hash FROM orion_plans WHERE plan_id=$1",
                    plan_id,
                )
                if plan is None or plan["plan_hash"] != plan_hash:
                    return None
                state = await connection.fetchrow(
                    """
                    SELECT status, approval_hash, approved_by, approved_at
                    FROM orion_plan_state
                    WHERE plan_id=$1
                    FOR UPDATE
                    """,
                    plan_id,
                )
                if state is None:
                    return None
                if state["status"] == "approved" and state["approval_hash"] == plan_hash:
                    return PlanState(
                        plan_id=plan_id,
                        status="approved",
                        approved_by=state["approved_by"],
                        approved_at=state["approved_at"],
                    )
                if state["status"] != "pending_approval":
                    return None

                approved_at = await connection.fetchval(
                    """
                    UPDATE orion_plan_state
                    SET status='approved', approval_hash=$2, approved_by=$3,
                        approved_at=NOW(), updated_at=NOW()
                    WHERE plan_id=$1 AND status='pending_approval'
                    RETURNING approved_at
                    """,
                    plan_id,
                    plan_hash,
                    actor_id,
                )
                if approved_at is None:
                    return None
                await connection.execute(
                    """
                    INSERT INTO orion_audit_events(
                        incident_id, plan_id, event_type, actor_id, details
                    )
                    VALUES($1, $2, 'plan_approved', $3, $4::jsonb)
                    """,
                    plan["incident_id"],
                    plan_id,
                    actor_id,
                    json.dumps({"plan_hash": plan_hash}),
                )
                return PlanState(
                    plan_id=plan_id,
                    status="approved",
                    approved_by=actor_id,
                    approved_at=approved_at,
                )

    async def block_plan(
        self,
        plan_id: str,
        actor_id: str | None,
        reason: str,
    ) -> bool:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    UPDATE orion_plan_state
                    SET status='blocked', approval_hash=NULL,
                        approved_by=NULL, approved_at=NULL, updated_at=NOW()
                    WHERE plan_id=$1 AND status IN ('pending_approval', 'approved')
                    RETURNING plan_id
                    """,
                    plan_id,
                )
                if row is None:
                    return False
                plan = await connection.fetchrow(
                    "SELECT incident_id FROM orion_plans WHERE plan_id=$1",
                    plan_id,
                )
                await connection.execute(
                    """
                    INSERT INTO orion_audit_events(
                        incident_id, plan_id, event_type, actor_id, details
                    )
                    VALUES($1, $2, 'plan_blocked', $3, $4::jsonb)
                    """,
                    plan["incident_id"],
                    plan_id,
                    actor_id,
                    json.dumps({"reason": reason}),
                )
                return True

    async def claim_execution(self, plan_id: str):
        from .models import PlanExecutionResult, PlanState

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    SELECT s.status, s.approval_hash, s.approved_by,
                           s.approved_at, s.execution_result, p.plan_hash,
                           p.incident_id
                    FROM orion_plan_state AS s
                    JOIN orion_plans AS p ON p.plan_id=s.plan_id
                    WHERE s.plan_id=$1
                    FOR UPDATE OF s
                    """,
                    plan_id,
                )
                if row is None:
                    return None
                if row["status"] in {
                    "succeeded",
                    "canary_failed",
                    "rolled_back",
                    "rollback_failed",
                    "cleanup_failed",
                    "failed",
                }:
                    result = _decode_json(row["execution_result"])
                    return PlanState(
                        plan_id=plan_id,
                        status=row["status"],
                        approved_by=row["approved_by"],
                        approved_at=row["approved_at"],
                        execution_result=(
                            PlanExecutionResult.model_validate(result)
                            if result is not None
                            else None
                        ),
                    )
                if (
                    row["status"] != "approved"
                    or row["approval_hash"] != row["plan_hash"]
                ):
                    return None
                await connection.execute(
                    """
                    UPDATE orion_plan_state
                    SET status='executing', updated_at=NOW()
                    WHERE plan_id=$1
                    """,
                    plan_id,
                )
                await connection.execute(
                    """
                    INSERT INTO orion_audit_events(
                        incident_id, plan_id, event_type, actor_id, details
                    )
                    VALUES($1, $2, 'canary_execution_started', $3, $4::jsonb)
                    """,
                    row["incident_id"],
                    plan_id,
                    row["approved_by"],
                    json.dumps({"plan_hash": row["plan_hash"]}),
                )
                return PlanState(
                    plan_id=plan_id,
                    status="executing",
                    approved_by=row["approved_by"],
                    approved_at=row["approved_at"],
                )

    async def complete_execution(
        self,
        plan_id: str,
        status: str,
        result: dict,
        actor_id: str | None,
    ):
        from .models import PlanExecutionResult, PlanState

        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    UPDATE orion_plan_state
                    SET status=$2, execution_result=$3::jsonb, updated_at=NOW()
                    WHERE plan_id=$1 AND status='executing'
                    RETURNING approved_by, approved_at
                    """,
                    plan_id,
                    status,
                    json.dumps(result),
                )
                if row is None:
                    return None
                plan = await connection.fetchrow(
                    "SELECT incident_id FROM orion_plans WHERE plan_id=$1",
                    plan_id,
                )
                await connection.execute(
                    """
                    INSERT INTO orion_audit_events(
                        incident_id, plan_id, event_type, actor_id, details
                    )
                    VALUES($1, $2, 'canary_execution_completed', $3, $4::jsonb)
                    """,
                    plan["incident_id"],
                    plan_id,
                    actor_id,
                    json.dumps(result),
                )
                return PlanState(
                    plan_id=plan_id,
                    status=status,
                    approved_by=row["approved_by"],
                    approved_at=row["approved_at"],
                    execution_result=PlanExecutionResult.model_validate(result),
                )

    async def record_dry_run(
        self,
        plan: ActionPlan,
        actor_id: str | None,
    ):
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO orion_audit_events(
                    incident_id, plan_id, event_type, actor_id, details
                )
                VALUES($1, $2, 'dry_run_completed', $3, $4::jsonb)
                """,
                plan.incident_id,
                plan.plan_id,
                actor_id,
                json.dumps(
                    {
                        "decision": plan.policy.decision,
                        "executed": False,
                        "would_mutate_cluster": False,
                    }
                ),
            )

    async def get_events(self, incident_id: str) -> list[AuditEvent]:
        rows = await self.pool.fetch(
            """
            SELECT event_id, incident_id, plan_id, event_type, actor_id,
                   details, created_at
            FROM orion_audit_events
            WHERE incident_id=$1
            ORDER BY event_id
            """,
            incident_id,
        )
        return [
            AuditEvent(
                event_id=row["event_id"],
                incident_id=row["incident_id"],
                plan_id=row["plan_id"],
                event_type=row["event_type"],
                actor_id=row["actor_id"],
                details=_decode_json(row["details"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
