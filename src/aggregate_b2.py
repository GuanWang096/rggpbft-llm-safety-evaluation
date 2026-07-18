#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import pathlib
import random
import statistics


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))] if ordered else None


def workflow_latencies(operations):
    bounds = {}
    for operation in operations:
        if operation.get("warmup"):
            continue
        task_id = operation["task_id"]
        start = operation["started_at_ms"]
        end = start + operation["latency_ms"]
        if task_id not in bounds:
            bounds[task_id] = [start, end]
        else:
            bounds[task_id][0] = min(bounds[task_id][0], start)
            bounds[task_id][1] = max(bounds[task_id][1], end)
    return {task_id: end - start for task_id, (start, end) in bounds.items()}


def bootstrap_mean_interval(values, seed, iterations=10000):
    rng = random.Random(seed)
    means = []
    for _ in range(iterations):
        sample = [rng.choice(values) for _ in values]
        means.append(statistics.fmean(sample))
    means.sort()
    return {
        "low": means[int(0.025 * (iterations - 1))],
        "high": means[int(0.975 * (iterations - 1))],
    }


def read_jsonl(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def system_row(pair_dir, system, pair_summary):
    candidates = list((pair_dir / system).glob("*/operations.jsonl"))
    if len(candidates) != 1:
        raise ValueError(f"expected one operations.jsonl for {pair_dir.name}/{system}")
    operations = read_jsonl(candidates[0])
    latencies = list(workflow_latencies(operations).values())
    summary = pair_summary[system]
    row = {
        "pair_id": pair_summary["pair"]["pair_id"],
        "repeat": pair_summary["pair"]["repeat"],
        "concurrency": pair_summary["pair"]["concurrency"],
        "system": system,
        "workflow_count": summary["workflow_count"],
        "workflow_success_rate": summary["workflow_success_rate"],
        "workflow_throughput_per_s": summary["workflow_throughput_per_s"],
        "evidence_record_throughput_per_s": summary[
            "evidence_record_throughput_per_s"
        ],
        "workflow_latency_p50_ms": percentile(latencies, 0.50),
        "workflow_latency_p95_ms": percentile(latencies, 0.95),
        "workflow_latency_p99_ms": percentile(latencies, 0.99),
        "nonwarmup_bytes_sent": sum(
            int(operation.get("bytes_sent", 0))
            for operation in operations
            if not operation.get("warmup")
        ),
    }
    if "batch_size" in pair_summary["pair"]:
        row["batch_size"] = pair_summary["pair"]["batch_size"]
    return row


def aggregate(input_dir, output_dir):
    status = json.loads((input_dir / "status.json").read_text(encoding="utf-8"))
    if status.get("state") != "completed":
        raise ValueError("B2 input is not completed")
    rows = []
    for pair_dir in sorted(path for path in input_dir.iterdir() if path.is_dir()):
        summary_path = pair_dir / "summary.json"
        if not summary_path.exists():
            continue
        pair_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for system in ("fabric", "signed"):
            rows.append(system_row(pair_dir, system, pair_summary))
    expected_rows = len(status.get("completed_pairs", [])) * 2
    if len(rows) != expected_rows:
        raise ValueError(
            f"expected {expected_rows} system rows, found {len(rows)}"
        )

    groups = {}
    for row in rows:
        groups.setdefault(
            (row["system"], row["concurrency"], row.get("batch_size")), []
        ).append(row)
    aggregate_groups = {}
    for (system, concurrency, batch_size), group_rows in sorted(
        groups.items(), key=lambda item: str(item[0])
    ):
        if len(group_rows) != 5:
            raise ValueError(f"expected five repeats for {system}/c{concurrency}")
        metrics = {}
        for name in (
            "workflow_throughput_per_s",
            "evidence_record_throughput_per_s",
            "workflow_latency_p50_ms",
            "workflow_latency_p95_ms",
            "workflow_latency_p99_ms",
            "nonwarmup_bytes_sent",
        ):
            values = [float(row[name]) for row in group_rows]
            seed_material = f"B2|{system}|{concurrency}|{name}"
            seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
            metrics[name] = {
                "mean": statistics.fmean(values),
                "median": statistics.median(values),
                "bootstrap_95": bootstrap_mean_interval(values, seed),
                "values": values,
            }
        group_name = f"{system}:c{concurrency}"
        if batch_size is not None:
            group_name += f":b{batch_size}"
        aggregate_groups[group_name] = {
            "repeat_count": len(group_rows),
            "all_workflows_successful": all(
                row["workflow_success_rate"] == 1 for row in group_rows
            ),
            "metrics": metrics,
        }

    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "runs.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                "schema": "zte-sci-b2-aggregate-v1",
                "pair_count": len(rows) // 2,
                "system_run_count": len(rows),
                "groups": aggregate_groups,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with (output_dir / "checksums.sha256").open("w", encoding="ascii") as output:
        for name in ("runs.csv", "summary.json"):
            digest = hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
            output.write(f"{digest}  {name}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    aggregate(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
