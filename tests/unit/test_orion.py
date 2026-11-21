from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from orion.app import create_app
from orion.auth import require_user
from orion.models import (
    AuditEvent,
    InvestigationReport,
    PlanExecutionResult,
    PlanState,
    PolicyCheck,
)


def make_report(status="anomaly", corroborating_errors=True):
    now = datetime.now(timezone.utc)
    evidence = []
    signals = ["errors", "error_logs"] if corroborating_errors else ["latency"]
    for index, signal in enumerate(signals):
        evidence.append(
            {
                "evidence_id": f"ev-{index}",
                "source": "loki" if signal == "error_logs" else "prometheus",
                "service": "inventory-service",
                "signal": signal,
                "window_start": now.isoformat(),
                "window_end": now.isoformat(),
                "observation": "Synthetic test evidence.",
                "reference": "local-test",
                "score": 5 if signal == "errors" else 3,
                "available": True,
            }
        )
    return InvestigationReport(
        incident_id="source-incident",
        detected_at=now,
        affected_services=["inventory-service"],
        detection={
            "status": status,
            "current_latency_seconds": 0.5,
            "baseline_latency_seconds": 0.1,
            "baseline_samples": 4,
            "threshold_seconds": 0.2,
            "message": "Synthetic test assessment.",
        },
        evidence=evidence,
        ranked_causes=[
            {
                "service": "inventory-service",
                "cause": "Corroborating error signals.",
                "confidence": "high" if corroborating_errors else "low",
                "evidence_ids": [item["evidence_id"] for item in evidence],
            }
        ],
        alternatives=[],
        unknowns=[],
        recommended_checks=["Inspect the local service."],
        remediation={"status": "not_run", "detail": "No action taken."},
    )


class MemoryRepository:
    def __init__(self):
        self.incidents = {}
        self.fingerprints = {}
        self.plans = {}
        self.events = {}
        self.next_event_id = 1
        self.states = {}

    async def ping(self):
        return True

    async def create_incident(self, incident, actor_id):
        existing_id = self.fingerprints.get(incident.fingerprint)
        if existing_id:
            return self.incidents[existing_id]
        self.incidents[incident.incident_id] = incident
        self.fingerprints[incident.fingerprint] = incident.incident_id
        self._event(incident.incident_id, "incident_created", actor_id, None, {})
        return incident

    async def get_incident(self, incident_id):
        return self.incidents.get(incident_id)

    async def create_plan(self, plan, actor_id):
        for existing in self.plans.values():
            if existing.incident_id == plan.incident_id and existing.plan_hash == plan.plan_hash:
                return existing
        self.plans[plan.plan_id] = plan
        status = {
            "approval_required": "pending_approval",
            "blocked": "blocked",
            "no_action": "no_action",
        }[plan.policy.decision]
        self.states[plan.plan_id] = PlanState(
            plan_id=plan.plan_id,
            status=status,
        )
        self._event(
            plan.incident_id,
            "plan_proposed",
            actor_id,
            plan.plan_id,
            {"action": plan.action},
        )
        return plan

    async def get_plan(self, plan_id):
        return self.plans.get(plan_id)

    async def get_plan_state(self, plan_id):
        return self.states.get(plan_id)

    async def approve_plan(self, plan_id, plan_hash, actor_id):
        plan = self.plans.get(plan_id)
        state = self.states.get(plan_id)
        if (
            plan is None
            or state is None
            or plan.plan_hash != plan_hash
            or state.status != "pending_approval"
        ):
            return None
        state = PlanState(
            plan_id=plan_id,
            status="approved",
            approved_by=actor_id,
            approved_at=datetime.now(timezone.utc),
        )
        self.states[plan_id] = state
        self._event(plan.incident_id, "plan_approved", actor_id, plan_id, {})
        return state

    async def block_plan(self, plan_id, actor_id, reason):
        state = self.states.get(plan_id)
        if state is None or state.status not in {"pending_approval", "approved"}:
            return False
        self.states[plan_id] = PlanState(
            plan_id=plan_id,
            status="blocked",
        )
        return True

    async def claim_execution(self, plan_id):
        state = self.states.get(plan_id)
        if state is None or state.status != "approved":
            return state if state and state.execution_result else None
        state = state.model_copy(update={"status": "executing"})
        self.states[plan_id] = state
        return state

    async def complete_execution(self, plan_id, status, result, actor_id):
        state = self.states.get(plan_id)
        if state is None or state.status != "executing":
            return None
        state = PlanState(
            plan_id=plan_id,
            status=status,
            approved_by=state.approved_by,
            approved_at=state.approved_at,
            execution_result=PlanExecutionResult.model_validate(result),
        )
        self.states[plan_id] = state
        plan = self.plans[plan_id]
        self._event(
            plan.incident_id,
            "canary_execution_completed",
            actor_id,
            plan_id,
            result,
        )
        return state

    async def record_dry_run(self, plan, actor_id):
        self._event(
            plan.incident_id,
            "dry_run_completed",
            actor_id,
            plan.plan_id,
            {"executed": False, "would_mutate_cluster": False},
        )

    async def get_events(self, incident_id):
        return self.events.get(incident_id, [])

    def _event(self, incident_id, event_type, actor_id, plan_id, details):
        event = AuditEvent(
            event_id=self.next_event_id,
            incident_id=incident_id,
            plan_id=plan_id,
            event_type=event_type,
            actor_id=actor_id,
            details=details,
            created_at=datetime.now(timezone.utc),
        )
        self.events.setdefault(incident_id, []).append(event)
        self.next_event_id += 1


class FakeInvestigator:
    def __init__(self, report):
        self.report = report
        self.authorization = None

    async def investigate(self, request, authorization):
        self.authorization = authorization
        return self.report


class HealthyExecutor:
    def __init__(self):
        self.validation_calls = 0
        self.execution_calls = 0
        self.validation_passes = True

    def validate(self, plan):
        self.validation_calls += 1
        return PolicyCheck(
            name="staging_validation",
            passed=self.validation_passes,
            detail="Test executor validated the isolated deployment.",
        )

    def execute(self, plan):
        self.execution_calls += 1
        return {
            "status": "succeeded",
            "canary_ready": True,
            "rollout_succeeded": True,
            "rollback_performed": False,
            "rollback_succeeded": False,
            "cleanup_succeeded": True,
            "message": "Test canary passed.",
        }


def test_incident_plan_and_dry_run_never_execute(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    repository = MemoryRepository()
    investigator = FakeInvestigator(make_report())

    with TestClient(create_app(repository, investigator)) as client:
        incident_response = client.post(
            "/orion/incidents",
            headers={"Authorization": "Bearer local-test-token"},
            json={
                "service": "inventory-service",
                "alert_time": datetime.now(timezone.utc).isoformat(),
                "window_seconds": 300,
            },
        )
        assert incident_response.status_code == 200
        incident = incident_response.json()
        assert incident["investigation"]["detection"]["status"] == "anomaly"
        assert investigator.authorization == "Bearer local-test-token"
        assert "local-test-token" not in incident_response.text

        plan_response = client.post(
            f"/orion/incidents/{incident['incident_id']}/plans"
        )
        assert plan_response.status_code == 200
        plan = plan_response.json()
        assert plan["action"] == "restart_deployment"
        assert plan["policy"]["decision"] == "blocked"
        assert plan["policy"]["execution_enabled"] is False

        dry_run = client.post(f"/orion/plans/{plan['plan_id']}/dry-run")
        assert dry_run.status_code == 200
        assert dry_run.json()["executed"] is False
        assert dry_run.json()["would_mutate_cluster"] is False
        assert client.post(f"/orion/plans/{plan['plan_id']}/execute").status_code == 401

        audit = client.get(f"/orion/incidents/{incident['incident_id']}/audit")
        assert audit.status_code == 200
        assert [event["event_type"] for event in audit.json()] == [
            "incident_created",
            "plan_proposed",
            "dry_run_completed",
        ]


def test_staging_plan_requires_hash_bound_approval_and_executes_once(monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")
        monkeypatch.setenv("ORION_EXECUTION_ENABLED", "true")
        repository = MemoryRepository()
        investigator = FakeInvestigator(make_report())
        executor = HealthyExecutor()
        app = create_app(repository, investigator, executor)
        app.dependency_overrides[require_user] = lambda: {"sub": "local-operator"}

        with TestClient(app) as client:
            incident_response = client.post(
                "/orion/incidents",
                json={
                    "service": "inventory-service",
                    "alert_time": datetime.now(timezone.utc).isoformat(),
                    "window_seconds": 300,
                },
            )
            incident_id = incident_response.json()["incident_id"]
            plan_response = client.post(
                f"/orion/incidents/{incident_id}/plans"
            )
            plan = plan_response.json()

            assert plan["policy"]["decision"] == "approval_required"
            assert plan["policy"]["execution_enabled"] is False
            assert executor.validation_calls == 1
            assert client.post(
                f"/orion/plans/{plan['plan_id']}/execute"
            ).status_code == 409
            assert client.post(
                f"/orion/plans/{plan['plan_id']}/approve",
                json={"plan_hash": "0" * 64},
            ).status_code == 409

            approval = client.post(
                f"/orion/plans/{plan['plan_id']}/approve",
                json={"plan_hash": plan["plan_hash"]},
            )
            assert approval.status_code == 200
            assert approval.json()["approved"] is True
            assert approval.json()["approved_by"] == "local-operator"

            execution = client.post(
                f"/orion/plans/{plan['plan_id']}/execute"
            )
            repeated_execution = client.post(
                f"/orion/plans/{plan['plan_id']}/execute"
            )
            assert execution.status_code == 200
            assert execution.json()["status"] == "succeeded"
            assert execution.json()["canary_ready"] is True
            assert executor.validation_calls == 3
            assert executor.execution_calls == 1
            assert repeated_execution.json() == execution.json()

            state = client.get(f"/orion/plans/{plan['plan_id']}/state")
            assert state.json()["status"] == "succeeded"


def test_staging_approval_is_rechecked_before_canary_execution(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("ORION_EXECUTION_ENABLED", "true")
    repository = MemoryRepository()
    executor = HealthyExecutor()
    app = create_app(
            repository,
            FakeInvestigator(make_report()),
            executor,
    )
    app.dependency_overrides[require_user] = lambda: {"sub": "local-operator"}

    with TestClient(app) as client:
            incident = client.post(
                "/orion/incidents",
                json={
                    "service": "inventory-service",
                    "alert_time": datetime.now(timezone.utc).isoformat(),
                    "window_seconds": 300,
                },
            ).json()
            plan = client.post(
                f"/orion/incidents/{incident['incident_id']}/plans"
            ).json()
            executor.validation_passes = False

            approval = client.post(
                f"/orion/plans/{plan['plan_id']}/approve",
                json={"plan_hash": plan["plan_hash"]},
            )

            assert approval.status_code == 409
            assert repository.states[plan["plan_id"]].status == "blocked"
            assert executor.execution_calls == 0


def test_insufficient_evidence_produces_no_action_plan(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    repository = MemoryRepository()
    investigator = FakeInvestigator(
        make_report(status="insufficient_data", corroborating_errors=False)
    )

    with TestClient(create_app(repository, investigator)) as client:
        response = client.post(
            "/orion/incidents",
            json={
                "service": "inventory-service",
                "alert_time": datetime.now(timezone.utc).isoformat(),
                "window_seconds": 300,
            },
        )
        incident_id = response.json()["incident_id"]

        plan_response = client.post(
            f"/orion/incidents/{incident_id}/plans"
        )

    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan["action"] == "investigate_only"
    assert plan["policy"]["decision"] == "no_action"
    assert plan["policy"]["execution_enabled"] is False


def test_latency_only_anomaly_does_not_propose_restart(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    repository = MemoryRepository()
    investigator = FakeInvestigator(
        make_report(status="anomaly", corroborating_errors=False)
    )

    with TestClient(create_app(repository, investigator)) as client:
        response = client.post(
            "/orion/incidents",
            json={
                "service": "inventory-service",
                "alert_time": datetime.now(timezone.utc).isoformat(),
                "window_seconds": 300,
            },
        )
        incident_id = response.json()["incident_id"]
        plan_response = client.post(
            f"/orion/incidents/{incident_id}/plans"
        )

    assert plan_response.status_code == 200
    assert plan_response.json()["action"] == "investigate_only"


def test_repeated_alert_in_same_window_returns_same_incident(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    repository = MemoryRepository()
    investigator = FakeInvestigator(make_report(status="normal"))
    alert_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = {
        "service": "order-service",
        "alert_time": alert_time,
        "window_seconds": 300,
    }

    with TestClient(create_app(repository, investigator)) as client:
        first = client.post("/orion/incidents", json=payload)
        second = client.post("/orion/incidents", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["incident_id"] == second.json()["incident_id"]


def test_stale_evidence_blocks_restart_candidate(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    repository = MemoryRepository()
    report = make_report()
    report.detected_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    investigator = FakeInvestigator(report)

    with TestClient(create_app(repository, investigator)) as client:
        incident_response = client.post(
            "/orion/incidents",
            json={
                "service": "inventory-service",
                "alert_time": datetime.now(timezone.utc).isoformat(),
                "window_seconds": 300,
            },
        )
        incident_id = incident_response.json()["incident_id"]
        plan_response = client.post(
            f"/orion/incidents/{incident_id}/plans"
        )

    assert plan_response.status_code == 200
    plan = plan_response.json()
    assert plan["action"] == "restart_deployment"
    assert plan["policy"]["decision"] == "blocked"
    assert any(
        check["name"] == "fresh_evidence" and not check["passed"]
        for check in plan["policy"]["checks"]
    )


def test_incident_api_requires_auth_when_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    repository = MemoryRepository()
    investigator = FakeInvestigator(make_report())

    with TestClient(create_app(repository, investigator)) as client:
        response = client.post(
            "/orion/incidents",
            json={
                "service": "inventory-service",
                "alert_time": datetime.now(timezone.utc).isoformat(),
                "window_seconds": 300,
            },
        )

    assert response.status_code == 401
