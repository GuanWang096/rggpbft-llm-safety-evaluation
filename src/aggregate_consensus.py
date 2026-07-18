#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import pathlib
import re
import statistics
import sys

from aggregate_b2 import bootstrap_mean_interval

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent / "rggpbft_distributed"),
)
from grouping import build_group_map


SIZE_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
}


def parse_size(value):
    match = re.fullmatch(r"\s*([0-9.]+)\s*([KMGT]?i?B)\s*", str(value), re.I)
    if not match:
        raise ValueError(f"unsupported Docker size: {value}")
    return int(float(match.group(1)) * SIZE_UNITS[match.group(2).upper()])


def paired_differences(rows, metric):
    pairs = {}
    for row in rows:
        pairs.setdefault(row["pair_id"], {})[row["protocol"]] = row
    output = []
    for pair_id, protocols in sorted(pairs.items()):
        if set(protocols) >= {"pbft", "rgg"}:
            pbft = float(protocols["pbft"][metric])
            rgg = float(protocols["rgg"][metric])
            output.append(
                {
                    "pair_id": pair_id,
                    "pbft": pbft,
                    "rgg": rgg,
                    "difference": rgg - pbft,
                }
            )
    return output


def derive_grouping_manifest(entry, ready_events):
    """Derive an RGG grouping manifest and verify it against node READY events."""
    order = [int(value) for value in entry["reputation_order"].split(",")]
    group_map, leaders, l_gl = build_group_map(order, int(entry["groups"]))
    if set(ready_events) != set(range(int(entry["nodes"]))):
        raise ValueError("READY events do not cover every RGG node")
    leader_set = set(leaders.values())
    for node, expected_group in group_map.items():
        observed = ready_events[node]
        expected = {
            "group": expected_group,
            "leader": node in leader_set,
            "primary": node == l_gl[0],
        }
        for field, value in expected.items():
            if observed.get(field) != value:
                raise ValueError(
                    f"READY mismatch for node {node}: {field}={observed.get(field)!r}, "
                    f"expected {value!r}"
                )
    return {
        "reputation_order": order,
        "group_map": {str(node): group for node, group in sorted(group_map.items())},
        "group_leaders": {str(group): node for group, node in sorted(leaders.items())},
        "l_gl": list(l_gl),
        "runtime_verified": True,
    }


def read_resource_stats(path):
    peak_memory = 0
    peak_cpu = 0.0
    peak_network = 0
    if not path.exists():
        return {
            "peak_aggregate_memory_bytes": 0,
            "peak_aggregate_cpu_percent": 0.0,
            "peak_aggregate_network_bytes": 0,
        }
    with path.open(encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            memory = 0
            cpu = 0.0
            network = 0
            for container in record.get("containers", []):
                usage = container.get("MemUsage", "0B / 0B").split("/", 1)[0]
                memory += parse_size(usage)
                cpu += float(container.get("CPUPerc", "0%").rstrip("%"))
                net_io = container.get("NetIO", "0B / 0B").split("/", 1)
                network += sum(parse_size(value) for value in net_io)
            peak_memory = max(peak_memory, memory)
            peak_cpu = max(peak_cpu, cpu)
            peak_network = max(peak_network, network)
    return {
        "peak_aggregate_memory_bytes": peak_memory,
        "peak_aggregate_cpu_percent": peak_cpu,
        "peak_aggregate_network_bytes": peak_network,
    }


def run_row(root, run_id):
    run_dir = root / run_id
    entry = json.loads((run_dir / "matrix_entry.json").read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    manifest = None
    if entry["protocol"] == "rgg":
        ready_events = {}
        with (run_dir / "events.jsonl").open(encoding="utf-8") as source:
            for line in source:
                event = json.loads(line)
                if event.get("type") == "READY":
                    ready_events[int(event["node"])] = event["data"]
        manifest = derive_grouping_manifest(entry, ready_events)
    row = {
        "run_id": run_id,
        "pair_id": entry["pair_id"],
        "protocol": entry["protocol"],
        "nodes": entry["nodes"],
        "delay_ms": entry["delay_ms"],
        "fault": entry["fault"],
        "repeat": entry["repeat"],
        "driver_success_rate": summary["driver_success_rate"],
        "client_latency_p50_ms": summary["client_latency_ms"]["p50"],
        "client_latency_p95_ms": summary["client_latency_ms"]["p95"],
        "client_latency_p99_ms": summary["client_latency_ms"]["p99"],
        "node_commit_completeness": summary["node_commit_completeness"],
        "final_protocol_messages_sent": summary["final_protocol_messages_sent"],
        "final_protocol_bytes_sent": summary["final_protocol_bytes_sent"],
        "max_accepted_view": summary["max_accepted_view"],
        "recovered_sequence_count": summary["recovered_sequence_count"],
        "recovery_latency_p50_ms": summary["recovery_latency_ms"]["p50"],
        "safety_violation_events": summary["safety_violation_events"],
        "conflicting_commit_count": summary["conflicting_commit_count"],
        "invalid_view_change_events": summary["invalid_view_change_events"],
        "invalid_new_view_events": summary["invalid_new_view_events"],
        "reputation_order": entry.get("reputation_order", ""),
        "group_map": json.dumps(manifest["group_map"], sort_keys=True) if manifest else "",
        "group_leaders": json.dumps(manifest["group_leaders"], sort_keys=True) if manifest else "",
        "l_gl": json.dumps(manifest["l_gl"]) if manifest else "",
        "runtime_grouping_verified": bool(manifest),
    }
    row.update(read_resource_stats(root / f"{run_id}.docker-stats.jsonl"))
    return row


def aggregate(input_dir, output_dir):
    status = json.loads((input_dir / "status.json").read_text(encoding="utf-8"))
    if status.get("state") != "completed":
        raise ValueError("consensus matrix is not completed")
    rows = [run_row(input_dir, run_id) for run_id in status["completed_runs"]]
    matrix = json.loads((input_dir / "matrix.json").read_text(encoding="utf-8"))
    if len(rows) != matrix.get("run_count", len(matrix.get("runs", []))):
        raise ValueError("completed run count does not match matrix")

    group_rows = {}
    for row in rows:
        key = (row["protocol"], row["nodes"], row["delay_ms"], row["fault"])
        group_rows.setdefault(key, []).append(row)
    groups = {}
    metrics = (
        "driver_success_rate",
        "client_latency_p50_ms",
        "client_latency_p95_ms",
        "node_commit_completeness",
        "final_protocol_messages_sent",
        "final_protocol_bytes_sent",
        "peak_aggregate_memory_bytes",
        "peak_aggregate_cpu_percent",
        "peak_aggregate_network_bytes",
        "max_accepted_view",
        "recovered_sequence_count",
        "recovery_latency_p50_ms",
    )
    for key, members in sorted(group_rows.items()):
        protocol, nodes, delay, fault = key
        group = {"run_count": len(members), "metrics": {}}
        for metric in metrics:
            values = [
                float(row[metric]) for row in members if row[metric] is not None
            ]
            if not values:
                group["metrics"][metric] = None
                continue
            seed = int(
                hashlib.sha256(f"{key}|{metric}".encode()).hexdigest()[:16], 16
            )
            group["metrics"][metric] = {
                "mean": statistics.fmean(values),
                "median": statistics.median(values),
                "bootstrap_95": bootstrap_mean_interval(values, seed),
                "values": values,
            }
        groups[f"{protocol}:m{nodes}:d{delay}:{fault}"] = group

    paired = {}
    for metric in (
        "client_latency_p50_ms",
        "client_latency_p95_ms",
        "final_protocol_messages_sent",
        "final_protocol_bytes_sent",
    ):
        differences = paired_differences(rows, metric)
        if differences:
            values = [item["difference"] for item in differences]
            seed = int(hashlib.sha256(f"paired|{metric}".encode()).hexdigest()[:16], 16)
            paired[metric] = {
                "pair_count": len(values),
                "mean_difference_rgg_minus_pbft": statistics.fmean(values),
                "bootstrap_95": bootstrap_mean_interval(values, seed),
                "pairs": differences,
            }

    grouping_manifests = {
        row["run_id"]: {
            "reputation_order": json.loads(f"[{row['reputation_order']}]") if row["reputation_order"] else [],
            "group_map": json.loads(row["group_map"]) if row["group_map"] else {},
            "group_leaders": json.loads(row["group_leaders"]) if row["group_leaders"] else {},
            "l_gl": json.loads(row["l_gl"]) if row["l_gl"] else [],
            "runtime_verified": row["runtime_grouping_verified"],
        }
        for row in rows
        if row["protocol"] == "rgg"
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    with (output_dir / "runs.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                "schema": "zte-sci-consensus-aggregate-v1",
                "run_count": len(rows),
                "groups": groups,
                "paired": paired,
                "grouping_manifests": grouping_manifests,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with (output_dir / "checksums.sha256").open("w", encoding="ascii") as output:
        for name in ("runs.csv", "summary.json"):
            output.write(
                f"{hashlib.sha256((output_dir / name).read_bytes()).hexdigest()}  {name}\n"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    aggregate(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
