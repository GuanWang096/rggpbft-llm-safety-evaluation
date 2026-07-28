from __future__ import annotations

from typing import Any

from .policy import CanonicalPolicy
from .schema import (
    CanonicalSample,
    JudgeServiceIdentity,
    ParsedDecision,
    canonical_json,
    sha256_file,
    sha256_text,
)


def assistant_response_sha256(sample: CanonicalSample) -> str:
    responses = [
        turn.content for turn in sample.turns if turn.role == "assistant"
    ]
    return sha256_text(canonical_json(responses))


def image_hashes(sample: CanonicalSample) -> list[dict[str, str]]:
    return [
        {"reference": reference, "sha256": sha256_file(path)}
        for reference, path in zip(
            sample.image_references,
            sample.image_paths,
        )
    ]


def build_native_record(
    *,
    sample: CanonicalSample,
    identity: JudgeServiceIdentity,
    policy: CanonicalPolicy,
    generation: dict[str, Any],
    prompt_sha256: str,
    raw_output: str,
    parsed: ParsedDecision,
    latency_ms: float,
    peak_vram_gib: float,
    native_decision: str | None = None,
    input_tokens: int | None = None,
) -> dict[str, Any]:
    subdecision: dict[str, Any] = {
        "unit": "native",
        "prompt_sha256": prompt_sha256,
        "raw_output": raw_output,
        "latency_ms": round(latency_ms, 3),
        "peak_vram_gib": round(peak_vram_gib, 4),
        "parsed": parsed.to_dict(),
    }
    if input_tokens is not None:
        subdecision["input_tokens"] = input_tokens
    if native_decision is not None:
        subdecision["native_decision"] = native_decision

    return {
        "schema": "multijudge-raw-record-v1",
        "sample_id": sample.sample_id,
        "dataset": sample.dataset,
        "split": sample.split,
        "expected_label": sample.expected_label,
        "expected_dimension": sample.expected_dimension,
        "conversation_sha256": sample.conversation_sha256,
        "target_response_sha256": assistant_response_sha256(sample),
        "image_hashes": image_hashes(sample),
        "judge_service": identity.to_dict(),
        "input_mode": "native",
        "policy_id": policy.policy_id,
        "policy_version": policy.version,
        "policy_sha256": policy.sha256,
        "generation": generation,
        "panels": [],
        "subdecisions": [subdecision],
        "decision": parsed.to_dict(),
        "latency_ms_total": round(latency_ms, 3),
        "peak_vram_gib_max": round(peak_vram_gib, 4),
    }
