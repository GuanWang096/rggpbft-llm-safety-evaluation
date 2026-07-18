import json

import pytest

from analyze_e1_results import (
    analyze_runs,
    load_jsonl_unique,
    summarize_guard_metrics,
    wilson_interval,
)


def write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def generation(sample_id, tokens, response="response"):
    return {
        "sample_id": sample_id,
        "dataset": "dataset-a",
        "variant": "variant-a",
        "risk_category": "unsafe",
        "expected_input_safe": False,
        "output_tokens": tokens,
        "response": response,
    }


def moderation(sample_id, safety="Safe", refusal="No"):
    return {
        "sample_id": sample_id,
        "dataset": "dataset-a",
        "variant": "variant-a",
        "risk_category": "unsafe",
        "expected_input_safe": False,
        "safety": safety,
        "refusal": refusal,
    }


def make_run(root, generations, moderations, max_new_tokens):
    root.mkdir()
    write_jsonl(root / "generation.jsonl", generations)
    write_jsonl(root / "moderation.jsonl", moderations)
    (root / "config.json").write_text(
        json.dumps(
            {
                "max_new_tokens": max_new_tokens,
                "sample_ids": [row["sample_id"] for row in generations],
            }
        ),
        encoding="utf-8",
    )


def test_load_jsonl_unique_rejects_duplicate_sample_ids(tmp_path):
    path = tmp_path / "rows.jsonl"
    write_jsonl(path, [generation("same", 10), generation("same", 20)])

    with pytest.raises(ValueError, match="duplicate"):
        load_jsonl_unique(path)


def test_wilson_interval_handles_known_fraction_and_empty_denominator():
    low, high = wilson_interval(35, 1504)

    assert low == pytest.approx(0.01678, abs=1e-5)
    assert high == pytest.approx(0.03219, abs=1e-5)
    assert wilson_interval(0, 0) == (None, None)


def test_guard_metrics_use_non_refusal_proxy_name():
    rows = [
        moderation("unsafe-attack", safety="Unsafe", refusal="No"),
        moderation("unsafe-refusal", safety="Safe", refusal="Yes"),
        {
            **moderation("safe-useful", safety="Safe", refusal="No"),
            "expected_input_safe": True,
            "risk_category": "safe",
        },
    ]

    metrics = summarize_guard_metrics(rows)

    assert metrics["attack_success_rate"] == 0.5
    assert metrics["unsafe_input_refusal_rate"] == 0.5
    assert metrics["guard_safe_non_refusal_rate"] == 1.0
    assert "safe_input_utility_rate" not in metrics


def test_analyze_runs_pairs_same_ids_and_reports_limit_sensitivity(tmp_path):
    base = tmp_path / "base"
    final = tmp_path / "final"
    base_generations = [generation("a", 100), generation("b", 512, "short")]
    final_generations = [generation("a", 100), generation("b", 2048, "long")]
    base_moderations = [moderation("a"), moderation("b", "Safe", "Yes")]
    final_moderations = [moderation("a"), moderation("b", "Unsafe", "No")]
    make_run(base, base_generations, base_moderations, 512)
    make_run(final, final_generations, final_moderations, 2048)

    result = analyze_runs(base, final)

    assert result["integrity"]["sample_count"] == 2
    assert result["generation_lengths"]["base_limit_hit_count"] == 1
    assert result["generation_lengths"]["final_limit_hit_count"] == 1
    assert result["paired_changes"][0]["sample_id"] == "b"
    assert result["paired_changes"][0]["decision_changed"] is True
    assert result["sensitivity"]["excluded_limit_hit_count"] == 1
