from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON at {path}:{line_number}"
                ) from exc
    return records


def classification_counts(
    expected: list[str],
    predicted: list[str],
) -> dict[str, int | float]:
    tp = sum(e == "unsafe" and p == "unsafe" for e, p in zip(expected, predicted))
    tn = sum(e == "safe" and p == "safe" for e, p in zip(expected, predicted))
    fp = sum(e == "safe" and p == "unsafe" for e, p in zip(expected, predicted))
    fn = sum(e == "unsafe" and p == "safe" for e, p in zip(expected, predicted))
    return {
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": (tp + tn) / len(expected),
        "unsafe_recall": tp / (tp + fn) if tp + fn else 0.0,
        "safe_specificity": tn / (tn + fp) if tn + fp else 0.0,
    }


def parse_model(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Model input must be name=directory")
    name, raw_path = value.split("=", 1)
    if not name:
        raise argparse.ArgumentTypeError("Model name cannot be empty")
    return name, Path(raw_path)


def validate(
    manifest_path: Path,
    model_inputs: list[tuple[str, Path]],
) -> dict[str, Any]:
    manifest = read_json(manifest_path)
    entries = manifest["entries"]
    expected_ids = [str(value["sample_id"]) for value in entries]
    expected_labels = {
        str(value["sample_id"]): str(value["assistant_label"])
        for value in entries
    }
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("Manifest contains duplicate sample IDs")

    identities: set[tuple[str, str, str, str, str]] = set()
    models: dict[str, Any] = {}
    for name, run_dir in model_inputs:
        status = read_json(run_dir / "status.json")
        if status.get("state") != "completed":
            raise ValueError(f"{name} status is not completed")
        records = read_jsonl(run_dir / "judgments.jsonl")
        actual_ids = [str(value["sample_id"]) for value in records]
        if actual_ids != expected_ids:
            raise ValueError(f"{name} sample IDs or ordering differ from manifest")
        if any(value["decision"]["status"] != "ok" for value in records):
            raise ValueError(f"{name} contains parser failures")
        for value in records:
            sample_id = str(value["sample_id"])
            if str(value["expected_label"]) != expected_labels[sample_id]:
                raise ValueError(f"{name} label mismatch for {sample_id}")

        service = records[0]["judge_service"]
        identity = (
            str(service["organization"]),
            str(service["model_id"]),
            str(service["model_revision"]),
            str(service["policy_sha256"]),
            str(service["adapter_version"]),
        )
        if identity in identities:
            raise ValueError(f"Duplicate judge service identity: {identity}")
        identities.add(identity)
        predicted = [str(value["decision"]["label"]) for value in records]
        expected = [expected_labels[sample_id] for sample_id in expected_ids]
        models[name] = {
            "run_dir": str(run_dir.resolve()),
            "service_identity": {
                "organization": identity[0],
                "model_id": identity[1],
                "model_revision": identity[2],
                "policy_sha256": identity[3],
                "adapter_version": identity[4],
            },
            "sample_count": len(records),
            "parser_errors": 0,
            "peak_vram_gib_max": max(
                float(value["peak_vram_gib_max"]) for value in records
            ),
            "latency_ms_mean": sum(
                float(value["latency_ms_total"]) for value in records
            )
            / len(records),
            "classification": classification_counts(expected, predicted),
        }

    return {
        "schema": "mj1-output-acceptance-v1",
        "manifest": str(manifest_path.resolve()),
        "split": manifest["source_split"],
        "sample_count": len(expected_ids),
        "model_count": len(models),
        "passed": True,
        "models": models,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate complete and aligned MJ1 judge outputs."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--model",
        action="append",
        type=parse_model,
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.manifest.resolve(), args.model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
