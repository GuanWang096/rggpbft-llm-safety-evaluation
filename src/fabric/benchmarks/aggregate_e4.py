#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import math
import pathlib
import statistics
from collections import defaultdict


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))] if ordered else None


def ingress_summary(run_dir, config):
    records = [json.loads(line) for line in (run_dir / "operations.jsonl").read_text().splitlines() if line]
    measured = [record for record in records if not record["warmup"]]
    by_task = defaultdict(list)
    for record in measured:
        by_task[record["taskId"]].append(record)
    successes = sum(all(row["success"] for row in rows) and len(rows) == 4 for rows in by_task.values())
    start = min(row["startedAt"] for row in measured)
    end = max(row["startedAt"] + row["latencyMs"] for row in measured)
    duration_ms = end - start
    result = {
        "mode": "ingress",
        "run_id": config["runId"],
        "concurrency": config["concurrency"],
        "payload_size": config["payloadSize"],
        "workflow_count": len(by_task),
        "successful_workflows": successes,
        "workflow_success_rate": successes / len(by_task),
        "duration_ms": duration_ms,
        "workflow_throughput_per_s": successes / (duration_ms / 1000),
    }
    for op in sorted({record["op"] for record in measured}):
        latencies = [record["latencyMs"] for record in measured if record["op"] == op and record["success"]]
        result[f"{op}_p50_ms"] = percentile(latencies, 0.50)
        result[f"{op}_p95_ms"] = percentile(latencies, 0.95)
        result[f"{op}_p99_ms"] = percentile(latencies, 0.99)
    return result


def lifecycle_summary(run_dir, config):
    summary = json.loads((run_dir / "summary.json").read_text())
    result = {
        "mode": "lifecycle",
        "run_id": config["run_id"],
        "concurrency": config["concurrency"],
        "payload_size": config["payload_size"],
        "workflow_count": summary["workflow_count"],
        "successful_workflows": summary["successful_workflows"],
        "workflow_success_rate": summary["workflow_success_rate"],
        "duration_ms": summary["measurement_duration_ms"],
        "workflow_throughput_per_s": summary["workflow_throughput_per_s"],
    }
    for op, values in summary["per_operation"].items():
        for metric in ("p50_ms", "p95_ms", "p99_ms"):
            result[f"{op}_{metric}"] = values[metric]
    return result


def mean_ci95(values):
    values = [float(value) for value in values]
    mean = statistics.fmean(values)
    if len(values) < 2:
        return mean, 0.0
    t_critical = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776}.get(len(values), 1.96)
    return mean, t_critical * statistics.stdev(values) / math.sqrt(len(values))


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["mode"], row["concurrency"], row["payload_size"])].append(row)
    output = []
    for (mode, concurrency, payload_size), group in sorted(groups.items()):
        item = {
            "mode": mode,
            "concurrency": concurrency,
            "payload_size": payload_size,
            "repeats": len(group),
            "all_workflows_successful": all(row["workflow_success_rate"] == 1.0 for row in group),
        }
        numeric_keys = sorted(set.intersection(*(set(row) for row in group)) - {
            "mode", "run_id", "concurrency", "payload_size", "workflow_count", "successful_workflows"
        })
        for key in numeric_keys:
            if all(isinstance(row[key], (int, float)) and row[key] is not None for row in group):
                mean, ci = mean_ci95([row[key] for row in group])
                item[f"{key}_mean"] = mean
                item[f"{key}_ci95"] = ci
        output.append(item)
    return output


def write_csv(path, rows):
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=pathlib.Path)
    args = parser.parse_args()
    rows = []
    for config_path in sorted(args.root.rglob("config.json")):
        run_dir = config_path.parent
        if not (run_dir / "operations.jsonl").exists() or not (run_dir / "summary.json").exists():
            continue
        config = json.loads(config_path.read_text())
        rows.append(lifecycle_summary(run_dir, config) if "run_id" in config else ingress_summary(run_dir, config))
    if not rows:
        raise SystemExit("No E4 runs found")
    aggregates = aggregate(rows)
    write_csv(args.root / "run_summary.csv", rows)
    write_csv(args.root / "aggregate_summary.csv", aggregates)
    (args.root / "aggregate_summary.json").write_text(json.dumps(aggregates, indent=2, sort_keys=True) + "\n")
    with (args.root / "aggregate_checksums.sha256").open("w", encoding="ascii") as handle:
        for name in ("run_summary.csv", "aggregate_summary.csv", "aggregate_summary.json"):
            handle.write(f"{hashlib.sha256((args.root / name).read_bytes()).hexdigest()}  {name}\n")
    print(json.dumps(aggregates, indent=2))


if __name__ == "__main__":
    main()
