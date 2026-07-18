#!/usr/bin/env python3
"""Aggregate E7 cross-layer results."""
import argparse
import hashlib
import json
import pathlib
import sys


def aggregate(result_dir):
    result_dir = pathlib.Path(result_dir)
    runs_path = result_dir / "runs.jsonl"
    if not runs_path.exists():
        raise SystemExit(f"runs.jsonl not found in {result_dir}")

    runs = [json.loads(l) for l in runs_path.read_text("utf-8").splitlines() if l.strip()]
    summary = json.loads((result_dir / "summary.json").read_text("utf-8"))

    by_scenario = {}
    for r in runs:
        sid = r["scenario"]
        by_scenario.setdefault(sid, []).append(r)

    agg = {
        "result_dir": str(result_dir),
        "total_runs": len(runs),
        "evaluator_count": summary["evaluator_count"],
        "groups": summary["groups"],
        "reputation_order": summary["reputation_order"],
        "group_leaders": summary["group_leaders"],
        "l_gl": summary["l_gl"],
        "q_m_scores": summary["q_m_scores"],
        "scenarios": {},
    }

    for sid, scenario_runs in sorted(by_scenario.items()):
        agg["scenarios"][sid] = {
            "count": len(scenario_runs),
            "launch_consensus": scenario_runs[0]["launch_consensus"],
            "expected_confirmation": scenario_runs[0]["expected_confirmation"],
            "attack_nodes": scenario_runs[0]["attack_nodes"],
        }

    out_path = result_dir / "aggregate.json"
    out_path.write_text(json.dumps(agg, indent=2), encoding="utf-8")

    # Checksum
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    checksum_line = f"{digest}  aggregate.json\n"
    existing = (result_dir / "checksums.sha256").read_text()
    if "aggregate.json" not in existing:
        (result_dir / "checksums.sha256").write_text(existing + checksum_line)

    print(json.dumps(agg, indent=2))
    return agg


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=pathlib.Path)
    args = parser.parse_args()
    aggregate(args.result_dir)
