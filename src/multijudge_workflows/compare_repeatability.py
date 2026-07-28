from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, dict[str, Any]]:
    records = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            records[record["sample_id"]] = record
    return records


def compare(first: Path, second: Path) -> dict[str, Any]:
    left = load(first)
    right = load(second)
    if set(left) != set(right):
        raise ValueError("Repeat runs contain different sample IDs")
    mismatches = []
    for sample_id in sorted(left):
        left_record = left[sample_id]
        right_record = right[sample_id]
        left_subdecision = left_record["subdecisions"][0]
        right_subdecision = right_record["subdecisions"][0]
        fields = {
            "raw_output": (
                left_subdecision["raw_output"],
                right_subdecision["raw_output"],
            ),
            "decision": (
                left_record["decision"],
                right_record["decision"],
            ),
            "prompt_sha256": (
                left_subdecision.get(
                    "prompt_sha256",
                    left_subdecision.get("message_sha256"),
                ),
                right_subdecision.get(
                    "prompt_sha256",
                    right_subdecision.get("message_sha256"),
                ),
            ),
        }
        differences = {
            key: value for key, value in fields.items() if value[0] != value[1]
        }
        if differences:
            mismatches.append(
                {"sample_id": sample_id, "differences": differences}
            )
    return {
        "schema": "multijudge-repeatability-v1",
        "sample_count": len(left),
        "mismatch_count": len(mismatches),
        "passed": not mismatches,
        "mismatches": mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two deterministic qualification runs."
    )
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.first.resolve(), args.second.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
