from __future__ import annotations

import json
import sys
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
if str(EXPERIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_DIR))

from correct_mj5_aggregate import run


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, values: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values),
        encoding="utf-8",
    )


def test_correction_uses_per_decision_required_commit_latency(
    tmp_path: Path,
) -> None:
    session = tmp_path / "session"
    run_dir = session / "runs" / "run-1"
    write_json(
        session / "aggregate.json",
        {
            "schema": "mj5-formal-aggregate-v1",
            "entries": [
                {
                    "run_id": "run-1",
                    "sequential_component_total": {"p95_ms": 999.0},
                }
            ],
        },
    )
    write_jsonl(
        run_dir / "stage_a.jsonl",
        [
            {"decision_id": "d1", "stage_a_total_ms": 10.0},
            {"decision_id": "d2", "stage_a_total_ms": 20.0},
        ],
    )
    write_jsonl(
        run_dir / "stage_c.jsonl",
        [
            {"decision_id": "d1", "stage_c_total_ms": 30.0},
            {"decision_id": "d2", "stage_c_total_ms": 40.0},
        ],
    )
    write_jsonl(
        run_dir / "rgg/events.jsonl",
        [
            {
                "type": "DRIVER_RESULT",
                "data": {"decision_id": "d1", "latency_ms": 50.0},
            },
            {
                "type": "DRIVER_RESULT",
                "data": {"decision_id": "d2", "latency_ms": 80.0},
            },
        ],
    )

    run(session)

    aggregate = json.loads(
        (session / "aggregate.json").read_text(encoding="utf-8")
    )
    entry = aggregate["entries"][0]
    assert aggregate["schema"] == "mj5-formal-aggregate-v2"
    assert "sequential_component_total" not in entry
    assert entry["decision_joined_component_total"]["mean_ms"] == 115.0
    assert entry["decision_joined_component_total"]["p95_ms"] == 140.0
