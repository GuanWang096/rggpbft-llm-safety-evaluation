import builtins
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_e1
from e1_pipeline.models import Qwen3GuardRunner, Qwen3VLRunner


def test_progress_does_not_stop_run_when_output_pipe_closes(monkeypatch):
    def broken_print(*args, **kwargs):
        raise BrokenPipeError

    monkeypatch.setattr(builtins, "print", broken_print)
    run_e1.progress(
        {
            "sample_id": "s1",
            "latency_ms": 1.0,
            "peak_vram_bytes": 1024,
        },
        {"written": 1},
    )


def test_generation_completeness_rejects_partial_checkpoint(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"sample_ids": ["s1", "s2"]}), encoding="utf-8"
    )
    (tmp_path / "generation.jsonl").write_text(
        json.dumps({"sample_id": "s1"}) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="1 of 2"):
        run_e1.require_generation_complete(tmp_path)


def test_moderation_completeness_rejects_missing_decisions(tmp_path):
    (tmp_path / "generation.jsonl").write_text(
        json.dumps({"sample_id": "s1"}) + "\n"
        + json.dumps({"sample_id": "s2"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "moderation.jsonl").write_text(
        json.dumps({"sample_id": "s1"}) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="1 moderation records for 2"):
        run_e1.require_moderation_complete(tmp_path)


def test_cloud_generation_defaults_preserve_image_detail_and_response_length(tmp_path):
    args = run_e1.build_parser().parse_args(
        ["generate", "--run-dir", str(tmp_path)]
    )

    assert args.max_new_tokens == 512
    assert args.max_pixels == 1280 * 28 * 28


def test_vlm_runner_default_matches_formal_generation_budget():
    parameter = inspect.signature(Qwen3VLRunner.__init__).parameters["max_new_tokens"]

    assert parameter.default == 512


def test_guard_runner_default_matches_formal_moderation_budget():
    parameter = inspect.signature(Qwen3GuardRunner.__init__).parameters[
        "max_new_tokens"
    ]

    assert parameter.default == 64


def test_autodl_script_uses_new_run_directory_and_512_token_budget():
    script = (Path(__file__).parents[1] / "run_e1_full.sh").read_text(
        encoding="utf-8"
    )

    assert "RUN_DIR=/root/result/full-qwen3vl4b-512" in script
    assert "--max-new-tokens 512" in script
    assert "pipeline_status.json" in script
    assert "( run_pipeline )" in script
    assert "if run_pipeline" not in script


def test_summary_records_generation_limit_hit_rate(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"max_new_tokens": 512}), encoding="utf-8"
    )
    generation_rows = [
        {"sample_id": "s1", "output_tokens": 512},
        {"sample_id": "s2", "output_tokens": 20},
    ]
    moderation_rows = [
        {
            "sample_id": sample_id,
            "dataset": "test",
            "variant": "test",
            "risk_category": "safe",
            "expected_input_safe": True,
            "safety": "Safe",
            "refusal": "No",
        }
        for sample_id in ("s1", "s2")
    ]
    (tmp_path / "generation.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in generation_rows),
        encoding="utf-8",
    )
    (tmp_path / "moderation.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in moderation_rows),
        encoding="utf-8",
    )

    run_e1.command_summarize(
        SimpleNamespace(run_dir=tmp_path, max_limit_hit_rate=0.5)
    )

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["generation_lengths"]["limit_hit_count"] == 1
    assert summary["generation_lengths"]["limit_hit_rate"] == 0.5


def test_summary_rejects_generation_limit_hit_rate_above_quality_gate(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"max_new_tokens": 512}), encoding="utf-8"
    )
    generation = {"sample_id": "s1", "output_tokens": 512}
    moderation = {
        "sample_id": "s1",
        "dataset": "test",
        "variant": "test",
        "risk_category": "safe",
        "expected_input_safe": True,
        "safety": "Safe",
        "refusal": "No",
    }
    (tmp_path / "generation.jsonl").write_text(
        json.dumps(generation) + "\n", encoding="utf-8"
    )
    (tmp_path / "moderation.jsonl").write_text(
        json.dumps(moderation) + "\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="quality limit"):
        run_e1.command_summarize(
            SimpleNamespace(run_dir=tmp_path, max_limit_hit_rate=0.01)
        )


def test_summary_quality_gate_defaults_to_one_percent(tmp_path):
    args = run_e1.build_parser().parse_args(
        ["summarize", "--run-dir", str(tmp_path)]
    )

    assert args.max_limit_hit_rate == 0.01


def test_stage_status_records_success_and_failure(tmp_path):
    with run_e1.track_stage(tmp_path, "generation"):
        pass
    completed = json.loads(
        (tmp_path / "generation_status.json").read_text(encoding="utf-8")
    )
    assert completed["state"] == "completed"
    assert completed["exit_status"] == 0

    with pytest.raises(RuntimeError):
        with run_e1.track_stage(tmp_path, "moderation"):
            raise RuntimeError("test failure")
    failed = json.loads(
        (tmp_path / "moderation_status.json").read_text(encoding="utf-8")
    )
    assert failed["state"] == "failed"
    assert failed["exit_status"] == 1
    assert failed["error_type"] == "RuntimeError"
