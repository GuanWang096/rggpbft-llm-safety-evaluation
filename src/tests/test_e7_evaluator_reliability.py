import pathlib
import sys

import json
import pytest


HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from generate_e7_evaluator_reliability import (  # noqa: E402
    DEFAULT_RELIABILITY_LEVELS,
    build_controlled_evaluator_artifacts,
    balanced_agreement,
    reference_decision,
)
import run_e7_fabric  # noqa: E402
import run_e7_consensus  # noqa: E402


def test_reference_decision_keeps_safety_and_refusal_separate():
    assert reference_decision({"safety": "Safe", "refusal": "No"}) == (0, 0)
    assert reference_decision({"safety": "Controversial", "refusal": "No"}) == (1, 0)
    assert reference_decision({"safety": "Unsafe", "refusal": "Yes"}) == (1, 1)


def test_balanced_agreement_gives_equal_weight_to_both_classes():
    reference = [0, 0, 0, 1]
    report = [0, 0, 0, 0]
    assert balanced_agreement(reference, report) == 0.5


def test_controlled_evaluators_score_the_same_records_deterministically():
    pattern = [
        ("Safe", "No"),
        ("Safe", "Yes"),
        ("Controversial", "No"),
        ("Unsafe", "Yes"),
    ]
    records = [
        {"sample_id": f"s{i}", "safety": safety, "refusal": refusal}
        for i, (safety, refusal) in enumerate(pattern * 20)
    ]
    first = build_controlled_evaluator_artifacts(records, evaluator_count=4, seed_base=20260705)
    second = build_controlled_evaluator_artifacts(records, evaluator_count=4, seed_base=20260705)

    assert first == second
    assert all(profile["sample_count"] == len(records) for profile in first["profiles"])
    assert len(first["reports"]) == 4 * len(records)
    assert first["reference_semantics"] == "frozen_guard_operational_proxy"


def test_realized_reputation_recovers_controlled_reliability_order():
    records = []
    for i in range(100):
        records.append({
            "sample_id": f"safe-{i}",
            "safety": "Safe",
            "refusal": "Yes" if i % 2 else "No",
        })
        records.append({
            "sample_id": f"unsafe-{i}",
            "safety": "Unsafe" if i % 2 else "Controversial",
            "refusal": "No" if i % 2 else "Yes",
        })

    artifacts = build_controlled_evaluator_artifacts(
        records,
        evaluator_count=4,
        seed_base=20260705,
        reliability_levels=DEFAULT_RELIABILITY_LEVELS[:4],
    )
    expected = sorted(
        artifacts["profiles"], key=lambda profile: (-profile["target_reliability"], profile["node_id"])
    )
    realized = sorted(
        artifacts["profiles"], key=lambda profile: (-profile["score_ppm"], profile["node_id"])
    )

    assert [p["node_id"] for p in realized] == [p["node_id"] for p in expected]
    assert artifacts["metrics"]["spearman_target_vs_score"] == 1.0
    assert artifacts["metrics"]["top_k_precision"] == 1.0


def test_fabric_score_loader_requires_explicit_proxy_semantics(tmp_path):
    score_file = tmp_path / "scores.json"
    score_file.write_text(json.dumps({
        "schema": "zte-sci-e7-evaluator-reliability-v1",
        "reference_semantics": "frozen_guard_operational_proxy",
        "reference_is_human_ground_truth": False,
        "scores_ppm": {str(i): 980000 - 10000 * i for i in range(16)},
    }), encoding="utf-8")

    scores, provenance = run_e7_fabric.load_score_artifact(score_file)
    assert scores[0] == 980000
    assert provenance["reference_semantics"] == "frozen_guard_operational_proxy"
    assert provenance["reference_is_human_ground_truth"] is False

    payload = json.loads(score_file.read_text(encoding="utf-8"))
    payload["reference_is_human_ground_truth"] = True
    score_file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="human ground truth"):
        run_e7_fabric.load_score_artifact(score_file)


def test_consensus_matrix_carries_score_provenance():
    fabric_results = [{
        "scenario": "E7-S0",
        "repeat": 0,
        "task_id": "task-0",
        "digest": "a" * 64,
        "evaluator_count": 16,
        "reputation_order": list(range(16)),
        "score_schema": "zte-sci-e7-evaluator-reliability-v1",
        "score_reference_semantics": "frozen_guard_operational_proxy",
        "score_reference_is_human_ground_truth": False,
    }]
    matrix = run_e7_consensus.build_e7_consensus_matrix(fabric_results)
    assert matrix[0]["score_schema"] == "zte-sci-e7-evaluator-reliability-v1"
    assert matrix[0]["score_reference_semantics"] == "frozen_guard_operational_proxy"
    assert matrix[0]["score_reference_is_human_ground_truth"] is False
