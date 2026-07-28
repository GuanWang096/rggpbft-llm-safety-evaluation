from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_references(record: dict[str, Any]) -> list[str]:
    references: list[str] = []
    for turn in record.get("conversations") or []:
        images = turn.get("image") or []
        if isinstance(images, str):
            images = [images]
        references.extend(str(value) for value in images)
    return sorted(set(references))


def conversation_sha256(record: dict[str, Any]) -> str:
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
                    "conversation_sha256": conversation_sha256(record),
                }
            )
    return records


def entry(item: dict[str, Any]) -> dict[str, Any]:
    record = item["record"]
    label = str(record.get("assistant_rating", "")).strip().lower()
    return {
        "sample_id": item["sample_id"],
        "dataset": "MMDS",
        "split": item["split"],
        "jsonl_line": item["line_number"],
        "raw_id": item["raw_id"],
        "assistant_label": label,
        "assistant_dimension": (
            str(record.get("assistant_dimension", "")).strip()
            or "unspecified"
        ),
        "image_references": item["images"],
        "conversation_sha256": item["conversation_sha256"],
    }


def manifest(
    *,
    records: list[dict[str, Any]],
    split: str,
    metadata_sha256: str,
    excluded_images: set[str],
) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    exclusions: Counter[str] = Counter()
    for item in records:
        if item["split"] != split:
            continue
        label = str(
            item["record"].get("assistant_rating", "")
        ).strip().lower()
        if label not in {"safe", "unsafe"}:
            exclusions["nonbinary_label"] += 1
            continue
        if not item["images"]:
            exclusions["no_image"] += 1
            continue
        if set(item["images"]) & excluded_images:
            exclusions["test_image_overlap"] += 1
            continue
        selected.append(entry(item))

    return {
        "schema": "mj1-formal-manifest-v1",
        "dataset": "MMDS",
        "source_metadata_sha256": metadata_sha256,
        "source_split": split,
        "purpose": (
            "image-disjoint development and parameter freezing"
            if split == "val"
            else "single-use frozen test evaluation"
        ),
        "ordering": "ascending one-based source JSONL line",
        "selection_rules": [
            "retain only official binary assistant response labels",
            "retain only records with multimodal image evidence",
            (
                "exclude validation records sharing any image with test"
                if split == "val"
                else "retain every eligible official test record"
            ),
        ],
        "sample_count": len(selected),
        "label_counts": dict(
            sorted(Counter(value["assistant_label"] for value in selected).items())
        ),
        "excluded_counts": dict(sorted(exclusions.items())),
        "entries": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build frozen MMDS validation and test manifests for MJ1."
    )
    parser.add_argument("--mmds-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    source = args.mmds_jsonl.resolve()
    records = load_records(source)
    test_images = {
        image
        for item in records
        if item["split"] == "test"
        for image in item["images"]
    }
    metadata_sha256 = sha256_file(source)
    validation = manifest(
        records=records,
        split="val",
        metadata_sha256=metadata_sha256,
        excluded_images=test_images,
    )
    test = manifest(
        records=records,
        split="test",
        metadata_sha256=metadata_sha256,
        excluded_images=set(),
    )
    if validation["sample_count"] < 50:
        raise RuntimeError("Too few image-disjoint validation records")
    if test["sample_count"] != 330:
        raise RuntimeError(
            f"Expected 330 formal test records, found {test['sample_count']}"
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "val": args.output_dir / "mmds_val_formal.json",
        "test": args.output_dir / "mmds_test_formal.json",
    }
    for split, path in outputs.items():
        value = validation if split == "val" else test
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                split: {
                    "path": str(path.resolve()),
                    "sample_count": (
                        validation if split == "val" else test
                    )["sample_count"],
                    "sha256": sha256_file(path),
                }
                for split, path in outputs.items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
