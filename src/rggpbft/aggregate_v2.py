#!/usr/bin/env python3
import argparse
import csv
import json
import math
import pathlib
import statistics
from collections import defaultdict


def mean_ci95(values):
    mean = statistics.fmean(values)
    if len(values) < 2:
        return mean, 0.0
    critical = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776}.get(len(values), 1.96)
    return mean, critical * statistics.stdev(values) / math.sqrt(len(values))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    args = parser.parse_args()
    rows = []
    for config_path in sorted(args.root.rglob("config.json")):
        run_dir = config_path.parent
        summary_path = run_dir / "summary.json"
        if not summary_path.exists():
            continue
        config = json.loads(config_path.read_text())
        summary = json.loads(summary_path.read_text())
        rows.append(
            {
                "run_id": run_dir.name,
                "mode": config["mode"],
                "nodes": config["nodes"],
                "groups": config["groups"] if config["mode"] == "rgg" else 1,
                "fault_mode": config["fault_mode"],
                "fault_nodes": config["fault_nodes"],
                "fault_delay_ms": config["fault_delay_ms"],
                "driver_success_rate": summary["driver_success_rate"],
                "client_latency_mean_ms": summary["client_latency_ms"]["mean"],
                "client_latency_p50_ms": summary["client_latency_ms"]["p50"],
                "client_latency_p95_ms": summary["client_latency_ms"]["p95"],
                "client_latency_p99_ms": summary["client_latency_ms"]["p99"],
                "node_commit_completeness": summary["node_commit_completeness"],
                "equivocation_sent_events": summary["equivocation_sent_events"],
                "equivocation_observed_events": summary["equivocation_observed_events"],
                "safety_violation_events": summary["safety_violation_events"],
                "conflicting_commit_count": summary["conflicting_commit_count"],
                "node_error_events": summary["node_error_events"],
            }
        )
    groups = defaultdict(list)
    for row in rows:
        key = (row["mode"], row["nodes"], row["groups"], row["fault_mode"], row["fault_nodes"], row["fault_delay_ms"])
        groups[key].append(row)
    aggregates = []
    for key, group in sorted(groups.items()):
        mode, nodes, group_count, fault_mode, fault_nodes, fault_delay = key
        item = {
            "mode": mode,
            "nodes": nodes,
            "groups": group_count,
            "fault_mode": fault_mode,
            "fault_nodes": fault_nodes,
            "fault_delay_ms": fault_delay,
            "repeats": len(group),
            "safety_preserved": all(not row["safety_violation_events"] and not row["conflicting_commit_count"] for row in group),
        }
        for metric in (
            "driver_success_rate",
            "client_latency_mean_ms",
            "client_latency_p50_ms",
            "client_latency_p95_ms",
            "client_latency_p99_ms",
            "node_commit_completeness",
            "equivocation_sent_events",
            "equivocation_observed_events",
            "node_error_events",
        ):
            values = [row[metric] for row in group if row[metric] is not None]
            if values:
                mean, ci = mean_ci95(values)
                item[f"{metric}_mean"] = mean
                item[f"{metric}_ci95"] = ci
        aggregates.append(item)
    for name, data in (("run_summary.csv", rows), ("aggregate_summary.csv", aggregates)):
        fields = sorted({field for row in data for field in row})
        with (args.root / name).open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fields)
            writer.writeheader()
            writer.writerows(data)
    (args.root / "aggregate_summary.json").write_text(json.dumps(aggregates, indent=2, sort_keys=True) + "\n")
    print(json.dumps(aggregates, indent=2))


if __name__ == "__main__":
    main()
