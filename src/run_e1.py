# ruff: noqa: E402
import argparse
from collections import defaultdict
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import sys
import time


HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE if (HERE / "e1_pipeline").is_dir() else HERE / "src"
sys.path.insert(0, str(PACKAGE_ROOT))

from e1_pipeline.artifacts import (
    JsonlCheckpoint,
    canonical_json_bytes,
    model_artifact_hashes,
    sha256_file,
    sha256_hex,
    write_checksum_manifest,
)
from e1_pipeline.datasets import load_mm_safetybench_tiny, load_vlguard
from e1_pipeline.metrics import summarize_decisions, summarize_generation_lengths
from e1_pipeline.models import Qwen3GuardRunner, Qwen3VLRunner
from e1_pipeline.pipeline import run_generation, run_moderation
from e1_pipeline.run_config import write_once
from e1_pipeline.selection import stratified_select


EXPERIMENTS_ROOT = HERE.parent
DATASET_ROOT = EXPERIMENTS_ROOT / "dataset"
MODEL_ROOT = EXPERIMENTS_ROOT / "model"


def load_samples(dataset: str, dataset_root: Path = DATASET_ROOT):
    samples = []
    if dataset in {"both", "mm"}:
        samples.extend(load_mm_safetybench_tiny(dataset_root / "MM-SafetyBench"))
    if dataset in {"both", "vlguard"}:
        samples.extend(load_vlguard(dataset_root / "VLGuard"))
    return samples


def write_environment(run_dir: Path) -> None:
    path = run_dir / "environment.json"
    if path.exists():
        return
    import torch
    import transformers

    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }
    path.write_bytes(canonical_json_bytes(environment) + b"\n")


def model_provenance(model_path: Path) -> dict:
    file_hashes = model_artifact_hashes(model_path)
    return {
        "model_path": str(model_path.resolve()),
        "model_files_sha256": file_hashes,
        "model_manifest_sha256": sha256_hex(canonical_json_bytes(file_hashes)),
    }


def _load_jsonl_records(path: Path) -> list[dict]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
    return records


def require_generation_complete(run_dir: Path) -> list[dict]:
    config_path = run_dir / "config.json"
    generation_path = run_dir / "generation.jsonl"
    if not config_path.is_file() or not generation_path.is_file():
        raise FileNotFoundError("Generation config or checkpoint is missing")
    expected_ids = json.loads(config_path.read_text(encoding="utf-8"))["sample_ids"]
    records = _load_jsonl_records(generation_path)
    actual_ids = [record["sample_id"] for record in records]
    if len(actual_ids) != len(expected_ids) or set(actual_ids) != set(expected_ids):
        raise ValueError(
            f"Generation checkpoint contains {len(actual_ids)} of {len(expected_ids)} configured samples"
        )
    return records


def require_moderation_complete(run_dir: Path) -> list[dict]:
    generation_records = _load_jsonl_records(run_dir / "generation.jsonl")
    moderation_records = _load_jsonl_records(run_dir / "moderation.jsonl")
    generation_ids = {record["sample_id"] for record in generation_records}
    moderation_ids = {record["sample_id"] for record in moderation_records}
    if (
        len(moderation_records) != len(generation_records)
        or moderation_ids != generation_ids
    ):
        raise ValueError(
            f"Found {len(moderation_records)} moderation records for {len(generation_records)} generation records"
        )
    return moderation_records


@contextmanager
def track_stage(run_dir: Path, stage: str):
    status_path = Path(run_dir) / f"{stage}_status.json"
    started_at = int(time.time())
    status = {
        "stage": stage,
        "state": "running",
        "started_at_unix": started_at,
        "ended_at_unix": None,
        "exit_status": None,
        "pid": os.getpid(),
    }
    status_path.write_bytes(canonical_json_bytes(status) + b"\n")
    try:
        yield
    except BaseException as exc:
        status.update(
            {
                "state": "failed",
                "ended_at_unix": int(time.time()),
                "exit_status": 1,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        status_path.write_bytes(canonical_json_bytes(status) + b"\n")
        raise
    else:
        status.update(
            {
                "state": "completed",
                "ended_at_unix": int(time.time()),
                "exit_status": 0,
            }
        )
        status_path.write_bytes(canonical_json_bytes(status) + b"\n")


def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except BrokenPipeError:
        pass


def progress(record, counts):
    safe_print(
        json.dumps(
            {
                "written": counts["written"],
                "sample_id": record["sample_id"],
                "latency_ms": round(record["latency_ms"], 1),
                "peak_vram_gib": round(record["peak_vram_bytes"] / 1024**3, 3),
            },
            ensure_ascii=True,
        ),
        flush=True,
    )


def command_inventory(args):
    samples = load_samples(args.dataset, args.dataset_root.resolve())
    counts = defaultdict(int)
    for sample in samples:
        counts[(sample.dataset, sample.variant)] += 1
    safe_print(json.dumps({"total": len(samples), "groups": {"|".join(k): v for k, v in counts.items()}}, indent=2))


def command_generate(args):
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset_root = args.dataset_root.resolve()
    samples = stratified_select(
        load_samples(args.dataset, dataset_root), args.limit, args.seed
    )
    model_path = args.model_path.resolve()
    config = {
        "stage": "generation",
        "dataset": args.dataset,
        "dataset_root": str(dataset_root),
        "seed": args.seed,
        "limit": args.limit,
        "max_new_tokens": args.max_new_tokens,
        "min_pixels": args.min_pixels,
        "max_pixels": args.max_pixels,
        "sample_ids": [sample.sample_id for sample in samples],
        **model_provenance(model_path),
    }
    write_once(run_dir / "config.json", config)
    write_environment(run_dir)
    runner = Qwen3VLRunner(
        model_path,
        max_new_tokens=args.max_new_tokens,
        min_pixels=args.min_pixels,
        max_pixels=args.max_pixels,
    )
    with track_stage(run_dir, "generation"):
        try:
            counts = run_generation(
                samples,
                runner,
                JsonlCheckpoint(run_dir / "generation.jsonl"),
                progress,
            )
        finally:
            runner.close()
    safe_print(json.dumps(counts), flush=True)


def command_moderate(args):
    run_dir = args.run_dir.resolve()
    generation_path = run_dir / "generation.jsonl"
    require_generation_complete(run_dir)
    write_environment(run_dir)
    model_path = args.model_path.resolve()
    moderation_config = {
        "stage": "moderation",
        "max_new_tokens": args.max_new_tokens,
        "generation_sha256": sha256_file(generation_path),
        **model_provenance(model_path),
    }
    write_once(run_dir / "moderation_config.json", moderation_config)
    runner = Qwen3GuardRunner(
        model_path, max_new_tokens=args.max_new_tokens
    )
    with track_stage(run_dir, "moderation"):
        try:
            counts = run_moderation(
                generation_path,
                runner,
                JsonlCheckpoint(run_dir / "moderation.jsonl"),
                progress,
            )
        finally:
            runner.close()
    safe_print(json.dumps(counts), flush=True)


def command_summarize(args):
    run_dir = args.run_dir.resolve()
    if not 0 <= args.max_limit_hit_rate <= 1:
        raise ValueError("max-limit-hit-rate must be between 0 and 1")
    rows = require_moderation_complete(run_dir)
    generation_rows = _load_jsonl_records(run_dir / "generation.jsonl")
    generation_config = json.loads(
        (run_dir / "config.json").read_text(encoding="utf-8")
    )
    groups = {"overall": summarize_decisions(rows)}
    for field in ("dataset", "variant", "risk_category"):
        values = sorted({row[field] for row in rows})
        for value in values:
            groups[f"{field}:{value}"] = summarize_decisions(
                row for row in rows if row[field] == value
            )
    summary = {
        "generated_at_unix": int(time.time()),
        "generation_lengths": summarize_generation_lengths(
            generation_rows,
            max_new_tokens=int(generation_config["max_new_tokens"]),
        ),
        "groups": groups,
    }
    (run_dir / "summary.json").write_bytes(canonical_json_bytes(summary) + b"\n")
    write_checksum_manifest(
        run_dir,
        [
            "config.json",
            "environment.json",
            "generation.jsonl",
            "generation_status.json",
            "moderation_config.json",
            "moderation.jsonl",
            "moderation_status.json",
            "summary.json",
        ],
    )
    safe_print(json.dumps(summary["groups"]["overall"], indent=2), flush=True)
    limit_hit_rate = summary["generation_lengths"]["limit_hit_rate"] or 0.0
    if limit_hit_rate > args.max_limit_hit_rate:
        raise RuntimeError(
            "Generation limit-hit rate "
            f"{limit_hit_rate:.2%} exceeds the quality limit "
            f"{args.max_limit_hit_rate:.2%}"
        )


def build_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--dataset", choices=("both", "mm", "vlguard"), default="both")
    inventory.add_argument("--dataset-root", type=Path, default=DATASET_ROOT)
    inventory.set_defaults(handler=command_inventory)

    generate = subparsers.add_parser("generate")
    generate.add_argument("--run-dir", type=Path, required=True)
    generate.add_argument("--dataset", choices=("both", "mm", "vlguard"), default="both")
    generate.add_argument("--dataset-root", type=Path, default=DATASET_ROOT)
    generate.add_argument("--limit", type=int)
    generate.add_argument("--seed", type=int, default=20260704)
    generate.add_argument("--max-new-tokens", type=int, default=512)
    generate.add_argument(
        "--model-path",
        type=Path,
        default=MODEL_ROOT / "Qwen3-VL-2B-Instruct",
    )
    generate.add_argument("--min-pixels", type=int, default=256 * 28 * 28)
    generate.add_argument("--max-pixels", type=int, default=1280 * 28 * 28)
    generate.set_defaults(handler=command_generate)

    moderate = subparsers.add_parser("moderate")
    moderate.add_argument("--run-dir", type=Path, required=True)
    moderate.add_argument("--max-new-tokens", type=int, default=64)
    moderate.add_argument(
        "--model-path",
        type=Path,
        default=MODEL_ROOT / "Qwen3Guard-Gen-0.6B",
    )
    moderate.set_defaults(handler=command_moderate)

    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--run-dir", type=Path, required=True)
    summarize.add_argument("--max-limit-hit-rate", type=float, default=0.01)
    summarize.set_defaults(handler=command_summarize)
    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.handler(parsed)
