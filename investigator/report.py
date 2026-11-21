"""Report construction helper.

The ``ReportBuilder`` class provides a static ``build`` method that assembles
all pieces of an incident into the JSON structure expected by the FastAPI
response model (``InvestigateResponse``).  Keeping the formatting logic in a
separate module makes it easy to replace with richer output (e.g., LLM
summarisation) later without touching the API.
"""

from typing import List, Dict


class ReportBuilder:
    @staticmethod
    def build(
        incident_id: str,
        detected_at: str,
        affected_services: List[str],
        detection: Dict,
        evidence: List[Dict],
        ranked_causes: List[Dict],
        alternatives: List[str],
        unknowns: List[str],
        recommended_checks: List[str],
        remediation: Dict[str, str],
    ) -> Dict:
        """Return a dict matching the ``InvestigateResponse`` schema.

        Parameters
        ----------
        incident_id: str
            Unique identifier for the incident.
        detected_at: str
            ISO‑8601 timestamp of the alert.
        affected_services: List[str]
            Services that exhibited the symptom.
        ranked_causes: List[Dict]
            Ranked root‑cause candidates (as produced by ``Diagnoser``).
        alternatives: List[str]
            Other plausible explanations.
        unknowns: List[str]
            Gaps in evidence.
        recommended_checks: List[str]
            Next investigative steps for a human operator.
        remediation: Dict[str, str]
            Placeholder for any remediation action status.
        """
        return {
            "incident_id": incident_id,
            "detected_at": detected_at,
            "affected_services": affected_services,
            "detection": detection,
            "evidence": evidence,
            "ranked_causes": ranked_causes,
            "alternatives": alternatives,
            "unknowns": unknowns,
            "recommended_checks": recommended_checks,
            "remediation": remediation,
        }
