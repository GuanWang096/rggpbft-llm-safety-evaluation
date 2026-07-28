from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def summarize(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean_ms": statistics.fmean(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
    }


def run(session: Path) -> None:
    session = session.resolve()
    aggregate_path = session / "aggregate.json"
    aggregate = read_json(aggregate_path)
    for entry in aggregate["entries"]:
        run_dir = session / "runs" / entry["run_id"]
        stage_a = read_jsonl(run_dir / "stage_a.jsonl")
        stage_c = {
            row["decision_id"]: row
            for row in read_jsonl(run_dir / "stage_c.jsonl")
        }
        driver = {
            event["data"]["decision_id"]: event["data"]
            for event in read_jsonl(run_dir / "rgg/events.jsonl")
            if event.get("type") == "DRIVER_RESULT"
        }
        decision_ids = {row["decision_id"] for row in stage_a}
        if decision_ids != set(stage_c) or decision_ids != set(driver):
            raise RuntimeError(f"{entry['run_id']}: decision join mismatch")
        values = [
            row["stage_a_total_ms"]
            + driver[row["decision_id"]]["latency_ms"]
            + stage_c[row["decision_id"]]["stage_c_total_ms"]
            for row in stage_a
        ]
        entry.pop("sequential_component_total", None)
        entry["decision_joined_component_total"] = {
            **summarize(values),
            "composition_method": (
                "exact decision_id join of Stage A, required-commit RGG, "
                "and Stage C"
            ),
        }
    aggregate["schema"] = "mj5-formal-aggregate-v2"
    aggregate_path.write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    args = parser.parse_args()
    run(args.session)


if __name__ == "__main__":
    main()
