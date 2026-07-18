import json
from pathlib import Path

import pytest

from e1_pipeline.topup import (
    merge_generation_records,
    select_limit_hits,
    validate_model_provenance,
)
import run_e1_topup


def record(sample_id, output_tokens, response):
    return {
        "sample_id": sample_id,
        "output_tokens": output_tokens,
        "response": response,
    }


def test_select_limit_hits_only_returns_records_that_reached_the_base_limit():
    rows = [record("a", 511, "done"), record("b", 512, "cut")]

    selected = select_limit_hits(rows, base_limit=512)

    assert [row["sample_id"] for row in selected] == ["b"]


def test_merge_reuses_natural_completions_and_replaces_only_limit_hits():
    base = [record("a", 300, "done"), record("b", 512, "cut")]
    replacements = [record("b", 900, "complete")]

    merged = merge_generation_records(
        base,
        replacements,
        base_limit=512,
        topup_limit=2048,
    )

    assert [row["sample_id"] for row in merged] == ["a", "b"]
    assert merged[0]["response"] == "done"
    assert merged[0]["generation_budget"] == 512
    assert merged[0]["generation_strategy"] == "reused_natural_completion"
    assert merged[1]["response"] == "complete"
    assert merged[1]["generation_budget"] == 2048
    assert merged[1]["generation_strategy"] == "limit_hit_topup"


def test_merge_rejects_missing_replacement_for_a_limit_hit():
    base = [record("a", 512, "cut")]

    with pytest.raises(ValueError, match="replacement IDs"):
        merge_generation_records(
            base,
            [],
            base_limit=512,
            topup_limit=2048,
        )


def test_merge_rejects_replacement_for_a_natural_completion():
    base = [record("a", 200, "done")]
    replacements = [record("a", 300, "different")]

    with pytest.raises(ValueError, match="replacement IDs"):
        merge_generation_records(
            base,
            replacements,
            base_limit=512,
            topup_limit=2048,
        )


def test_merge_rejects_changed_prompt_or_image_for_a_topup_record():
    base = [
        {
            **record("a", 512, "cut"),
            "prompt": "original",
            "image_sha256": "image-a",
        }
    ]
    replacement = [
        {
            **record("a", 800, "complete"),
            "prompt": "changed",
            "image_sha256": "image-a",
        }
    ]

    with pytest.raises(ValueError, match="prompt"):
        merge_generation_records(
            base,
            replacement,
            base_limit=512,
            topup_limit=2048,
        )


def test_topup_launcher_uses_separate_result_directory_and_2048_budget():
    script = (Path(__file__).parents[1] / "run_e1_topup.sh").read_text(
        encoding="utf-8"
    )

    assert "BASE_RUN=/root/result/full-qwen3vl4b-512" in script
    assert "RUN_DIR=/root/result/full-qwen3vl4b-2048-topup" in script
    assert "--max-new-tokens 2048" in script


def test_topup_command_defaults_to_the_archived_base_and_new_result_directory():
    args = run_e1_topup.build_parser().parse_args([])

    assert str(args.base_run) == "/root/result/full-qwen3vl4b-512"
    assert str(args.run_dir) == "/root/result/full-qwen3vl4b-2048-topup"
    assert args.base_limit == 512
    assert args.max_new_tokens == 2048
    assert args.max_limit_hit_rate == 0.01


def test_generation_status_is_idempotent_after_topup_completion(tmp_path):
    (tmp_path / "topup_generation_status.json").write_text(
        json.dumps(
            {
                "state": "completed",
                "started_at_unix": 10,
                "ended_at_unix": 20,
                "exit_status": 0,
                "pid": 123,
            }
        ),
        encoding="utf-8",
    )

    run_e1_topup._write_generation_status(tmp_path, 447)
    run_e1_topup._write_generation_status(tmp_path, 447)

    status = json.loads(
        (tmp_path / "generation_status.json").read_text(encoding="utf-8")
    )
    assert status["started_at_unix"] == 10
    assert status["ended_at_unix"] == 20
    assert status["pid"] == 123


def test_topup_rejects_a_different_model_manifest():
    with pytest.raises(ValueError, match="model manifest"):
        validate_model_provenance(
            {"model_manifest_sha256": "base"},
            {"model_manifest_sha256": "different"},
        )
