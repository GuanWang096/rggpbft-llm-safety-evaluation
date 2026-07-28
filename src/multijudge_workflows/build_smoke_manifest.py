from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SEED_MATERIAL = "zte-sci-v15|20260724|MJ0|MMDS|adapter-smoke|0"


def stable_hash(value: str) -> str:
    return hashlib.sha256(f"{SEED_MATERIAL}|{value}".encode("utf-8")).hexdigest()


def canonical_label(value: Any) -> str:
    label = str(value).strip().lower()
    if label not in {"safe", "unsafe"}:
        raise ValueError(f"Unsupported binary label: {value!r}")
    return label


def image_references(record: dict[str, Any]) -> list[str]:
    references: list[str] = []
    for turn in record.get("conversations") or []:
        images = turn.get("image") or []
        if isinstance(images, str):
            images = [images]
        references.extend(str(item) for item in images)
    return sorted(set(references))


def conversation_hash(record: dict[str, Any]) -> str:
    payload = json.dumps(
        record.get("conversations") or [],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            split = str(record.get("set", "")).strip().lower()
            raw_id = str(record.get("id"))
            records.append(
                {
                    "record": record,
                    "line_number": line_number,
                    "split": split,
                    "raw_id": raw_id,
                    "sample_id": f"mmds:{split}:{line_number}:{raw_id}",
                    "images": image_references(record),
                    "conversation_sha256": conversation_hash(record),
                }
            )
    return records


def proportional_quotas(counts: Counter[str], total: int) -> dict[str, int]:
    available = sum(counts.values())
    if total > available:
        raise ValueError(f"Requested {total} records from only {available} candidates")
    raw = {key: total * value / available for key, value in counts.items()}
    quotas = {key: int(value) for key, value in raw.items()}
    remaining = total - sum(quotas.values())
    order = sorted(
        counts,
        key=lambda key: (-(raw[key] - quotas[key]), stable_hash(key)),
    )
    for key in order[:remaining]:
        quotas[key] += 1
    return quotas


def select_without_reused_images(
    candidates: list[dict[str, Any]],
    quota: int,
    used_images: set[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda value: stable_hash(value["sample_id"])):
        images = set(item["images"])
        if images & used_images:
            continue
        selected.append(item)
        used_images.update(images)
        if len(selected) == quota:
            return selected
    raise RuntimeError(
        f"Could not select {quota} image-disjoint records; selected {len(selected)}"
    )


def build_manifest(records: list[dict[str, Any]], size: int) -> dict[str, Any]:
    test_images = {
        image
        for item in records
        if item["split"] == "test"
        for image in item["images"]
    }
    validation_images = {
        image
        for item in records
        if item["split"] == "val"
        for image in item["images"]
    }
    excluded_images = test_images | validation_images

    candidates: list[dict[str, Any]] = []
    for item in records:
        if item["split"] != "train":
            continue
        label = str(item["record"].get("assistant_rating", "")).strip().lower()
        if label not in {"safe", "unsafe"}:
            continue
        if not item["images"]:
            continue
        if set(item["images"]) & excluded_images:
            continue
        candidates.append(item)

    test_binary = [
        item
        for item in records
        if item["split"] == "test"
        and str(item["record"].get("assistant_rating", "")).strip().lower()
        in {"safe", "unsafe"}
    ]
    test_label_counts = Counter(
        canonical_label(item["record"]["assistant_rating"]) for item in test_binary
    )
    label_quotas = proportional_quotas(test_label_counts, size)

    selected: list[dict[str, Any]] = []
    used_images: set[str] = set()
    for label in ("unsafe", "safe"):
        label_candidates = [
            item
            for item in candidates
            if canonical_label(item["record"]["assistant_rating"]) == label
        ]
        if label == "safe":
            selected.extend(
                select_without_reused_images(
                    label_candidates, label_quotas[label], used_images
                )
            )
            continue

        by_dimension: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in label_candidates:
            dimension = (
                str(item["record"].get("assistant_dimension", "")).strip()
                or "unspecified"
            )
            by_dimension[dimension].append(item)
        dimension_quotas = proportional_quotas(
            Counter({key: len(value) for key, value in by_dimension.items()}),
            label_quotas[label],
        )
        for dimension in sorted(by_dimension):
            selected.extend(
                select_without_reused_images(
                    by_dimension[dimension],
                    dimension_quotas[dimension],
                    used_images,
                )
            )

    selected = sorted(selected, key=lambda item: stable_hash(item["sample_id"]))
    entries = [
        {
            "sample_id": item["sample_id"],
            "dataset": "MMDS",
            "split": "train",
            "jsonl_line": item["line_number"],
            "raw_id": item["raw_id"],
            "assistant_label": canonical_label(
                item["record"].get("assistant_rating")
            ),
            "assistant_dimension": (
                str(item["record"].get("assistant_dimension", "")).strip()
                or "unspecified"
            ),
            "image_references": item["images"],
            "conversation_sha256": item["conversation_sha256"],
        }
        for item in selected
    ]
    return {
        "schema": "mj0-smoke-manifest-v1",
        "seed_material": SEED_MATERIAL,
        "source_split": "train",
        "purpose": "adapter and parser qualification only",
        "selection_rules": [
            "match the official MMDS test safe/unsafe ratio by proportional allocation",
            "stratify unsafe records by assistant_dimension",
            "exclude records with images referenced by validation or test records",
            "exclude text-only training records because every official test record has images",
            "do not reuse an image reference within the smoke manifest",
            "do not use this manifest to select thresholds or report final accuracy",
        ],
        "sample_count": len(entries),
        "label_counts": dict(
            sorted(Counter(item["assistant_label"] for item in entries).items())
        ),
        "dimension_counts": dict(
            sorted(
                Counter(
                    item["assistant_dimension"]
                    for item in entries
                    if item["assistant_label"] == "unsafe"
                ).items()
            )
        ),
        "entries": entries,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the deterministic MMDS adapter-smoke manifest."
    )
    parser.add_argument("--mmds-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=100)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_manifest(load_records(args.mmds_jsonl.resolve()), args.size)
    if manifest["sample_count"] != args.size:
        raise RuntimeError(
            f"Expected {args.size} records, found {manifest['sample_count']}"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "sample_count": manifest["sample_count"],
                "label_counts": manifest["label_counts"],
                "dimension_counts": manifest["dimension_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
