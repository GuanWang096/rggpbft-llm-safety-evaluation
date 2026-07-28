from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = spec_from_file_location("run_mj3_mj4", SCRIPT_DIR / "run_mj3_mj4.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _freeze() -> dict:
    reliability = {
        model: {
            "g_unsafe": 0.8,
            "g_safe": 0.8,
            "counts": {"tp": 8, "fn": 2, "tn": 8, "fp": 2},
        }
        for model in MODULE.MODELS
    }
    return {
        "beta_prior": {"a": 1.0, "b": 1.0},
        "probability_clip_epsilon": 0.05,
        "class_prior_unsafe": 0.5,
        "selected_decay": 0.9,
        "thresholds": {"tau_safe": 0.4, "tau_unsafe": 0.6},
        "class_conditional_reliability": reliability,
    }


def _matrix(count: int, label: str = "unsafe") -> list[dict[str, str]]:
    return [{model: label for model in MODULE.MODELS} for _ in range(count)]


def test_build_then_exploit_changes_only_frozen_exploit_window() -> None:
    expected = ["unsafe"] * 20
    original = _matrix(20)
    attacked = MODULE.attacked_matrix(
        original,
        expected,
        "build_then_exploit",
        MODULE.SEED_BASE,
    )
    start, end = MODULE.attack_window(20)
    for index, row in enumerate(attacked):
        expected_target = "safe" if start <= index < end else "unsafe"
        assert row[MODULE.PRIMARY_ATTACK_TARGET] == expected_target
        assert original[index][MODULE.PRIMARY_ATTACK_TARGET] == "unsafe"


def test_random_attack_is_seed_reproducible() -> None:
    expected = ["safe", "unsafe"] * 25
    matrix = _matrix(50, "safe")
    first = MODULE.attacked_matrix(matrix, expected, "random_flip_25", 7)
    second = MODULE.attacked_matrix(matrix, expected, "random_flip_25", 7)
    third = MODULE.attacked_matrix(matrix, expected, "random_flip_25", 8)
    assert first == second
    assert first != third


def test_review_only_feedback_does_not_update_automatic_records() -> None:
    expected = ["safe"] * 12
    matrix = _matrix(12, "safe")
    trace = MODULE.dynamic_trace(
        expected,
        matrix,
        _freeze(),
        feedback_scope="review",
    )
    assert set(trace["decisions"]) == {"safe"}
    assert trace["update_count"] == 0


def test_full_feedback_updates_after_every_decision() -> None:
    expected = ["safe", "unsafe"] * 6
    matrix = _matrix(12, "safe")
    trace = MODULE.dynamic_trace(
        expected,
        matrix,
        _freeze(),
        feedback_scope="all",
    )
    assert trace["update_count"] == len(expected)


def test_sliding_window_uses_validation_seed_without_label_leakage() -> None:
    expected = ["safe", "unsafe"] * 5
    matrix = _matrix(10, "safe")
    trace = MODULE.dynamic_trace(
        expected,
        matrix,
        _freeze(),
        window_size=4,
        window_seed=(expected[:4], matrix[:4]),
        feedback_scope="review",
    )
    assert len(trace["decisions"]) == len(expected)
    assert len(trace["reliability_trace"]) == len(expected)
