from types import SimpleNamespace

import pytest

from investigator.evaluation import summarize_detection
from scripts import evaluate_live


def test_detection_metrics_use_labeled_live_observations_and_exclude_gaps():
    summary = summarize_detection(
        [
            {"expected_anomaly": False, "status": "normal"},
            {"expected_anomaly": False, "status": "anomaly"},
            {"expected_anomaly": True, "status": "anomaly"},
            {"expected_anomaly": True, "status": "normal"},
            {"expected_anomaly": True, "status": "insufficient_data"},
            {"expected_anomaly": False, "status": "normal"},
        ]
    )

    assert summary["evaluated_samples"] == 5
    assert summary["insufficient_data_samples"] == 1
    assert summary["true_positive"] == 1
    assert summary["false_positive"] == 1
    assert summary["true_negative"] == 2
    assert summary["false_negative"] == 1
    assert summary["precision"] == pytest.approx(0.5)
    assert summary["false_positive_rate"] == pytest.approx(1 / 3)
    assert summary["recall"] == pytest.approx(0.5)


def test_detection_metrics_handle_empty_denominators_without_inventing_scores():
    summary = summarize_detection(
        [{"expected_anomaly": False, "status": "insufficient_data"}]
    )

    assert summary["evaluated_samples"] == 0
    assert summary["precision"] is None
    assert summary["false_positive_rate"] is None


def test_evaluation_resets_only_the_demo_inventory_items(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(evaluate_live.subprocess, "run", fake_run)

    evaluate_live._reset_local_inventory()

    command, options = calls[0]
    assert command[-1] == (
        "UPDATE inventory SET stock=10000 WHERE item_id IN ('itemA','itemB');"
    )
    assert options["cwd"] == evaluate_live.ROOT
    assert options["check"] is False


def test_evaluation_restores_demo_inventory_even_when_run_fails(monkeypatch):
    resets = []

    def fail_evaluation(*_args):
        raise RuntimeError("evaluation failed")

    monkeypatch.setattr(
        evaluate_live,
        "_reset_local_inventory",
        lambda: resets.append("reset"),
    )
    monkeypatch.setattr(
        evaluate_live,
        "_run_live_evaluation",
        fail_evaluation,
    )

    with pytest.raises(RuntimeError, match="evaluation failed"):
        evaluate_live.run_evaluation(360, 2)

    assert resets == ["reset", "reset"]
