from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = spec_from_file_location(
    "analyze_mj2_extended",
    SCRIPT_DIR / "analyze_mj2_extended.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _row(*unsafe_models: str) -> dict[str, str]:
    return {
        model: ("unsafe" if model in unsafe_models else "safe")
        for model in MODULE.MODELS
    }


def test_coverage_selection_keeps_confidence_ties_together() -> None:
    scores = [0.0, 1.0, 0.25, 0.75, 0.5]
    decisions, actual, _ = MODULE.decisions_at_coverage(scores, 0.8)
    assert actual == 0.8
    assert decisions[-1] == "review"


def test_coverage_selection_does_not_exceed_target() -> None:
    scores = [0.0, 1.0, 0.25, 0.75, 0.25, 0.75]
    _, actual, _ = MODULE.decisions_at_coverage(scores, 0.5)
    assert actual <= 0.5


def test_dawid_skene_converges_and_returns_probabilities() -> None:
    matrix = [
        _row(),
        _row(),
        _row("qwen", "safework", "internvl", "minicpm"),
        _row("qwen", "safework", "internvl", "minicpm"),
        _row("qwen", "safework", "internvl"),
        _row("safework", "internvl", "minicpm"),
    ]
    result = MODULE.ds_fit(matrix)
    assert result["converged"] is True
    assert len(result["posterior_unsafe"]) == len(matrix)
    assert all(0 < value < 1 for value in result["posterior_unsafe"])


def test_exact_mcnemar_known_discordance() -> None:
    expected = ["safe", "safe", "unsafe", "unsafe"]
    proposed = ["safe", "safe", "unsafe", "safe"]
    baseline = ["safe", "unsafe", "safe", "unsafe"]
    result = MODULE.exact_mcnemar(expected, proposed, baseline)
    assert result["proposed_correct_baseline_wrong"] == 2
    assert result["proposed_wrong_baseline_correct"] == 1
    assert result["discordant_pairs"] == 3


def test_three_judge_majority_has_no_review_or_tie_rule() -> None:
    committee = ("qwen", "safework", "minicpm")
    matrix = [
        _row("qwen", "safework"),
        _row("minicpm"),
    ]
    assert MODULE.committee_majority(matrix, committee) == ["unsafe", "safe"]
