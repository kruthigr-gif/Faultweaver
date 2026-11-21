import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Literal

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from config import LOKI_URL, PROMETHEUS_URL, SERVICE_HANDLERS, TEMPO_URL
from diagnosis import Diagnoser
from detection import Detector
from evidence import EvidenceCollector
from report import ReportBuilder
from telemetry_clients import TelemetryClient
from auth import require_user
from fastapi import Depends


logger = logging.getLogger(__name__)
app = FastAPI(title="SRE Investigator")

telemetry = TelemetryClient(
    prometheus_url=PROMETHEUS_URL,
    loki_url=LOKI_URL,
    tempo_url=TEMPO_URL,
)
detector = Detector(telemetry_client=telemetry)
evidence_collector = EvidenceCollector(telemetry_client=telemetry)
diagnoser = Diagnoser()


class InvestigateRequest(BaseModel):
    alert_time: datetime
    service: Literal["order-service", "inventory-service", "payment-service"]
    window_seconds: int = Field(default=300, ge=60, le=3600)


class DetectionResult(BaseModel):
    status: Literal["anomaly", "normal", "insufficient_data"]
    current_latency_seconds: float | None
    baseline_latency_seconds: float | None
    baseline_samples: int
    threshold_seconds: float | None
    message: str


class EvidenceItem(BaseModel):
    evidence_id: str
    source: str
    service: str
    signal: str
    window_start: str
    window_end: str
    observation: str
    reference: str
    score: float
    available: bool


class RankedCause(BaseModel):
    service: str
    cause: str
    confidence: Literal["high", "medium", "low"]
    evidence_ids: List[str]


class InvestigateResponse(BaseModel):
    incident_id: str
    detected_at: str
    affected_services: List[str]
    detection: DetectionResult
    evidence: List[EvidenceItem]
    ranked_causes: List[RankedCause]
    alternatives: List[str]
    unknowns: List[str]
    recommended_checks: List[str]
    remediation: Dict[str, str]


def _as_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/investigate", response_model=InvestigateResponse)
async def investigate(req: InvestigateRequest, _user=Depends(require_user)):
    alert_time = _as_utc_iso(req.alert_time)
    alert_timestamp = datetime.fromisoformat(
        alert_time.replace("Z", "+00:00")
    ).timestamp()
    if alert_timestamp > datetime.now(timezone.utc).timestamp() + 60:
        raise HTTPException(status_code=422, detail="alert_time cannot be in the future")

    try:
        assessments = {
            service: detector.assess(service, alert_time)
            for service in SERVICE_HANDLERS
        }
    except requests.RequestException as exc:
        logger.exception("Prometheus query failed during investigation")
        raise HTTPException(
            status_code=503,
            detail="Prometheus is unavailable; investigation could not complete.",
        ) from exc

    evidence = []
    for service, assessment in assessments.items():
        evidence.extend(
            evidence_collector.collect(
                service,
                alert_time,
                req.window_seconds,
                assessment,
            )
        )

    ranked = diagnoser.rank(req.service, evidence)
    detection = assessments[req.service]
    unknowns = []
    if detection["status"] == "insufficient_data":
        unknowns.append(detection["message"])
    unavailable_sources = sorted(
        {
            item["source"]
            for item in evidence
            if not item["available"]
        }
    )
    if unavailable_sources:
        unknowns.append(
            "Telemetry unavailable from: " + ", ".join(unavailable_sources)
        )

    recommended_checks = []
    if detection["status"] == "anomaly":
        recommended_checks.append(
            "Compare the highest-ranked service's latency and error evidence with the incident window."
        )
    elif detection["status"] == "insufficient_data":
        recommended_checks.append(
            "Generate traffic and retry after at least three baseline latency samples have been collected."
        )
    else:
        recommended_checks.append(
            "No latency anomaly was detected; verify the alert condition and inspect the evidence window."
        )
    if any(item["score"] > 0 for item in evidence if item["signal"] in ("errors", "error_logs")):
        recommended_checks.append(
            "Inspect the error-rate and error-log evidence for the affected service."
        )

    report = ReportBuilder.build(
        incident_id=f"inc-{uuid.uuid4().hex[:8]}",
        detected_at=alert_time,
        affected_services=[req.service],
        detection=detection,
        evidence=evidence,
        ranked_causes=ranked,
        alternatives=[],
        unknowns=unknowns,
        recommended_checks=recommended_checks,
        remediation={
            "status": "not_run",
            "detail": "This project reports recommendations but does not execute remediation.",
        },
    )
    return report
