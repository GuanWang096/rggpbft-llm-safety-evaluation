from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


V15_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = (
    V15_ROOT / "src"
    if (V15_ROOT / "src" / "multijudge").is_dir()
    else V15_ROOT
)
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multijudge.metrics import binary_classification_metrics  # noqa: E402


def load_records(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            sample_id = str(record["sample_id"])
            if sample_id in records:
                raise ValueError(f"Duplicate sample ID in {path}: {sample_id}")
            if record["decision"]["status"] != "ok":
                raise ValueError(
                    f"Parser failure in {path} line {line_number}: {sample_id}"
                )
            records[sample_id] = record
    if not records:
        raise ValueError(f"No records found in {path}")
    return records


def compare_modes(
    native: dict[str, dict[str, Any]],
    panel: dict[str, dict[str, Any]],
    *,
    macro_f1_tolerance: float,
    unsafe_recall_drop_tolerance: float,
) -> dict[str, Any]:
    if set(native) != set(panel):
        missing_native = sorted(set(panel) - set(native))
        missing_panel = sorted(set(native) - set(panel))
        raise ValueError(
            "Input modes contain different sample IDs; "
            f"missing_native={missing_native[:5]}, missing_panel={missing_panel[:5]}"
        )
    sample_ids = sorted(native)
    expected: list[str] = []
    native_predicted: list[str] = []
    panel_predicted: list[str] = []
    disagreement_count = 0
    for sample_id in sample_ids:
        native_record = native[sample_id]
        panel_record = panel[sample_id]
        if native_record["expected_label"] != panel_record["expected_label"]:
            raise ValueError(f"Expected-label mismatch for {sample_id}")
        expected.append(str(native_record["expected_label"]))
        native_label = str(native_record["decision"]["label"])
        panel_label = str(panel_record["decision"]["label"])
        native_predicted.append(native_label)
        panel_predicted.append(panel_label)
        disagreement_count += native_label != panel_label

    native_metrics = binary_classification_metrics(expected, native_predicted)
    panel_metrics = binary_classification_metrics(expected, panel_predicted)
    macro_f1_difference = (
        float(panel_metrics["macro_f1"]) - float(native_metrics["macro_f1"])
    )
    unsafe_recall_difference = (
        float(panel_metrics["unsafe_recall"])
        - float(native_metrics["unsafe_recall"])
    )
    passed = (
        abs(macro_f1_difference) <= macro_f1_tolerance
        and unsafe_recall_difference >= -unsafe_recall_drop_tolerance
    )
    return {
        "schema": "qwen-input-mode-fidelity-v1",
        "sample_count": len(sample_ids),
        "native_metrics": native_metrics,
        "panel_metrics": panel_metrics,
        "panel_minus_native": {
            "macro_f1": macro_f1_difference,
            "unsafe_recall": unsafe_recall_difference,
        },
        "prediction_disagreement_count": disagreement_count,
        "prediction_disagreement_rate": disagreement_count / len(sample_ids),
        "gate": {
            "macro_f1_absolute_tolerance": macro_f1_tolerance,
            "unsafe_recall_drop_tolerance": unsafe_recall_drop_tolerance,
            "passed": passed,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare Qwen3-VL native multi-image and panel smoke results."
    )
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--macro-f1-tolerance", type=float, default=0.02)
    parser.add_argument("--unsafe-recall-drop-tolerance", type=float, default=0.01)
    args = parser.parse_args()

    result = compare_modes(
        load_records(args.native.resolve()),
        load_records(args.panel.resolve()),
        macro_f1_tolerance=args.macro_f1_tolerance,
        unsafe_recall_drop_tolerance=args.unsafe_recall_drop_tolerance,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["gate"]["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
