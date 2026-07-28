from __future__ import annotations

from typing import Iterable


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    denominator = 2 * true_positive + false_positive + false_negative
    return 0.0 if denominator == 0 else 2 * true_positive / denominator


def binary_classification_metrics(
    expected: Iterable[str],
    predicted: Iterable[str],
) -> dict[str, float | int]:
    expected_values = list(expected)
    predicted_values = list(predicted)
    if not expected_values or len(expected_values) != len(predicted_values):
        raise ValueError("Expected and predicted labels must have equal nonzero length")
    valid = {"safe", "unsafe"}
    if set(expected_values) - valid or set(predicted_values) - valid:
        raise ValueError("Only safe and unsafe labels are supported")

    unsafe_tp = sum(
        truth == "unsafe" and guess == "unsafe"
        for truth, guess in zip(expected_values, predicted_values)
    )
    unsafe_fp = sum(
        truth == "safe" and guess == "unsafe"
        for truth, guess in zip(expected_values, predicted_values)
    )
    unsafe_fn = sum(
        truth == "unsafe" and guess == "safe"
        for truth, guess in zip(expected_values, predicted_values)
    )
    safe_tp = sum(
        truth == "safe" and guess == "safe"
        for truth, guess in zip(expected_values, predicted_values)
    )
    safe_fp = unsafe_fn
    safe_fn = unsafe_fp
    unsafe_total = sum(value == "unsafe" for value in expected_values)
    correct = sum(
        truth == guess for truth, guess in zip(expected_values, predicted_values)
    )
    unsafe_recall = (
        0.0 if unsafe_total == 0 else unsafe_tp / unsafe_total
    )
    return {
        "sample_count": len(expected_values),
        "accuracy": correct / len(expected_values),
        "macro_f1": (
            _f1(safe_tp, safe_fp, safe_fn)
            + _f1(unsafe_tp, unsafe_fp, unsafe_fn)
        )
        / 2,
        "unsafe_recall": unsafe_recall,
        "false_safe_rate": 1.0 - unsafe_recall,
        "unsafe_true_positive": unsafe_tp,
        "unsafe_false_positive": unsafe_fp,
        "unsafe_false_negative": unsafe_fn,
    }
