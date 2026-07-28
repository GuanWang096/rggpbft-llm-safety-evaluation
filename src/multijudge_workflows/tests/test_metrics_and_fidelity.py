from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from multijudge.metrics import binary_classification_metrics


SCRIPT = (
    Path(__file__).resolve().parents[1] / "compare_qwen_input_modes.py"
)
SPEC = spec_from_file_location("compare_qwen_input_modes", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _record(expected: str, predicted: str) -> dict:
    return {
        "expected_label": expected,
        "decision": {"status": "ok", "label": predicted},
    }


def test_binary_metrics_known_values() -> None:
    metrics = binary_classification_metrics(
        ["safe", "safe", "unsafe", "unsafe"],
        ["safe", "unsafe", "unsafe", "safe"],
    )
    assert metrics["accuracy"] == 0.5
    assert metrics["macro_f1"] == 0.5
    assert metrics["unsafe_recall"] == 0.5


def test_fidelity_gate_passes_equal_predictions() -> None:
    native = {
        "a": _record("safe", "safe"),
        "b": _record("unsafe", "unsafe"),
    }
    result = MODULE.compare_modes(
        native,
        dict(native),
        macro_f1_tolerance=0.02,
        unsafe_recall_drop_tolerance=0.01,
    )
    assert result["gate"]["passed"] is True


def test_fidelity_gate_rejects_unsafe_recall_drop() -> None:
    native = {
        "a": _record("safe", "safe"),
        "b": _record("unsafe", "unsafe"),
    }
    panel = {
        "a": _record("safe", "safe"),
        "b": _record("unsafe", "safe"),
    }
    result = MODULE.compare_modes(
        native,
        panel,
        macro_f1_tolerance=1.0,
        unsafe_recall_drop_tolerance=0.01,
    )
    assert result["gate"]["passed"] is False
