import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from analyze_e1_results import (
    DEFAULT_FINAL,
    canonical_json_bytes,
    load_jsonl_unique,
    sha256_file,
)


DEFAULT_RESULTS = Path(__file__).resolve().parents[1] / "results"


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique_index(rows, label):
    indexed = {}
    for row in rows:
        sample_id = row["sample_id"]
        if sample_id in indexed:
            raise ValueError(f"duplicate {label} sample_id: {sample_id}")
        indexed[sample_id] = row
    return indexed


def build_evidence_records(generations, moderations, sample_ids):
    generation_by_id = _unique_index(generations, "generation")
    moderation_by_id = _unique_index(moderations, "moderation")
    configured = list(sample_ids)
    if len(configured) != len(set(configured)):
        raise ValueError("duplicate configured sample_id")
    if set(configured) != set(generation_by_id):
        raise ValueError("configured sample IDs do not match generation records")
    missing_moderation = sorted(set(configured) - set(moderation_by_id))
    extra_moderation = sorted(set(moderation_by_id) - set(configured))
    if missing_moderation or extra_moderation:
        raise ValueError(
            "moderation IDs do not match configured samples; "
            f"missing={missing_moderation}, extra={extra_moderation}"
        )

    records = []
    for sample_id in configured:
        generation = generation_by_id[sample_id]
        moderation = moderation_by_id[sample_id]
        records.append(
            {
                "sample_id": sample_id,
                "dataset": generation["dataset"],
                "variant": generation["variant"],
                "risk_category": generation["risk_category"],
                "expected_input_safe": generation["expected_input_safe"],
                "image_sha256": generation["image_sha256"],
                "prompt": generation["prompt"],
                "prompt_sha256": sha256_text(generation["prompt"]),
                "response": generation["response"],
                "response_sha256": sha256_text(generation["response"]),
                "model_id": generation["model_id"],
                "output_tokens": generation["output_tokens"],
                "safety": moderation["safety"],
                "refusal": moderation.get("refusal"),
                "categories": moderation.get("categories", []),
                "guard_model_id": moderation["guard_model_id"],
            }
        )
    return records


def chunk_records(records, batch_size):
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    records = list(records)
    return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]


def _rank(seed, sample_id):
    material = f"zte-sci-stratified-v1|{seed}|{sample_id}".encode("utf-8")
    return hashlib.sha256(material).digest()


def stratified_sample(records, count, seed):
    records = list(records)
    if count < 0 or count > len(records):
        raise ValueError("sample count is outside the record range")
    strata = defaultdict(list)
    for record in records:
        key = (
            record["dataset"],
            record["variant"],
            record["risk_category"],
            bool(record["expected_input_safe"]),
        )
        strata[key].append(record)
    for values in strata.values():
        values.sort(key=lambda row: (_rank(seed, row["sample_id"]), row["sample_id"]))

    selected = []
    keys = sorted(strata, key=lambda key: tuple(map(str, key)))
    while len(selected) < count:
        progressed = False
        for key in keys:
            if strata[key] and len(selected) < count:
                selected.append(strata[key].pop(0))
                progressed = True
        if not progressed:
            raise ValueError("unable to complete stratified selection")
    selected_ids = {row["sample_id"] for row in selected}
    return [row for row in records if row["sample_id"] in selected_ids]


def public_record(record):
    return {
        key: value
        for key, value in record.items()
        if key not in {"prompt", "response"}
    }


def write_batch_set(root, name, records, batch_size):
    target = root / name
    target.mkdir(parents=True, exist_ok=False)
    manifest_batches = []
    for index, batch in enumerate(chunk_records(records, batch_size)):
        filename = f"batch-{index:03d}.json"
        payload = {
            "schema": "zte-sci-e1-record-batch-v1",
            "batch_index": index,
            "record_count": len(batch),
            "records": batch,
        }
        path = target / filename
        path.write_bytes(canonical_json_bytes(payload) + b"\n")
        manifest_batches.append(
            {
                "batch_index": index,
                "filename": filename,
                "record_count": len(batch),
                "first_sample_id": batch[0]["sample_id"],
                "last_sample_id": batch[-1]["sample_id"],
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "records": [public_record(record) for record in batch],
            }
        )
    manifest = {
        "schema": "zte-sci-e1-batch-manifest-v1",
        "name": name,
        "batch_size": batch_size,
        "record_count": len(records),
        "batch_count": len(manifest_batches),
        "batches": manifest_batches,
    }
    (target / "manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    return manifest


def write_results(final_run, output_dir, seed=20260705):
    final_run = Path(final_run)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    generations = load_jsonl_unique(final_run / "generation.jsonl")
    moderations = load_jsonl_unique(final_run / "moderation.jsonl")
    config = json.loads((final_run / "config.json").read_text(encoding="utf-8"))
    records = build_evidence_records(generations, moderations, config["sample_ids"])
    full_manifest = write_batch_set(output_dir, "full-b64", records, 64)
    selected = stratified_sample(records, 256, seed)
    granular = {}
    for size in (1, 16, 64, 256):
        granular[str(size)] = write_batch_set(
            output_dir, f"stratified-256-b{size}", selected, size
        )
    summary = {
        "stage": "B2-input",
        "source_run": str(final_run.resolve()),
        "source_config_sha256": sha256_file(final_run / "config.json"),
        "source_generation_sha256": sha256_file(final_run / "generation.jsonl"),
        "source_moderation_sha256": sha256_file(final_run / "moderation.jsonl"),
        "seed": seed,
        "record_count": len(records),
        "full": {
            "batch_size": full_manifest["batch_size"],
            "batch_count": full_manifest["batch_count"],
            "last_batch_count": full_manifest["batches"][-1]["record_count"],
        },
        "granularity": {
            size: {
                "batch_count": manifest["batch_count"],
                "record_count": manifest["record_count"],
            }
            for size, manifest in granular.items()
        },
    }
    (output_dir / "summary.json").write_bytes(canonical_json_bytes(summary) + b"\n")
    (output_dir / "status.json").write_bytes(
        canonical_json_bytes({"stage": "B2-input", "state": "completed"}) + b"\n"
    )
    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    lines = []
    for path in files:
        relative = path.relative_to(output_dir).as_posix()
        lines.append(f"{sha256_file(path)}  {relative}\n")
    (output_dir / "checksums.sha256").write_text("".join(lines), encoding="ascii")


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--final-run", type=Path, default=DEFAULT_FINAL)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--seed", type=int, default=20260705)
    return parser


def main():
    args = build_parser().parse_args()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = DEFAULT_RESULTS / f"b2-input-{stamp}"
    write_results(args.final_run, output_dir, args.seed)
    print(output_dir)


if __name__ == "__main__":
    main()
