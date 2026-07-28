from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any

from mj5_common import (
    EXTENDED_COMMITTEE,
    ONE_PPM,
    PRIMARY_COMMITTEE,
    canonical_json_bytes,
    generate_validator_order,
    judge_private_key,
    likelihood_score,
    public_key_fingerprint,
    public_key_hex,
    read_json,
    read_jsonl,
    validator_private_key,
)


def load_aligned_records(
    formal_test: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, dict[str, Any]]]]:
    by_model: dict[str, dict[str, dict[str, Any]]] = {}
    for model in EXTENDED_COMMITTEE:
        rows = read_jsonl(formal_test / model / "judgments.jsonl")
        by_model[model] = {row["sample_id"]: row for row in rows}
    sample_ids = set(by_model[EXTENDED_COMMITTEE[0]])
    for model in EXTENDED_COMMITTEE[1:]:
        if set(by_model[model]) != sample_ids:
            raise RuntimeError(f"sample alignment mismatch for {model}")
    reference = by_model[EXTENDED_COMMITTEE[0]]
    return reference, by_model


def reliability_registration(
    freeze: dict[str, Any],
    model: str,
) -> dict[str, int]:
    counts = freeze["class_conditional_reliability"][model]["counts"]
    return {
        "unsafeCorrectMicro": (int(counts["tp"]) + 1) * ONE_PPM,
        "unsafeIncorrectMicro": (int(counts["fn"]) + 1) * ONE_PPM,
        "safeCorrectMicro": (int(counts["tn"]) + 1) * ONE_PPM,
        "safeIncorrectMicro": (int(counts["fp"]) + 1) * ONE_PPM,
    }


def reduced_raw_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": record["schema"],
        "sample_id": record["sample_id"],
        "dataset": record["dataset"],
        "split": record["split"],
        "expected_label": record["expected_label"],
        "expected_dimension": record.get("expected_dimension"),
        "conversation_sha256": record["conversation_sha256"],
        "target_response_sha256": record["target_response_sha256"],
        "image_hashes": record["image_hashes"],
        "judge_service": record["judge_service"],
        "policy_id": record["policy_id"],
        "policy_version": record["policy_version"],
        "policy_sha256": record["policy_sha256"],
        "subdecisions": [
            {
                "unit": item["unit"],
                "message_sha256": item.get(
                    "message_sha256", item.get("prompt_sha256")
                ),
                "raw_output": item["raw_output"],
                "parsed": item["parsed"],
            }
            for item in record["subdecisions"]
        ],
        "decision": record["decision"],
    }


def build_committee_workload(
    committee: tuple[str, ...],
    reference: dict[str, dict[str, Any]],
    by_model: dict[str, dict[str, dict[str, Any]]],
    freeze: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    committee_name = f"j{len(committee)}"
    bundle_dir = output_dir / "bundles" / committee_name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    reliability = {
        model: (
            float(freeze["class_conditional_reliability"][model]["g_unsafe"]),
            float(freeze["class_conditional_reliability"][model]["g_safe"]),
        )
        for model in committee
    }
    prior = float(freeze["class_prior_unsafe"])
    records = []
    for index, sample_id in enumerate(sorted(reference)):
        predictions = {
            model: by_model[model][sample_id]["decision"]["label"]
            for model in committee
        }
        posterior = likelihood_score(predictions, reliability, prior)
        bundle = {
            "schema": "mj5-real-output-evidence-v1",
            "committee": list(committee),
            "sample_id": sample_id,
            "expected_label": reference[sample_id]["expected_label"],
            "judge_records": {
                model: reduced_raw_record(by_model[model][sample_id])
                for model in committee
            },
            "aggregation": {
                "method": "class_conditional_reliability_likelihood",
                "prior_unsafe": prior,
                "frozen_reliability": {
                    model: {
                        "g_unsafe": reliability[model][0],
                        "g_safe": reliability[model][1],
                    }
                    for model in committee
                },
                "posterior_unsafe": posterior,
                "provisional_label": "unsafe" if posterior >= 0.5 else "safe",
            },
        }
        payload = canonical_json_bytes(bundle)
        filename = f"{index:04d}.json"
        (bundle_dir / filename).write_bytes(payload)
        records.append(
            {
                "index": index,
                "sample_id": sample_id,
                "expected_label": reference[sample_id]["expected_label"],
                "expected_dimension": reference[sample_id].get(
                    "expected_dimension"
                ),
                "bundle_path": f"bundles/{committee_name}/{filename}",
                "bundle_bytes": len(payload),
                "evidence_sha256": hashlib.sha256(payload).hexdigest(),
                "predictions": predictions,
                "posterior_unsafe_ppm": round(posterior * ONE_PPM),
                "provisional_label": bundle["aggregation"]["provisional_label"],
            }
        )
    manifest = {
        "schema": "mj5-workload-manifest-v1",
        "committee": list(committee),
        "judge_count": len(committee),
        "sample_count": len(records),
        "execution_batch_size": 64,
        "execution_batch_count": (len(records) + 63) // 64,
        "records": records,
    }
    (output_dir / f"{committee_name}_manifest.json").write_bytes(
        canonical_json_bytes(manifest)
    )
    return manifest


def select_stratified_records(
    manifest: dict[str, Any],
    target_count: int,
) -> list[dict[str, Any]]:
    records = manifest["records"]
    if target_count > len(records):
        raise ValueError("selection target exceeds manifest size")
    strata: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for record in records:
        key = (
            record["expected_label"],
            record.get("expected_dimension") or "none",
        )
        strata[key].append(record)
    for key, values in strata.items():
        values.sort(
            key=lambda record: hashlib.sha256(
                f"mj5-system-selection-v1|{key}|{record['sample_id']}".encode()
            ).digest()
        )

    exact = {
        key: target_count * len(values) / len(records)
        for key, values in strata.items()
    }
    quotas = {key: int(value) for key, value in exact.items()}
    remaining = target_count - sum(quotas.values())
    for key in sorted(
        strata,
        key=lambda item: (-(exact[item] - quotas[item]), item),
    )[:remaining]:
        quotas[key] += 1
    selected = [
        record
        for key, values in strata.items()
        for record in values[: quotas[key]]
    ]
    selected.sort(key=lambda record: record["index"])
    if len(selected) != target_count:
        raise RuntimeError("stratified selection produced the wrong size")
    return selected


def run(args: argparse.Namespace) -> None:
    formal_test = args.formal_test.resolve()
    freeze = read_json(args.freeze.resolve())
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    reference, by_model = load_aligned_records(formal_test)
    if len(reference) != 330:
        raise RuntimeError(f"expected 330 formal test records, found {len(reference)}")

    judge_keys = {}
    registrations = {}
    for model in EXTENDED_COMMITTEE:
        identity = freeze["judge_service_identities"][model]
        private_key = judge_private_key(identity["canonical_id"])
        judge_keys[model] = {
            "private_key_hex": private_key.private_bytes_raw().hex(),
            "public_key_hex": public_key_hex(private_key),
            "public_key_fingerprint": public_key_fingerprint(private_key),
            "purpose": "deterministic replay-only application signing key",
        }
        registrations[model] = {
            "judgeId": model,
            "organization": identity["organization"],
            "modelId": identity["model_id"],
            "modelRevision": identity["model_revision"],
            "policySha256": identity["policy_sha256"],
            "adapterVersion": identity["adapter_version"],
            "publicKeyHex": public_key_hex(private_key),
            **reliability_registration(freeze, model),
        }

    validator_order, validator_ppm = generate_validator_order(16)
    validators = {}
    for node_id in range(16):
        private_key = validator_private_key(node_id)
        validators[f"validator-{node_id:02d}"] = {
            "node_id": node_id,
            "private_key_hex": private_key.private_bytes_raw().hex(),
            "public_key_hex": public_key_hex(private_key),
            "public_key_fingerprint": public_key_fingerprint(private_key),
            "reliability_ppm": validator_ppm[node_id],
            "version": 1,
            "purpose": "deterministic RGG-PBFT test validator key",
        }

    keys = {
        "schema": "mj5-replay-key-material-v1",
        "security_scope": "public deterministic experiment keys; never production keys",
        "judge_keys": judge_keys,
        "validator_keys": validators,
    }
    (output_dir / "replay_keys.json").write_bytes(canonical_json_bytes(keys))
    registrations_payload = {
        "schema": "mj5-registration-v1",
        "judge_registrations": registrations,
        "validator_order": validator_order,
        "validator_registrations": [
            {
                "validatorId": f"validator-{node_id:02d}",
                "publicKeyHex": validators[f"validator-{node_id:02d}"][
                    "public_key_hex"
                ],
                "reliabilityPpm": validator_ppm[node_id],
                "version": 1,
            }
            for node_id in range(16)
        ],
    }
    (output_dir / "registrations.json").write_bytes(
        canonical_json_bytes(registrations_payload)
    )

    manifests = [
        build_committee_workload(
            committee, reference, by_model, freeze, output_dir
        )
        for committee in (PRIMARY_COMMITTEE, EXTENDED_COMMITTEE)
    ]
    selected = select_stratified_records(manifests[0], args.system_sample_count)
    selected_ids = [record["sample_id"] for record in selected]
    if {
        record["sample_id"]
        for record in manifests[1]["records"]
        if record["sample_id"] in set(selected_ids)
    } != set(selected_ids):
        raise RuntimeError("J=3 and J=4 system selections are not aligned")
    selection = {
        "schema": "mj5-system-selection-v1",
        "source_sample_count": len(reference),
        "selected_sample_count": len(selected_ids),
        "selection_rule": "proportional label-by-risk-dimension strata with deterministic SHA-256 ranking",
        "sample_ids": selected_ids,
        "label_counts": dict(
            collections.Counter(record["expected_label"] for record in selected)
        ),
        "dimension_counts": dict(
            collections.Counter(
                record.get("expected_dimension") or "none" for record in selected
            )
        ),
    }
    (output_dir / "system_selection.json").write_bytes(
        canonical_json_bytes(selection)
    )
    matrix = {
        "schema": "mj5-formal-matrix-v1",
        "seed_base": 20260705,
        "execution_batch_size": 64,
        "entries": [
            {
                "run_id": f"mj5-j{judge_count}-c{concurrency}-r{repeat}",
                "judge_count": judge_count,
                "concurrency": concurrency,
                "repeat": repeat,
                "sample_count": len(selected_ids),
                "execution_batch_count": (len(selected_ids) + 63) // 64,
            }
            for judge_count in (3, 4)
            for concurrency in (1, 8, 16)
            for repeat in range(1, 4)
        ],
    }
    (output_dir / "matrix.json").write_bytes(canonical_json_bytes(matrix))
    summary = {
        "schema": "mj5-workload-build-summary-v1",
        "sample_count": len(reference),
        "committees": [manifest["committee"] for manifest in manifests],
        "bundle_count": sum(manifest["sample_count"] for manifest in manifests),
        "matrix_run_count": len(matrix["entries"]),
        "validator_order": validator_order,
    }
    (output_dir / "summary.json").write_bytes(canonical_json_bytes(summary))
    print(json.dumps(summary, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    default_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--formal-test",
        type=Path,
        default=default_root / "results/multijudge/formal/test",
    )
    parser.add_argument(
        "--freeze",
        type=Path,
        default=default_root / "results/multijudge/formal/validation_frozen.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_root / "results/cross_layer/workload",
    )
    parser.add_argument("--system-sample-count", type=int, default=96)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
