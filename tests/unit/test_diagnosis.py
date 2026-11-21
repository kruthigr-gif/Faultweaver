from investigator.diagnosis import Diagnoser


def test_diagnoser_does_not_treat_empty_evidence_as_high_confidence():
    ranked = Diagnoser().rank(
        "order-service",
        [
            {
                "evidence_id": f"id-{source}",
                "service": "order-service",
                "source": source,
                "signal": signal,
                "score": 0,
            }
            for source, signal in (
                ("prometheus", "latency"),
                ("loki", "error_logs"),
                ("tempo", "traces"),
            )
        ],
    )

    assert ranked[0]["service"] == "order-service"
    assert ranked[0]["confidence"] == "low"
    assert "unconfirmed" in ranked[0]["cause"]


def test_diagnoser_ranks_correlated_error_signals_first():
    ranked = Diagnoser().rank(
        "order-service",
        [
            {
                "evidence_id": "inventory-latency",
                "service": "inventory-service",
                "signal": "latency",
                "score": 4,
            },
            {
                "evidence_id": "inventory-errors",
                "service": "inventory-service",
                "signal": "errors",
                "score": 5,
            },
            {
                "evidence_id": "order-latency",
                "service": "order-service",
                "signal": "latency",
                "score": 0,
            },
        ],
    )

    assert ranked[0]["service"] == "inventory-service"
    assert ranked[0]["confidence"] == "high"
    assert ranked[0]["evidence_ids"] == [
        "inventory-latency",
        "inventory-errors",
    ]
