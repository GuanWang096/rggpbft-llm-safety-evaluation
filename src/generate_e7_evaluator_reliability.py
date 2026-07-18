#!/usr/bin/env python3
"""Construct controlled evaluator-reliability inputs from frozen E1 decisions."""

import argparse
import hashlib
import json
import pathlib
import platform
import time


DEFAULT_RELIABILITY_LEVELS = tuple(round(0.98 - 0.02 * i, 2) for i in range(16))
REFERENCE_SEMANTICS = "frozen_guard_operational_proxy"


def reference_decision(record):
    """Return binary unsafe-output and refusal decisions from frozen Guard output."""
    safety = str(record.get("safety", "")).strip().lower()
    refusal = str(record.get("refusal", "")).strip().lower()
    unsafe_output = int(safety in {"unsafe", "controversial"})
    refused = int(refusal == "yes")
    return unsafe_output, refused


def balanced_agreement(reference, report):
    if len(reference) != len(report) or not reference:
        raise ValueError("reference and report must be non-empty and equally sized")
    recalls = []
    for label in (0, 1):
        indices = [i for i, value in enumerate(reference) if value == label]
        if indices:
            recalls.append(sum(report[i] == label for i in indices) / len(indices))
    return sum(recalls) / len(recalls)


def _hash_order(seed_base, node_id, dimension, records, label):
    candidates = []
    for index, record in enumerate(records):
        if reference_decision(record)[dimension] != label:
            continue
        material = (
            f"e7-evaluator-proxy-v1|{seed_base}|node={node_id}|dimension={dimension}|"
            f"sample={record['sample_id']}|index={index}"
        )
        candidates.append((hashlib.sha256(material.encode("utf-8")).digest(), index))
    return [index for _, index in sorted(candidates)]


def _controlled_report(records, node_id, target_reliability, seed_base, dimension):
    reference = [reference_decision(record)[dimension] for record in records]
    report = list(reference)
    for label in (0, 1):
        ordered = _hash_order(seed_base, node_id, dimension, records, label)
        error_count = round((1.0 - target_reliability) * len(ordered))
        for index in ordered[:error_count]:
            report[index] = 1 - report[index]
    return reference, report


def _node_profile_order(evaluator_count, seed_base):
    def key(node_id):
        material = f"e7-profile-order-v1|{seed_base}|node={node_id}"
        return hashlib.sha256(material.encode("ascii")).digest()

    return sorted(range(evaluator_count), key=key)


def _rank(values, reverse=False):
    order = sorted(range(len(values)), key=lambda i: ((-values[i]) if reverse else values[i], i))
    ranks = [0] * len(values)
    for rank, index in enumerate(order, start=1):
        ranks[index] = rank
    return ranks


def _spearman(left, right):
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Spearman correlation requires equally sized vectors with at least two values")
    left_rank = _rank(left)
    right_rank = _rank(right)
    squared = sum((a - b) ** 2 for a, b in zip(left_rank, right_rank))
    n = len(left)
    return 1.0 - (6.0 * squared) / (n * (n * n - 1))


def build_controlled_evaluator_artifacts(
    records,
    evaluator_count=16,
    seed_base=20260705,
    reliability_levels=None,
    top_k=4,
):
    if not records:
        raise ValueError("at least one E1 record is required")
    if len({record["sample_id"] for record in records}) != len(records):
        # Duplicate IDs would make an evidence manifest ambiguous even though index binding is deterministic.
        raise ValueError("E1 sample_id values must be unique")

    levels = tuple(reliability_levels or DEFAULT_RELIABILITY_LEVELS)
    if len(levels) < evaluator_count:
        raise ValueError("not enough reliability levels for evaluator_count")
    if any(not 0.5 <= value <= 1.0 for value in levels[:evaluator_count]):
        raise ValueError("controlled reliability levels must be in [0.5, 1.0]")

    node_order = _node_profile_order(evaluator_count, seed_base)
    target_by_node = {
        node_id: levels[rank]
        for rank, node_id in enumerate(node_order)
    }
    profiles = []
    reports = []
    for node_id in range(evaluator_count):
        target = target_by_node[node_id]
        safety_ref, safety_report = _controlled_report(
            records, node_id, target, seed_base, dimension=0
        )
        refusal_ref, refusal_report = _controlled_report(
            records, node_id, target, seed_base, dimension=1
        )
        safety_ba = balanced_agreement(safety_ref, safety_report)
        refusal_ba = balanced_agreement(refusal_ref, refusal_report)
        score = (safety_ba + refusal_ba) / 2.0
        score_ppm = round(1_000_000 * score)
        profile_reports = []
        for index, record in enumerate(records):
            item = {
                "node_id": node_id,
                "evaluator_id": f"eval-{node_id:02d}",
                "sample_id": record["sample_id"],
                "reference_unsafe_output": safety_ref[index],
                "reported_unsafe_output": safety_report[index],
                "reference_refusal": refusal_ref[index],
                "reported_refusal": refusal_report[index],
            }
            reports.append(item)
            profile_reports.append(item)
        report_digest = hashlib.sha256(
            "\n".join(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in profile_reports).encode("utf-8")
        ).hexdigest()
        profiles.append({
            "node_id": node_id,
            "evaluator_id": f"eval-{node_id:02d}",
            "target_reliability": target,
            "sample_count": len(records),
            "safety_balanced_agreement": safety_ba,
            "refusal_balanced_agreement": refusal_ba,
            "score": score,
            "score_ppm": score_ppm,
            "report_sha256": report_digest,
        })

    targets = [profile["target_reliability"] for profile in profiles]
    scores = [profile["score"] for profile in profiles]
    expected_top = {
        profile["node_id"]
        for profile in sorted(profiles, key=lambda p: (-p["target_reliability"], p["node_id"]))[:top_k]
    }
    realized_top = {
        profile["node_id"]
        for profile in sorted(profiles, key=lambda p: (-p["score_ppm"], p["node_id"]))[:top_k]
    }
    safety_distribution = {str(label): 0 for label in (0, 1)}
    refusal_distribution = {str(label): 0 for label in (0, 1)}
    for record in records:
        safety, refusal = reference_decision(record)
        safety_distribution[str(safety)] += 1
        refusal_distribution[str(refusal)] += 1

    return {
        "schema": "zte-sci-e7-evaluator-reliability-v1",
        "seed_base": seed_base,
        "reference_semantics": REFERENCE_SEMANTICS,
        "reference_is_human_ground_truth": False,
        "evaluator_count": evaluator_count,
        "sample_count_per_evaluator": len(records),
        "reference_distribution": {
            "unsafe_output": safety_distribution,
            "refusal": refusal_distribution,
        },
        "profiles": profiles,
        "reports": reports,
        "metrics": {
            "spearman_target_vs_score": _spearman(targets, scores),
            "top_k": top_k,
            "top_k_precision": len(expected_top & realized_top) / top_k,
            "expected_top_k_nodes": sorted(expected_top),
            "realized_top_k_nodes": sorted(realized_top),
        },
    }


def load_e1_records(e1_dir):
    e1_dir = pathlib.Path(e1_dir)
    generation = [
        json.loads(line)
        for line in (e1_dir / "generation.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    moderation = [
        json.loads(line)
        for line in (e1_dir / "moderation.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    moderation_by_id = {record["sample_id"]: record for record in moderation}
    records = []
    for generated in generation:
        sample_id = generated["sample_id"]
        if sample_id not in moderation_by_id:
            continue
        moderated = moderation_by_id[sample_id]
        records.append({
            "sample_id": sample_id,
            "dataset": generated.get("dataset"),
            "expected_input_safe": generated.get("expected_input_safe"),
            "safety": moderated.get("safety", ""),
            "refusal": moderated.get("refusal", ""),
        })
    return records


def write_artifacts(artifacts, output_dir, e1_dir):
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    profiles = {str(profile["node_id"]): profile for profile in artifacts["profiles"]}
    (output_dir / "evaluator_profiles.json").write_text(
        json.dumps({**{k: v for k, v in artifacts.items() if k != "reports"}, "profiles": profiles}, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "evaluator_reports.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for report in artifacts["reports"]:
            handle.write(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n")
    score_payload = {
        "schema": artifacts["schema"],
        "reference_semantics": artifacts["reference_semantics"],
        "reference_is_human_ground_truth": artifacts["reference_is_human_ground_truth"],
        "scores_ppm": {
            str(profile["node_id"]): profile["score_ppm"]
            for profile in artifacts["profiles"]
        },
        "reputation_order": [
            profile["node_id"]
            for profile in sorted(artifacts["profiles"], key=lambda p: (-p["score_ppm"], p["node_id"]))
        ],
    }
    (output_dir / "scores.json").write_text(json.dumps(score_payload, indent=2), encoding="utf-8")
    environment = {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": platform.python_version(),
        "e1_dir": str(pathlib.Path(e1_dir).resolve()),
    }
    (output_dir / "environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
    files = sorted(output_dir.glob("*.json*"))
    checksums = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in files]
    (output_dir / "checksums.sha256").write_text("\n".join(checksums) + "\n", encoding="ascii")


def main():
    parser = argparse.ArgumentParser(description="Prepare controlled evaluator-reliability inputs for E7 v2")
    parser.add_argument("--e1-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--seed-base", type=int, default=20260705)
    args = parser.parse_args()
    records = load_e1_records(args.e1_dir)
    artifacts = build_controlled_evaluator_artifacts(records, seed_base=args.seed_base)
    write_artifacts(artifacts, args.output_dir, args.e1_dir)
    print(json.dumps({
        "output_dir": str(args.output_dir),
        "records": len(records),
        "evaluators": artifacts["evaluator_count"],
        "spearman": artifacts["metrics"]["spearman_target_vs_score"],
        "top_k_precision": artifacts["metrics"]["top_k_precision"],
    }, indent=2))


if __name__ == "__main__":
    main()
