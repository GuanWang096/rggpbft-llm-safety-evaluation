from __future__ import annotations

import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(relative_path: str) -> dict:
    path = ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"Missing release artifact: {relative_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _close(actual: float, expected: float, name: str) -> None:
    if not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12):
        raise AssertionError(f"{name}: expected {expected}, found {actual}")


def verify_multijudge_claim() -> None:
    results = _load("results/multijudge/analysis/mj2_extended_results.json")
    claim = results["headline_same_committee_comparison"]
    assert claim["sample_count"] == 330
    assert claim["committee"] == ["qwen", "safework", "minicpm"]
    assert claim["baseline"] == "unweighted_2_of_3_majority"
    assert claim["proposed"] == "class_conditional_reliability_likelihood"

    baseline = claim["baseline_metrics"]
    proposed = claim["proposed_metrics"]
    delta = claim["proposed_minus_baseline"]
    _close(baseline["macro_f1"], 0.7371797708066015, "baseline macro-F1")
    _close(proposed["macro_f1"], 0.8932468067169227, "proposed macro-F1")
    _close(delta["macro_f1"], 0.15606703591032123, "macro-F1 delta")
    _close(baseline["unsafe_recall"], 0.4728682170542636, "baseline recall")
    _close(proposed["unsafe_recall"], 0.8294573643410853, "proposed recall")
    _close(delta["unsafe_recall"], 0.35658914728682173, "recall delta")

    bootstrap = claim["cluster_bootstrap"]
    assert bootstrap["replicates"] == 2000
    assert bootstrap["macro_f1_delta_95_ci"] == [
        0.1044639433865866,
        0.21294451892191651,
    ]
    assert bootstrap["unsafe_recall_delta_95_ci"] == [
        0.2671628498727735,
        0.45386363636363625,
    ]
    mcnemar = claim["mcnemar"]
    assert mcnemar["proposed_correct_baseline_wrong"] == 46
    assert mcnemar["proposed_wrong_baseline_correct"] == 7
    _close(mcnemar["exact_two_sided_p"], 4.003196174551249e-08, "McNemar p")


def verify_cross_layer_claim() -> None:
    audit = _load("results/cross_layer/formal/FINAL_INTEGRITY_AUDIT.json")
    assert audit["verdict"] == "PASS"
    assert all(audit["checks"].values())
    assert audit["totals"] == {
        "driver_results": 1728,
        "fabric_transactions": 8640,
        "protocol_certificates": 1728,
        "stage_a_rows": 1728,
        "stage_c_rows": 1728,
    }


def verify_required_files() -> None:
    required = [
        "results/multijudge/formal/validation_frozen.json",
        "results/multijudge/formal/test/acceptance.json",
        "results/multijudge/analysis/mj1_mj2_results.json",
        "results/multijudge/analysis/mj2_extended_results.json",
        "results/multijudge/analysis/mj3_mj4_results.json",
        "results/cross_layer/workload/matrix.json",
        "results/cross_layer/formal/aggregate.json",
        "results/cross_layer/formal/formal_analysis.json",
        "results/cross_layer/formal/FORMAL_RESULTS_REPORT.md",
        "src/multijudge_workflows/configs/canonical_policy_v1.json",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    if missing:
        raise AssertionError(f"Missing required files: {missing}")


def main() -> None:
    verify_required_files()
    verify_multijudge_claim()
    verify_cross_layer_claim()
    print("RELEASE_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
