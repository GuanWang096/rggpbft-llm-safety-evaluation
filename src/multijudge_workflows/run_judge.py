from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
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

from multijudge.dataset import load_mmds_manifest  # noqa: E402
from multijudge.policy import load_policy  # noqa: E402
from multijudge.runtime import collect_environment  # noqa: E402
from multijudge.qwen3_vl import (  # noqa: E402
    NATIVE_MAX_PIXELS,
    NATIVE_MIN_PIXELS,
    PANEL_MAX_PIXELS,
    PANEL_MIN_PIXELS,
    Qwen3VLJudgeAdapter,
)
from multijudge.schema import sha256_file  # noqa: E402


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def completed_sample_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    completed: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                completed.add(str(json.loads(line)["sample_id"]))
            except (json.JSONDecodeError, KeyError) as exc:
                raise RuntimeError(
                    f"Invalid existing result at line {line_number}: {path}"
                ) from exc
    return completed


def progress_iterator(values: list[Any]) -> Any:
    try:
        from tqdm import tqdm
    except ImportError:
        return values
    return tqdm(values, unit="sample", dynamic_ncols=True)


def seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed % (2**32))
    except ImportError:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def run(args: argparse.Namespace) -> None:
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "judgments.jsonl"
    status_path = run_dir / "status.json"
    panel_dir = run_dir / "panels"

    samples = load_mmds_manifest(args.dataset_root, args.manifest)
    if args.limit is not None:
        samples = samples[: args.limit]
    existing = completed_sample_ids(result_path)
    pending = [sample for sample in samples if sample.sample_id not in existing]
    policy = load_policy(args.policy)
    seed_everything(args.seed)
    adapter = Qwen3VLJudgeAdapter(
        model_path=args.model_path,
        model_revision=args.model_revision,
        policy=policy,
        max_new_tokens=args.max_new_tokens,
        attn_implementation=args.attn_implementation,
    )

    model_fingerprint = None
    if args.model_fingerprint is not None:
        model_fingerprint = json.loads(
            args.model_fingerprint.read_text(encoding="utf-8")
        )
        if (
            Path(model_fingerprint["model_path"]).resolve()
            != args.model_path.resolve()
        ):
            raise RuntimeError(
                "Model fingerprint was generated for a different model directory"
            )

    config = {
        "schema": "multijudge-run-config-v2",
        "adapter": "qwen3-vl",
        "input_mode": args.input_mode,
        "dataset_root": str(args.dataset_root.resolve()),
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256_file(args.manifest.resolve()),
        "policy": str(args.policy.resolve()),
        "policy_sha256": policy.sha256,
        "model_path": str(args.model_path.resolve()),
        "model_revision": args.model_revision,
        "model_manifest_sha256": (
            model_fingerprint["manifest_sha256"]
            if model_fingerprint is not None
            else None
        ),
        "seed": args.seed,
        "max_new_tokens": args.max_new_tokens,
        "attn_implementation": args.attn_implementation,
        "native_min_pixels": NATIVE_MIN_PIXELS,
        "native_max_pixels": NATIVE_MAX_PIXELS,
        "panel_min_pixels": PANEL_MIN_PIXELS,
        "panel_max_pixels": PANEL_MAX_PIXELS,
        "selected_sample_count": len(samples),
    }
    config_path = run_dir / "config.json"
    if config_path.exists():
        prior = json.loads(config_path.read_text(encoding="utf-8"))
        if prior != config:
            raise RuntimeError(
                "Run directory contains a different config; choose a new run directory"
            )
    else:
        write_json(config_path, config)
    if model_fingerprint is not None:
        fingerprint_copy = run_dir / "model_fingerprint.json"
        if not fingerprint_copy.exists():
            write_json(fingerprint_copy, model_fingerprint)
    if not (run_dir / "environment.json").exists():
        write_json(run_dir / "environment.json", collect_environment())

    started = time.time()
    write_json(
        status_path,
        {
            "state": "running",
            "started_at_unix": started,
            "selected": len(samples),
            "already_completed": len(existing),
            "pending": len(pending),
        },
    )
    try:
        with result_path.open("a", encoding="utf-8", buffering=1) as output:
            for index, sample in enumerate(progress_iterator(pending), start=1):
                record = adapter.judge(
                    sample,
                    input_mode=args.input_mode,
                    panel_dir=panel_dir,
                )
                output.write(
                    json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                write_json(
                    status_path,
                    {
                        "state": "running",
                        "started_at_unix": started,
                        "selected": len(samples),
                        "completed": len(existing) + index,
                        "pending": len(pending) - index,
                        "last_sample_id": sample.sample_id,
                    },
                )
        parser_errors = 0
        with result_path.open("r", encoding="utf-8") as handle:
            records = [json.loads(line) for line in handle if line.strip()]
        selected_ids = {sample.sample_id for sample in samples}
        selected_records = [
            record for record in records if record["sample_id"] in selected_ids
        ]
        parser_errors = sum(
            record["decision"]["status"] != "ok" for record in selected_records
        )
        parser_failure_rate = parser_errors / len(samples) if samples else 1.0
        final_state = (
            "completed"
            if len(selected_records) == len(samples)
            and parser_failure_rate <= args.max_parser_failure_rate
            else "quality_gate_failed"
        )
        write_json(
            status_path,
            {
                "state": final_state,
                "started_at_unix": started,
                "ended_at_unix": time.time(),
                "selected": len(samples),
                "completed": len(selected_records),
                "parser_errors": parser_errors,
                "parser_failure_rate": parser_failure_rate,
                "max_parser_failure_rate": args.max_parser_failure_rate,
            },
        )
        if final_state != "completed":
            raise RuntimeError(
                f"Smoke quality gate failed: {parser_errors}/{len(samples)} "
                "parser errors"
            )
    except BaseException as exc:
        current = (
            json.loads(status_path.read_text(encoding="utf-8"))
            if status_path.exists()
            else {}
        )
        if current.get("state") not in {"quality_gate_failed", "completed"}:
            current.update(
                {
                    "state": "failed",
                    "ended_at_unix": time.time(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            write_json(status_path, current)
        raise
    finally:
        adapter.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a resumable Qwen3-VL MMDS judge smoke."
    )
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-fingerprint", type=Path)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--input-mode", choices=("native", "panel"), required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-new-tokens", type=int, default=96)
    parser.add_argument("--attn-implementation", default="sdpa")
    parser.add_argument("--max-parser-failure-rate", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=20260726)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
