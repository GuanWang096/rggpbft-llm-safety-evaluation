#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import math
import pathlib
import statistics
from collections import defaultdict


def mean_ci95(values):
    mean = statistics.fmean(values)
    return mean, 0.0 if len(values) < 2 else 4.303 * statistics.stdev(values) / math.sqrt(len(values))


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    return values[int(fraction * (len(values) - 1))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    args = parser.parse_args()
    rows = []
    for config_path in sorted(args.root.rglob("config.json")):
        run_dir = config_path.parent
        config = json.loads(config_path.read_text())
        records = [json.loads(line) for line in (run_dir / "operations.jsonl").read_text().splitlines() if line]
        measured = [record for record in records if not record["warmup"]]
        tasks = defaultdict(list)
        for record in measured:
            tasks[record["taskId"]].append(record)
        successful = sum(all(record["success"] for record in task_records) for task_records in tasks.values())
        start = min(record["startedAt"] for record in measured)
        end = max(record["startedAt"] + record["latencyMs"] for record in measured)
        post_latencies = [record["latencyMs"] for record in measured if record["op"] == "fabric_post_task" and record["success"]]
        rows.append(
            {
                "run_id": config["runId"],
                "storage_mode": config["storageMode"],
                "concurrency": config["concurrency"],
                "payload_size": config["payloadSize"],
                "workflow_count": len(tasks),
                "workflow_success_rate": successful / len(tasks),
                "workflow_throughput_per_s": successful / ((end - start) / 1000),
                "fabric_post_p50_ms": percentile(post_latencies, 0.50),
                "fabric_post_p95_ms": percentile(post_latencies, 0.95),
                "fabric_post_p99_ms": percentile(post_latencies, 0.99),
            }
        )
    groups = defaultdict(list)
    for row in rows:
        groups[(row["storage_mode"], row["concurrency"], row["payload_size"])].append(row)
    aggregates = []
    for (mode, concurrency, payload), group in sorted(groups.items()):
        item = {"storage_mode": mode, "concurrency": concurrency, "payload_size": payload, "repeats": len(group)}
        for metric in ("workflow_success_rate", "workflow_throughput_per_s", "fabric_post_p50_ms", "fabric_post_p95_ms", "fabric_post_p99_ms"):
            values = [row[metric] for row in group if row[metric] is not None]
            item[f"{metric}_mean"], item[f"{metric}_ci95"] = mean_ci95(values) if values else (None, None)
        aggregates.append(item)
    for name, data in (("run_summary.csv", rows), ("aggregate_summary.csv", aggregates)):
        with (args.root / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    (args.root / "aggregate_summary.json").write_text(json.dumps(aggregates, indent=2, sort_keys=True) + "\n")
    with (args.root / "aggregate_checksums.sha256").open("w", encoding="ascii") as handle:
        for name in ("run_summary.csv", "aggregate_summary.csv", "aggregate_summary.json"):
            handle.write(f"{hashlib.sha256((args.root / name).read_bytes()).hexdigest()}  {name}\n")
    print(json.dumps(aggregates, indent=2))


if __name__ == "__main__":
    main()
