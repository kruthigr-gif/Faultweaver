from typing import Dict, List


class Diagnoser:
    """Rank services using positive, actionable telemetry signals."""

    def rank(self, primary_service: str, evidence: List[Dict]) -> List[Dict]:
        scores: Dict[str, float] = {}
        signals: Dict[str, List[str]] = {}
        evidence_ids: Dict[str, List[str]] = {}

        for item in evidence:
            if not item.get("available", True):
                continue
            service = item.get("service")
            if not service:
                continue
            scores[service] = scores.get(service, 0) + float(item.get("score", 0))
            evidence_ids.setdefault(service, []).append(item["evidence_id"])
            if item.get("score", 0) > 0:
                signals.setdefault(service, []).append(item["signal"])

        scores.setdefault(primary_service, 0)
        services = sorted(scores, key=lambda service: (-scores[service], service))
        ranked = []
        for service in services:
            score = scores[service]
            positive_signals = list(dict.fromkeys(signals.get(service, [])))
            if score >= 7 and len(positive_signals) >= 2:
                confidence = "high"
            elif score >= 3:
                confidence = "medium"
            else:
                confidence = "low"

            cause = (
                "Corroborating signals: " + ", ".join(positive_signals)
                if positive_signals
                else "No corroborating anomaly signals were found; this is an unconfirmed lead."
            )
            ranked.append(
                {
                    "service": service,
                    "cause": cause,
                    "confidence": confidence,
                    "evidence_ids": evidence_ids.get(service, []),
                }
            )
        return ranked
