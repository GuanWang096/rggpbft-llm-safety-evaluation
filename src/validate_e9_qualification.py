#!/usr/bin/env python3
"""Compare E9 runner-v3 qualification runs with the accepted v2 raw series."""
import argparse
import csv
import hashlib
import json
import pathlib
import statistics
from collections import defaultdict


def load_runs(root):
    grouped = defaultdict(list)
    for summary_path in pathlib.Path(root).glob("*/summary.json"):
        entry_path = summary_path.parent / "matrix_entry.json"
        if not entry_path.exists():
            continue
        entry = json.loads(entry_path.read_text())
        summary = json.loads(summary_path.read_text())
        evidence_path = summary_path.parent / "netem_evidence.json"
        evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
        key = (entry["network_profile"], entry["nodes"], entry["protocol"])
        grouped[key].append({
            "run_id": entry["run_id"],
            "p50_latency_ms": summary["client_latency_ms"]["p50"],
            "success": summary["driver_success_count"] == entry["rounds"],
            "safety": summary.get("safety_violation_events", 0),
            "conflicts": summary.get("conflicting_commit_count", 0),
            "ready": len(set(evidence.get("ready_node_ids", []))) == entry["nodes"],
            "timing": all(evidence.get("timing_order", {}).values()),
            "cleanup": (
                len(evidence.get("qdisc_after_cleanup", [])) == entry["nodes"]
                and all("netem" not in row.get("qdisc", "")
                        for row in evidence.get("qdisc_after_cleanup", []))
            ),
        })
    return grouped


def validate(old_dir, qualification_dir, output_dir):
    old = load_runs(old_dir)
    qualification = load_runs(qualification_dir)
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for key in sorted(old):
        old_values = [row["p50_latency_ms"] for row in old[key]]
        new_rows = qualification.get(key, [])
        if len(old_values) != 10 or len(new_rows) != 1:
            raise RuntimeError(f"E9 comparison cardinality failed for {key}")
        new_value = new_rows[0]["p50_latency_ms"]
        old_mean = statistics.fmean(old_values)
        relative_difference = (new_value - old_mean) / old_mean
        profile, nodes, protocol = key
        rows.append({
            "network_profile": profile,
            "nodes": nodes,
            "protocol": protocol,
            "old_repeat_count": len(old_values),
            "old_mean_p50_ms": old_mean,
            "old_min_p50_ms": min(old_values),
            "old_max_p50_ms": max(old_values),
            "qualification_p50_ms": new_value,
            "relative_difference": relative_difference,
            "distribution_gate_applicable": profile != "N0",
            "distribution_gate_passed": profile == "N0" or abs(relative_difference) <= 0.10,
            "engineering_gates_passed": (
                new_rows[0]["success"] and new_rows[0]["safety"] == 0
                and new_rows[0]["conflicts"] == 0 and new_rows[0]["ready"]
                and (profile == "N0" or (new_rows[0]["timing"] and new_rows[0]["cleanup"]))
            ),
        })
    checks = {
        "sixteen_configurations_compared": len(rows) == 16,
        "all_runner_v3_engineering_gates_passed": all(row["engineering_gates_passed"] for row in rows),
        "all_netem_distributions_within_ten_percent": all(row["distribution_gate_passed"] for row in rows),
    }
    if not all(checks.values()):
        raise RuntimeError(f"E9 qualification comparison failed: {checks}")
    document = {
        "schema_version": "e9-v3-qualification-comparison-v1",
        "old_raw_series": str(pathlib.Path(old_dir).resolve()),
        "qualification_series": str(pathlib.Path(qualification_dir).resolve()),
        "checks": checks,
        "comparisons": rows,
        "interpretation": (
            "N1-N3 enforce a 10% relative-difference gate against the ten-repeat raw mean. "
            "N0 is retained as a no-netem startup baseline and is excluded from this gate."
        ),
    }
    (output_dir / "comparison.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    with (output_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    names = ["comparison.json", "comparison.csv"]
    (output_dir / "checksums.sha256").write_text("".join(
        f"{hashlib.sha256((output_dir / name).read_bytes()).hexdigest()}  {name}\n"
        for name in names
    ), encoding="ascii")
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-dir", type=pathlib.Path, required=True)
    parser.add_argument("--qualification-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = validate(args.old_dir, args.qualification_dir, args.output_dir)
    print(json.dumps(result["checks"], indent=2))
