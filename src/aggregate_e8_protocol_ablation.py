#!/usr/bin/env python3
"""Aggregate E8 protocol ablation results. Reads matrix_entry.json for config, summary.json for results."""
import argparse, hashlib, json, pathlib, statistics
from collections import defaultdict


def recompute_event_checks(events_path):
    events = [json.loads(line) for line in pathlib.Path(events_path).read_text().splitlines() if line.strip()]
    commits = {}
    conflicts = 0
    for event in events:
        if event.get("type") != "COMMIT":
            continue
        sequence = event.get("data", {}).get("sequence")
        digest = event.get("data", {}).get("digest")
        previous = commits.setdefault(sequence, digest)
        if previous != digest:
            conflicts += 1
    return {
        "driver_success_count": sum(
            event.get("type") == "DRIVER_RESULT" and event.get("data", {}).get("success") is True
            for event in events
        ),
        "safety_violation_events": sum(event.get("type") == "SAFETY_VIOLATION" for event in events),
        "conflicting_commit_count": conflicts,
        "invalid_new_view_events": sum(event.get("type") == "INVALID_NEW_VIEW" for event in events),
    }


def aggregate(input_dir, output_dir):
    input_dir = pathlib.Path(input_dir)
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runs = []
    rejected = []
    seen_ids = set()
    summary_dirs = sorted(input_dir.glob("*/summary.json"))
    for sp in summary_dirs:
        run_dir = sp.parent
        summary = json.loads(sp.read_text())

        # Read matrix_entry.json for configuration (authoritative)
        me_path = run_dir / "matrix_entry.json"
        if not me_path.exists():
            rejected.append({"run_dir": str(run_dir), "reason": "missing matrix_entry.json"})
            continue
        me = json.loads(me_path.read_text())

        run_id = me.get("run_id", run_dir.name)
        if run_id in seen_ids:
            rejected.append({"run_dir": str(run_dir), "run_id": run_id, "reason": "duplicate run_id"})
            continue
        seen_ids.add(run_id)

        # Reject protocol or safety violations
        safety = summary.get("safety_violation_events", 0)
        if (safety > 0 or summary.get("conflicting_commit_count", 0) > 0
                or summary.get("invalid_new_view_events", 0) > 0):
            rejected.append({"run_dir": str(run_dir), "run_id": run_id,
                             "reason": "protocol/safety stop gate failed"})
            continue

        # Reject incomplete runs
        if summary.get("driver_success_rate", 0) < 1.0:
            rejected.append({"run_dir": str(run_dir), "run_id": run_id,
                             "reason": f"driver_success_rate={summary.get('driver_success_rate', 0)}"})
            continue

        # Extract rank_condition from pair_id (e.g. "e8-fault-identity_round_robin-separable-m16-...")
        pair_id = me.get("pair_id", "")
        rank_condition = "default"
        if "separable" in pair_id:
            rank_condition = "separable"
        elif "build-then-exploit" in pair_id:
            rank_condition = "build-then-exploit"

        cl = summary.get("client_latency_ms", {})
        runs.append({
            "run_id": run_id,
            "run_dir": str(run_dir),
            "mode": me.get("protocol", "rgg"),
            "strategy": me.get("strategy", ""),
            "rank_condition": rank_condition,
            "nodes": me.get("nodes", 0),
            "groups": me.get("groups", 4),
            "delay_ms": me.get("delay_ms", 5),
            "fault": me.get("fault", "none"),
            "fault_nodes": me.get("fault_nodes", ""),
            "repeat": me.get("repeat", 0),
            "rounds": me.get("rounds", 0),
            "reputation_order": me.get("reputation_order", ""),
            "group_map": me.get("group_map"),
            "group_leaders": me.get("group_leaders"),
            "global_primary": me.get("global_primary"),
            "driver_success_rate": summary.get("driver_success_rate", 0),
            "safety_violations": safety,
            "conflicting_commits": summary.get("conflicting_commit_count", 0),
            "invalid_signatures": summary.get("invalid_signature_events", 0),
            "invalid_new_views": summary.get("invalid_new_view_events", 0),
            "view_change_sent": summary.get("view_change_sent_events", 0),
            "new_view_accepted": summary.get("new_view_accepted_events", 0),
            "total_messages": summary.get("final_protocol_messages_sent", 0),
            "total_bytes": summary.get("final_protocol_bytes_sent", 0),
            "client_latency_mean": cl.get("mean", 0),
            "client_latency_p50": cl.get("p50", 0),
            "client_latency_p95": cl.get("p95", 0),
            "client_latency_p99": cl.get("p99", 0),
            "node_latency_mean": summary.get("node_latency_ms", {}).get("mean", 0),
            "node_commit_count": summary.get("node_commit_count", 0),
        })

    if not runs:
        raise SystemExit(f"No valid runs found in {input_dir} (rejected: {len(rejected)})")

    expected_matrix = json.loads((input_dir / "matrix.json").read_text())
    expected_count = expected_matrix.get("run_count", len(expected_matrix.get("runs", [])))

    # Stop gate: the three strategies must realize three distinct group maps.
    topology_sets = defaultdict(list)
    for run in runs:
        key = (run["nodes"], run["delay_ms"], run["fault"], run["rank_condition"], run["repeat"])
        topology_sets[key].append(run)
    topology_violations = []
    for key, group in sorted(topology_sets.items()):
        maps = {json.dumps(run.get("group_map"), sort_keys=True) for run in group}
        if len(group) != 3 or len(maps) != 3:
            topology_violations.append({"configuration": key, "runs": len(group), "unique_group_maps": len(maps)})
    if topology_violations:
        raise RuntimeError(f"E8 topology stop gate failed: {topology_violations[:3]}")

    # Deterministic six-run raw-event audit.
    sampled = sorted(runs, key=lambda run: hashlib.sha256(run["run_id"].encode()).hexdigest())[:6]
    raw_audit = []
    for run in sampled:
        run_dir = pathlib.Path(run["run_dir"])
        recomputed = recompute_event_checks(run_dir / "events.jsonl")
        summary = json.loads((run_dir / "summary.json").read_text())
        matches = all(summary.get(key) == value for key, value in recomputed.items())
        raw_audit.append({"run_id": run["run_id"], "recomputed": recomputed, "matches_summary": matches})
    if not all(item["matches_summary"] for item in raw_audit):
        raise RuntimeError("E8 raw-event audit does not match summary.json")

    # Group by mode, strategy, rank_condition, M, delay, fault
    by_key = defaultdict(list)
    for r in runs:
        key = (r["mode"], r["strategy"], r["rank_condition"], r["nodes"], r["delay_ms"], r["fault"])
        by_key[key].append(r)

    groups = {}
    for key, grp in sorted(by_key.items()):
        mode, strategy, rank_cond, nodes, delay, fault = key
        n = len(grp)
        label = f"{mode}_{strategy}_{rank_cond}_M{nodes}_d{delay}_{fault}"
        driver_rates = [r["driver_success_rate"] for r in grp]
        latencies = [r["client_latency_p50"] for r in grp if r["client_latency_p50"] > 0]
        msgs = [r["total_messages"] for r in grp if r["total_messages"] > 0]
        bytess = [r["total_bytes"] for r in grp if r["total_bytes"] > 0]

        groups[label] = {
            "mode": mode,
            "strategy": strategy,
            "rank_condition": rank_cond,
            "nodes": nodes,
            "delay_ms": delay,
            "fault": fault,
            "run_count": n,
            "mean_driver_success_rate": statistics.mean(driver_rates) if driver_rates else 0,
            "total_safety_violations": sum(r["safety_violations"] for r in grp),
            "total_conflicting_commits": sum(r["conflicting_commits"] for r in grp),
            "mean_view_changes_sent": statistics.mean([r["view_change_sent"] for r in grp]) if grp else 0,
            "mean_new_view_accepted": statistics.mean([r["new_view_accepted"] for r in grp]) if grp else 0,
            "mean_messages": statistics.mean(msgs) if msgs else 0,
            "mean_bytes": statistics.mean(bytess) if bytess else 0,
            "mean_client_latency_p50_ms": statistics.mean(latencies) if latencies else 0,
            "mean_client_latency_p95_ms": statistics.mean([r["client_latency_p95"] for r in grp if r["client_latency_p95"] > 0]) if grp else 0,
        }

    agg = {
        "experiment": "E8",
        "description": "Protocol-level grouping strategy ablation",
        "total_valid_runs": len(runs),
        "total_rejected": len(rejected),
        "expected_run_count": expected_count,
        "rejected": rejected,
        "groups": groups,
    }

    out_path = output_dir / "aggregate.json"
    out_path.write_text(json.dumps(agg, indent=2))
    print(json.dumps(agg, indent=2))

    # CSV
    csv_path = output_dir / "aggregate.csv"
    with csv_path.open("w") as f:
        headers = ["label", "mode", "strategy", "rank_condition", "nodes", "delay_ms", "fault",
                   "run_count", "mean_driver_success_rate", "total_safety_violations",
                   "mean_view_changes_sent", "mean_messages", "mean_bytes",
                   "mean_client_latency_p50_ms", "mean_client_latency_p95_ms"]
        f.write(",".join(headers) + "\n")
        for label, s in sorted(groups.items()):
            row = [label, s["mode"], s["strategy"], s["rank_condition"],
                   str(s["nodes"]), str(s["delay_ms"]), s["fault"],
                   str(s["run_count"]), f"{s['mean_driver_success_rate']:.4f}",
                   str(s["total_safety_violations"]),
                   f"{s['mean_view_changes_sent']:.1f}",
                   str(s["mean_messages"]), str(s["mean_bytes"]),
                   f"{s['mean_client_latency_p50_ms']:.1f}",
                   f"{s['mean_client_latency_p95_ms']:.1f}"]
            f.write(",".join(row) + "\n")

    # Validation report
    validation = {
        "rejected_runs": len(rejected),
        "rejected_details": rejected,
        "checks": {
            "all_runs_have_matrix_entry": all(pathlib.Path(r["run_dir"]).joinpath("matrix_entry.json").exists() for r in runs),
            "no_duplicate_run_ids": len(seen_ids) == len(runs),
            "zero_safety_violations": sum(r["safety_violations"] for r in runs) == 0,
            "zero_conflicting_commits": sum(r["conflicting_commits"] for r in runs) == 0,
            "run_count_matches": len(runs) == expected_count == len(summary_dirs) - len(rejected),
            "three_distinct_topologies_per_configuration": not topology_violations,
            "six_raw_event_recomputations_match": all(item["matches_summary"] for item in raw_audit),
        },
        "raw_event_audit": raw_audit,
    }
    (output_dir / "validation_report.json").write_text(json.dumps(validation, indent=2))

    # Checksums covering all output files
    out_files = sorted([f.name for f in output_dir.glob("*.json") if f.name != "checksums.sha256"])
    out_files.extend([f.name for f in output_dir.glob("*.csv")])
    chk_lines = []
    for fn in out_files:
        fp = output_dir / fn
        if fp.exists():
            chk_lines.append(f"{hashlib.sha256(fp.read_bytes()).hexdigest()}  {fn}")
    (output_dir / "checksums.sha256").write_text("\n".join(chk_lines) + "\n")

    print(f"\nAggregated {len(runs)} valid runs ({len(rejected)} rejected) -> {output_dir}")
    return agg


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    aggregate(args.input_dir, args.output_dir)
