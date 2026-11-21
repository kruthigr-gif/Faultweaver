from faults import inject


def test_latency_scenario_applies_and_clears_fault(monkeypatch):
    compose_calls = []
    sleeps = []
    monkeypatch.setattr(
        inject,
        "run_compose",
        lambda service, latency, error: compose_calls.append(
            (service, latency, error)
        ),
    )
    monkeypatch.setattr(inject.time, "sleep", sleeps.append)
    monkeypatch.setattr(inject, "wait_for_service", lambda: None)

    inject.inject_fault("inventory_latency_01")

    assert compose_calls == [
        ("inventory-service", 2000, None),
        ("inventory-service", 0, None),
    ]
    assert sleeps == [30, 60]


def test_error_scenario_applies_and_clears_fault(monkeypatch):
    compose_calls = []
    monkeypatch.setattr(
        inject,
        "run_compose",
        lambda service, latency, error: compose_calls.append(
            (service, latency, error)
        ),
    )
    monkeypatch.setattr(inject.time, "sleep", lambda _: None)
    monkeypatch.setattr(inject, "wait_for_service", lambda: None)

    inject.inject_fault("inventory_error_01")

    assert compose_calls == [
        ("inventory-service", 0, 500),
        ("inventory-service", 0, None),
    ]


def test_combined_scenario_applies_both_faults_and_clears_them(monkeypatch):
    compose_calls = []
    monkeypatch.setattr(
        inject,
        "run_compose",
        lambda service, latency, error: compose_calls.append(
            (service, latency, error)
        ),
    )
    monkeypatch.setattr(inject.time, "sleep", lambda _: None)
    monkeypatch.setattr(inject, "wait_for_service", lambda: None)

    inject.inject_fault("inventory_combined_01")

    assert compose_calls == [
        ("inventory-service", 1500, 500),
        ("inventory-service", 0, None),
    ]
