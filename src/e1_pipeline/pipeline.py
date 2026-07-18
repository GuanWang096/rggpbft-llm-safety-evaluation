import json
from pathlib import Path

from .artifacts import JsonlCheckpoint, sha256_file


def run_generation(
    samples, runner, checkpoint: JsonlCheckpoint, on_record=None
) -> dict[str, int]:
    counts = {"selected": 0, "skipped": 0, "written": 0}
    for sample in samples:
        counts["selected"] += 1
        if sample.sample_id in checkpoint.completed_ids:
            counts["skipped"] += 1
            continue
        result = runner.generate(sample)
        record = {
            "sample_id": sample.sample_id,
            "dataset": sample.dataset,
            "variant": sample.variant,
            "risk_category": sample.risk_category,
            "expected_input_safe": sample.expected_input_safe,
            "image_path": str(sample.image_path),
            "image_sha256": sha256_file(sample.image_path),
            "prompt": sample.prompt,
            "response": result.response,
            "model_id": runner.model_id,
            "latency_ms": result.latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "peak_vram_bytes": result.peak_vram_bytes,
        }
        if checkpoint.append(record):
            counts["written"] += 1
            if on_record is not None:
                on_record(record, counts.copy())
    return counts


def _read_jsonl(path: Path):
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc


def run_moderation(
    generation_path: Path, runner, checkpoint: JsonlCheckpoint, on_record=None
) -> dict[str, int]:
    counts = {"selected": 0, "skipped": 0, "written": 0}
    for generation in _read_jsonl(generation_path):
        counts["selected"] += 1
        sample_id = generation["sample_id"]
        if sample_id in checkpoint.completed_ids:
            counts["skipped"] += 1
            continue
        result = runner.moderate(generation["prompt"], generation["response"])
        record = {
            "sample_id": sample_id,
            "dataset": generation["dataset"],
            "variant": generation["variant"],
            "risk_category": generation["risk_category"],
            "expected_input_safe": generation["expected_input_safe"],
            "safety": result.decision.safety,
            "categories": list(result.decision.categories),
            "refusal": result.decision.refusal,
            "guard_raw_output": result.raw_output,
            "guard_model_id": runner.model_id,
            "latency_ms": result.latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "peak_vram_bytes": result.peak_vram_bytes,
        }
        if checkpoint.append(record):
            counts["written"] += 1
            if on_record is not None:
                on_record(record, counts.copy())
    return counts

