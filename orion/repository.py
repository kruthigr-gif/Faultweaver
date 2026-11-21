import json
from pathlib import Path

from .models import ActionPlan, AuditEvent, IncidentRecord


def _decode_json(value):
    return json.loads(value) if isinstance(value, str) else value


class PostgresRepository:
    def __init__(self, pool):
        self.pool = pool

    async def initialize(self):
        migration = Path(__file__).with_name("migrations") / "001_initial.sql"
        sql = migration.read_text(encoding="utf-8")
        async with self.pool.acquire() as connection:
            await connection.execute(sql)

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
        return stored

    async def get_plan(self, plan_id: str) -> ActionPlan | None:
        row = await self.pool.fetchrow(
            "SELECT plan_json FROM orion_plans WHERE plan_id=$1",
            plan_id,
        )
        if row is None:
            return None
        return ActionPlan.model_validate(_decode_json(row["plan_json"]))

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
