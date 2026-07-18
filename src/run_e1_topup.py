# ruff: noqa: E402
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import run_e1
from e1_pipeline.artifacts import (
    JsonlCheckpoint,
    canonical_json_bytes,
    sha256_file,
    write_checksum_manifest,
)
from e1_pipeline.models import Qwen3VLRunner
from e1_pipeline.pipeline import run_generation
from e1_pipeline.run_config import write_once
from e1_pipeline.topup import (
    merge_generation_records,
    select_limit_hits,
    validate_model_provenance,
)


def _write_bytes_once(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"Existing artifact does not match regenerated data: {path}")
        return
    path.write_bytes(content)


def _jsonl_bytes(rows) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _write_generation_status(run_dir: Path, topup_count: int) -> None:
    topup_status = json.loads(
        (run_dir / "topup_generation_status.json").read_text(encoding="utf-8")
    )
    status = {
        "stage": "generation",
        "state": "completed",
        "started_at_unix": topup_status["started_at_unix"],
        "ended_at_unix": topup_status["ended_at_unix"],
        "exit_status": 0,
        "pid": topup_status["pid"],
        "strategy": "adaptive_limit_hit_topup",
        "topup_count": topup_count,
    }
    _write_bytes_once(
        run_dir / "generation_status.json",
        canonical_json_bytes(status) + b"\n",
    )


def run(args) -> None:
    base_run = Path(args.base_run).resolve()
    run_dir = Path(args.run_dir).resolve()
    dataset_root = Path(args.dataset_root).resolve()
    vlm_path = Path(args.model_path).resolve()
    guard_path = Path(args.guard_model_path).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    base_config = json.loads(
        (base_run / "config.json").read_text(encoding="utf-8")
    )
    base_status = json.loads(
        (base_run / "generation_status.json").read_text(encoding="utf-8")
    )
    if base_status.get("state") != "completed":
        raise ValueError("Base generation did not complete")
    if int(base_config["max_new_tokens"]) != args.base_limit:
        raise ValueError(
            f"Base run used {base_config['max_new_tokens']} tokens, expected {args.base_limit}"
        )
    if args.max_new_tokens <= args.base_limit:
        raise ValueError("Top-up token limit must exceed the base limit")

    base_rows = run_e1.require_generation_complete(base_run)
    limit_hits = select_limit_hits(base_rows, base_limit=args.base_limit)
    selected_ids = [row["sample_id"] for row in limit_hits]
    if not selected_ids:
        raise ValueError("Base generation contains no limit-hit records")

    provenance = run_e1.model_provenance(vlm_path)
    validate_model_provenance(base_config, provenance)
    topup_config = {
        "stage": "topup_generation",
        "strategy": "rerun_only_base_limit_hits",
        "base_run": str(base_run),
        "base_generation_sha256": sha256_file(base_run / "generation.jsonl"),
        "base_config_sha256": sha256_file(base_run / "config.json"),
        "base_limit": args.base_limit,
        "max_new_tokens": args.max_new_tokens,
        "selected_count": len(selected_ids),
        "sample_ids": selected_ids,
        "dataset_root": str(dataset_root),
        "min_pixels": int(base_config["min_pixels"]),
        "max_pixels": int(base_config["max_pixels"]),
        **provenance,
    }
    write_once(run_dir / "topup_config.json", topup_config)
    run_e1.write_environment(run_dir)

    all_samples = run_e1.load_samples(base_config["dataset"], dataset_root)
    sample_by_id = {sample.sample_id: sample for sample in all_samples}
    missing_samples = sorted(set(selected_ids) - set(sample_by_id))
    if missing_samples:
        raise ValueError(f"Top-up samples are missing from the datasets: {missing_samples}")
    selected_samples = [sample_by_id[sample_id] for sample_id in selected_ids]

    runner = Qwen3VLRunner(
        vlm_path,
        max_new_tokens=args.max_new_tokens,
        min_pixels=int(base_config["min_pixels"]),
        max_pixels=int(base_config["max_pixels"]),
    )
    with run_e1.track_stage(run_dir, "topup_generation"):
        try:
            counts = run_generation(
                selected_samples,
                runner,
                JsonlCheckpoint(run_dir / "generation_topup.jsonl"),
                run_e1.progress,
            )
        finally:
            runner.close()
    run_e1.safe_print(json.dumps(counts), flush=True)

    replacement_rows = run_e1._load_jsonl_records(
        run_dir / "generation_topup.jsonl"
    )
    merged_rows = merge_generation_records(
        base_rows,
        replacement_rows,
        base_limit=args.base_limit,
        topup_limit=args.max_new_tokens,
    )
    final_config = {
        "stage": "generation",
        "strategy": "adaptive_limit_hit_topup",
        "dataset": base_config["dataset"],
        "dataset_root": str(dataset_root),
        "seed": base_config["seed"],
        "limit": None,
        "max_new_tokens": args.max_new_tokens,
        "initial_max_new_tokens": args.base_limit,
        "topup_count": len(selected_ids),
        "topup_selection_rule": "base output_tokens >= initial_max_new_tokens",
        "base_generation_sha256": sha256_file(base_run / "generation.jsonl"),
        "min_pixels": int(base_config["min_pixels"]),
        "max_pixels": int(base_config["max_pixels"]),
        "sample_ids": list(base_config["sample_ids"]),
        **provenance,
    }
    write_once(run_dir / "config.json", final_config)
    _write_bytes_once(run_dir / "generation.jsonl", _jsonl_bytes(merged_rows))
    _write_generation_status(run_dir, len(selected_ids))

    run_e1.command_moderate(
        SimpleNamespace(
            run_dir=run_dir,
            model_path=guard_path,
            max_new_tokens=args.guard_max_new_tokens,
        )
    )
    run_e1.command_summarize(
        SimpleNamespace(
            run_dir=run_dir,
            max_limit_hit_rate=args.max_limit_hit_rate,
        )
    )
    write_checksum_manifest(
        run_dir,
        [
            "topup_config.json",
            "topup_generation_status.json",
            "generation_topup.jsonl",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-run", default="/root/result/full-qwen3vl4b-512")
    parser.add_argument(
        "--run-dir", default="/root/result/full-qwen3vl4b-2048-topup"
    )
    parser.add_argument("--dataset-root", default="/root/datasets")
    parser.add_argument(
        "--model-path", default="/root/autodl-tmp/model/Qwen3-VL-4B-Instruct"
    )
    parser.add_argument(
        "--guard-model-path", default="/root/autodl-tmp/model/Qwen3Guard-Gen-4B"
    )
    parser.add_argument("--base-limit", type=int, default=512)
    parser.add_argument("--max-new-tokens", type=int, default=2048)
    parser.add_argument("--guard-max-new-tokens", type=int, default=64)
    parser.add_argument("--max-limit-hit-rate", type=float, default=0.01)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
