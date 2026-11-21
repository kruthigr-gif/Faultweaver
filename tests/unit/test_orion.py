from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from orion.app import create_app
from orion.models import AuditEvent, InvestigationReport


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
        assert client.post(f"/orion/plans/{plan['plan_id']}/execute").status_code == 404

        audit = client.get(f"/orion/incidents/{incident['incident_id']}/audit")
        assert audit.status_code == 200
        assert [event["event_type"] for event in audit.json()] == [
            "incident_created",
            "plan_proposed",
            "dry_run_completed",
        ]


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
