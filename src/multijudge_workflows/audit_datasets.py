from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_label(value: Any) -> str:
    if value is None:
        return "null"
    label = str(value).strip()
    return label.lower() if label else "empty"


def image_references(conversations: Iterable[dict[str, Any]]) -> list[str]:
    references: list[str] = []
    for turn in conversations:
        images = turn.get("image") or []
        if isinstance(images, str):
            images = [images]
        references.extend(str(item) for item in images)
    return references


def audit_mmds(root: Path) -> dict[str, Any]:
    metadata_path = root / "mmds.jsonl"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Missing MMDS metadata: {metadata_path}")

    sample_count = 0
    ids: list[str] = []
    split_counts: Counter[str] = Counter()
    data_type_counts: Counter[str] = Counter()
    user_label_counts: Counter[str] = Counter()
    assistant_label_counts: Counter[str] = Counter()
    assistant_by_split: dict[str, Counter[str]] = {}
    target_model_counts: Counter[str] = Counter()
    image_refs: list[str] = []
    image_splits: dict[str, set[str]] = {}
    conversation_splits: dict[str, set[str]] = {}
    duplicate_ids: list[dict[str, Any]] = []
    first_id_occurrence: dict[str, dict[str, Any]] = {}
    invalid_json_lines: list[int] = []

    with metadata_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                invalid_json_lines.append(line_number)
                continue

            sample_count += 1
            record_id = str(record.get("id"))
            ids.append(record_id)
            split = str(record.get("set", "")).strip().lower() or "missing"
            assistant_label = normalized_label(record.get("assistant_rating"))
            conversations = record.get("conversations") or []
            conversation_hash = text_sha256(
                json.dumps(
                    conversations,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            conversation_splits.setdefault(conversation_hash, set()).add(split)
            occurrence = {
                "line_number": line_number,
                "split": split,
                "data_type": str(record.get("data_type", "")).strip() or "missing",
                "conversation_sha256": conversation_hash,
            }
            if record_id in first_id_occurrence:
                duplicate_ids.append(
                    {
                        "id": record_id,
                        "first": first_id_occurrence[record_id],
                        "duplicate": occurrence,
                    }
                )
            else:
                first_id_occurrence[record_id] = occurrence

            split_counts[split] += 1
            data_type_counts[str(record.get("data_type", "")).strip() or "missing"] += 1
            user_label_counts[normalized_label(record.get("user_rating"))] += 1
            assistant_label_counts[assistant_label] += 1
            assistant_by_split.setdefault(split, Counter())[assistant_label] += 1
            target_model_counts[
                str(record.get("target_model", "")).strip() or "empty"
            ] += 1
            record_images = image_references(conversations)
            image_refs.extend(record_images)
            for ref in record_images:
                image_splits.setdefault(ref, set()).add(split)

    unique_image_refs = sorted(set(image_refs))
    missing_images = [
        ref for ref in unique_image_refs if not (root / Path(ref)).is_file()
    ]
    duplicate_id_count = sample_count - len(set(ids))
    exact_conversation_cross_split = sorted(
        digest for digest, splits in conversation_splits.items() if len(splits) > 1
    )
    image_cross_split = sorted(
        ref for ref, splits in image_splits.items() if len(splits) > 1
    )
    formal_binary_count = assistant_label_counts["safe"] + assistant_label_counts["unsafe"]

    return {
        "metadata_path": str(metadata_path),
        "metadata_sha256": file_sha256(metadata_path),
        "sample_count": sample_count,
        "unique_id_count": len(set(ids)),
        "duplicate_id_count": duplicate_id_count,
        "duplicate_ids": duplicate_ids,
        "stable_sample_id_rule": "mmds:<set>:<one-based-jsonl-line>:<raw-id>",
        "invalid_json_line_count": len(invalid_json_lines),
        "invalid_json_lines": invalid_json_lines,
        "split_counts": dict(sorted(split_counts.items())),
        "data_type_counts": dict(sorted(data_type_counts.items())),
        "user_label_counts": dict(sorted(user_label_counts.items())),
        "assistant_label_counts": dict(sorted(assistant_label_counts.items())),
        "assistant_label_counts_by_split": {
            split: dict(sorted(counts.items()))
            for split, counts in sorted(assistant_by_split.items())
        },
        "formal_binary_assistant_count": formal_binary_count,
        "target_model_counts": dict(
            sorted(target_model_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "image_reference_count": len(image_refs),
        "unique_image_reference_count": len(unique_image_refs),
        "missing_image_count": len(missing_images),
        "missing_images": missing_images,
        "exact_conversation_cross_split_count": len(exact_conversation_cross_split),
        "exact_conversation_cross_split_sha256": exact_conversation_cross_split,
        "image_reference_cross_split_count": len(image_cross_split),
        "image_references_cross_split": image_cross_split,
        "warnings": [
            "Raw id is not globally unique; use the declared stable sample ID rule.",
            "The raw data_type value 'Augmentaion' must be preserved and canonically mapped to 'augmentation'.",
        ],
        "primary_task_eligible": (
            sample_count > 0
            and not invalid_json_lines
            and not missing_images
            and formal_binary_count > 0
            and split_counts["test"] > 0
            and assistant_by_split.get("test", Counter())["null"] == 0
            and not exact_conversation_cross_split
        ),
    }


def audit_siuo(root: Path) -> dict[str, Any]:
    generation_path = root / "siuo_gen.json"
    mcqa_path = root / "siuo_mcqa.json"
    for path in (generation_path, mcqa_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing SIUO metadata: {path}")

    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    mcqa = json.loads(mcqa_path.read_text(encoding="utf-8"))
    if not isinstance(generation, list) or not isinstance(mcqa, list):
        raise ValueError("SIUO metadata roots must be JSON arrays")

    generation_ids = [str(item.get("question_id")) for item in generation]
    mcqa_ids = [str(item.get("question_id")) for item in mcqa]
    generation_images = [str(item.get("image", "")) for item in generation]
    resolved_images: dict[str, str] = {}
    missing_images: list[str] = []
    for ref in sorted(set(generation_images)):
        if not ref:
            continue
        candidates = (root / Path(ref), root / "images" / Path(ref))
        match = next((path for path in candidates if path.is_file()), None)
        if match is None:
            missing_images.append(ref)
        else:
            resolved_images[ref] = str(match.relative_to(root))
    categories = Counter(
        str(item.get("category", "")).strip() or "missing" for item in generation
    )

    has_assistant_binary_label = any(
        any(
            key in item
            for key in (
                "assistant_rating",
                "response_rating",
                "response_safety",
                "label",
            )
        )
        for item in generation
    )

    return {
        "generation_path": str(generation_path),
        "generation_sha256": file_sha256(generation_path),
        "mcqa_path": str(mcqa_path),
        "mcqa_sha256": file_sha256(mcqa_path),
        "generation_count": len(generation),
        "mcqa_count": len(mcqa),
        "generation_unique_id_count": len(set(generation_ids)),
        "mcqa_unique_id_count": len(set(mcqa_ids)),
        "question_id_sets_match": set(generation_ids) == set(mcqa_ids),
        "category_counts": dict(sorted(categories.items())),
        "reference_answer_count": sum(
            bool(str(item.get("reference_answer", "")).strip()) for item in generation
        ),
        "correct_option_count": sum(
            bool(str(item.get("correct_option", "")).strip()) for item in mcqa
        ),
        "missing_image_count": len(missing_images),
        "missing_images": missing_images,
        "resolved_image_directory": (
            "images"
            if resolved_images
            and all(Path(path).parts[0] == "images" for path in resolved_images.values())
            else "."
        ),
        "has_assistant_binary_label": has_assistant_binary_label,
        "primary_task_eligible": has_assistant_binary_label and not missing_images,
        "recommended_role": (
            "response-safety stress/case set; not a labeled binary primary dataset"
            if not has_assistant_binary_label
            else "labeled response-safety evaluation set"
        ),
    }


def markdown_report(result: dict[str, Any]) -> str:
    mmds = result["datasets"]["MMDS"]
    siuo = result["datasets"]["SIUO"]
    lines = [
        "# MJ0 Dataset Audit",
        "",
        f"- Dataset root: `{result['dataset_root']}`",
        f"- Audit schema: `{result['audit_schema']}`",
        "",
        "## MMDS",
        "",
        f"- Samples: {mmds['sample_count']}",
        f"- Splits: `{json.dumps(mmds['split_counts'], ensure_ascii=False)}`",
        (
            "- Assistant labels: "
            f"`{json.dumps(mmds['assistant_label_counts'], ensure_ascii=False)}`"
        ),
        f"- Duplicate IDs: {mmds['duplicate_id_count']}",
        f"- Stable sample ID: `{mmds['stable_sample_id_rule']}`",
        f"- Missing images: {mmds['missing_image_count']}",
        (
            "- Exact conversations repeated across splits: "
            f"{mmds['exact_conversation_cross_split_count']}"
        ),
        (
            "- Image references reused across splits: "
            f"{mmds['image_reference_cross_split_count']}"
        ),
        f"- Primary task eligible: {str(mmds['primary_task_eligible']).lower()}",
        (
            "- Isolation rule: trainable calibration or aggregation components must "
            "exclude training/validation records whose images also occur in the test "
            "split. Frozen-judge evaluation on the official test split remains valid."
        ),
        "",
        "## SIUO",
        "",
        f"- Generation samples: {siuo['generation_count']}",
        f"- MCQA samples: {siuo['mcqa_count']}",
        f"- Missing images: {siuo['missing_image_count']}",
        (
            "- Has assistant binary labels: "
            f"{str(siuo['has_assistant_binary_label']).lower()}"
        ),
        f"- Recommended role: {siuo['recommended_role']}",
        "",
        "## Decision",
        "",
        (
            "MMDS is the response-level binary primary dataset if all integrity gates "
            "above pass. SIUO is retained as an independent stress/case set and is not "
            "pooled with MMDS accuracy metrics unless an official binary response label "
            "is added by the dataset maintainers. The two repeated MMDS raw IDs are "
            "numbering collisions between distinct records, so all code must use the "
            "declared stable sample ID rather than the raw ID alone."
        ),
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit MMDS and SIUO for MJ0.")
    parser.add_argument(
        "--dataset-root",
        type=Path,
        required=True,
        help="Directory containing MMDS/ and SIUO/.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for dataset_audit.json and DATASET_AUDIT.md.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_root = args.dataset_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "audit_schema": "mj0-dataset-audit-v1",
        "dataset_root": str(dataset_root),
        "datasets": {
            "MMDS": audit_mmds(dataset_root / "MMDS"),
            "SIUO": audit_siuo(dataset_root / "SIUO"),
        },
    }

    json_path = output_dir / "dataset_audit.json"
    markdown_path = output_dir / "DATASET_AUDIT.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(markdown_report(result), encoding="utf-8")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {json_path}")
    print(f"Wrote: {markdown_path}")


if __name__ == "__main__":
    main()
