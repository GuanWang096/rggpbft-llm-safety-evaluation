#!/usr/bin/env python3
"""Aggregate E9 netem perturbation results."""
import argparse
import hashlib
import json
import pathlib
import statistics
from collections import defaultdict


def aggregate(input_dir, output_dir):
    input_dir = pathlib.Path(input_dir)
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runs = []
    summary_dirs = sorted(input_dir.glob("*/summary.json"))
    for sp in summary_dirs:
        run_dir = sp.parent
        summary = json.loads(sp.read_text())
        config_path = run_dir / "config.json"
        config = {}
        if config_path.exists():
            config = json.loads(config_path.read_text())

        # Extract netem evidence from netem_evidence.json (phased-startup runs)
        # Fall back to docker.log for legacy runs
        netem_applied = False
        netem_qdisc = ""
        rtt_gate_passed = False
        timing_order_ok = False
        cleanup_ok = False
        ready_count = 0
        evidence_path = run_dir / "netem_evidence.json"
        if evidence_path.exists():
            try:
                evidence = json.loads(evidence_path.read_text())
                netem_applied = evidence.get("has_netem", False) and len(evidence.get("qdisc_applied", [])) > 0
                rtt_gate_passed = evidence.get("rtt_probe", {}).get("gate", {}).get("passed", False)
                timing_order_ok = evidence.get("timing_order", {}).get("netem_before_driver", False) and \
                                  evidence.get("timing_order", {}).get("driver_before_first_event", False)
                ready_count = len(set(evidence.get("ready_node_ids", [])))
                cleanup = evidence.get("qdisc_after_cleanup", [])
                cleanup_ok = (
                    len(cleanup) == config.get("nodes", 0)
                    and all("netem" not in entry.get("qdisc", "") for entry in cleanup)
                )
                for entry in evidence.get("qdisc_applied", []):
                    netem_qdisc += f"node{entry.get('node','?')}: {entry.get('qdisc','')}\n"
            except (json.JSONDecodeError, KeyError):
                pass
        else:
            docker_log = run_dir / "docker.log"
            if docker_log.exists():
                log_text = docker_log.read_text(errors="replace")
                for line in log_text.splitlines():
                    if "NETEM_APPLIED" in line:
                        netem_applied = True
                        netem_qdisc += line + "\n"

        # Read matrix_entry.json for network_profile (not in config.json from run_v2.py)
        matrix_path = run_dir / "matrix_entry.json"
        matrix = {}
        if matrix_path.exists():
            matrix = json.loads(matrix_path.read_text())

        network_profile = matrix.get("network_profile", config.get("network_profile", "N0"))
        netem_delay = matrix.get("netem_delay_ms", config.get("netem_delay", 0))
        netem_jitter = matrix.get("netem_jitter_ms", config.get("netem_jitter", 0))
        netem_loss = matrix.get("netem_loss_pct", config.get("netem_loss", 0))

        runs.append({
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "protocol": config.get("mode", config.get("protocol", "")),
            "nodes": config.get("nodes", 0),
            "delay_ms": config.get("delay_ms", 5),
            "fault": config.get("fault", "none"),
            "repeat": config.get("repeat", 0),
            "netem_delay": netem_delay,
            "netem_jitter": netem_jitter,
            "netem_loss": netem_loss,
            "network_profile": network_profile,
            "netem_applied": netem_applied,
            "rtt_gate_passed": rtt_gate_passed,
            "timing_order_ok": timing_order_ok,
            "cleanup_ok": cleanup_ok,
            "ready_count": ready_count,
            "completed_rounds": summary.get("driver_success_count", 0),
            "total_rounds": config.get("rounds", summary.get("total_rounds", 20)),
            "success": summary.get("driver_success_count", 0) == config.get("rounds", 20),
            "safety_violations": summary.get("safety_violation_events", 0),
            "conflicting_commits": summary.get("conflicting_commit_count", 0),
            "view_changes": summary.get("view_change_sent_events", 0),
            "message_count": summary.get("final_protocol_messages_sent", 0),
            "total_bytes": summary.get("final_protocol_bytes_sent", 0),
            "p50_latency_ms": summary.get("client_latency_ms", {}).get("p50", 0),
            "p95_latency_ms": summary.get("client_latency_ms", {}).get("p95", 0),
        })

    if not runs:
        raise SystemExit(f"No summary.json files found in {input_dir}")

    # Group by profile + nodes + protocol
    by_key = defaultdict(list)
    for r in runs:
        key = (r["network_profile"], r["nodes"], r["protocol"])
        by_key[key].append(r)

    per_config = {}
    for key, group in sorted(by_key.items()):
        profile, nodes, protocol = key
        n = len(group)
        completed = sum(1 for r in group if r["success"])
        netem_ok = sum(1 for r in group if r["netem_applied"])
        rtt_ok = sum(1 for r in group if r["rtt_gate_passed"])
        timing_ok = sum(1 for r in group if r["timing_order_ok"])
        cleanup_count = sum(1 for r in group if r["cleanup_ok"])
        ready_ok = sum(1 for r in group if r["ready_count"] == r["nodes"])
        latencies = [r["p50_latency_ms"] for r in group if r["p50_latency_ms"] > 0]
        view_changes = [r["view_changes"] for r in group]

        label = f"{profile}_M{nodes}_{protocol}"
        per_config[label] = {
            "network_profile": profile,
            "nodes": nodes,
            "protocol": protocol,
            "total_runs": n,
            "completed": completed,
            "completion_rate": completed / n if n > 0 else 0,
            "netem_verified": netem_ok,
            "netem_verification_rate": netem_ok / n if n > 0 else 0,
            "rtt_gate_passed": rtt_ok,
            "rtt_gate_pass_rate": rtt_ok / n if n > 0 else 0,
            "timing_order_ok": timing_ok,
            "qdisc_cleanup_ok": cleanup_count,
            "ready_barrier_ok": ready_ok,
            "safety_violations": sum(r["safety_violations"] for r in group),
            "mean_view_changes": statistics.mean(view_changes) if view_changes else 0,
            "mean_messages": statistics.mean([r["message_count"] for r in group if r["message_count"] > 0]) if any(r["message_count"] > 0 for r in group) else 0,
            "mean_p50_latency_ms": statistics.mean(latencies) if latencies else 0,
            "max_p50_latency_ms": max(latencies) if latencies else 0,
        }

    # Infrastructure failures: runs where completion < total_rounds
    infra_failures = [r for r in runs if r["completed_rounds"] < r["total_rounds"]]
    infra_by_config = defaultdict(list)
    for r in infra_failures:
        key = (r["network_profile"], r["nodes"], r["protocol"])
        infra_by_config[key].append(r["run_id"])

    agg = {
        "experiment": "E9",
        "description": "Single-host Docker network emulation",
        "total_runs": len(runs),
        "total_unique_runs": len(set(r["run_id"] for r in runs)),
        "infrastructure_failures": {
            "count": len(infra_failures),
            "by_config": {str(k): v for k, v in infra_by_config.items()},
        },
        "per_config": per_config,
    }

    netem_runs = [run for run in runs if run["netem_delay"] > 0]
    validation = {
        "checks": {
            "all_rounds_completed": all(run["success"] for run in runs),
            "all_ready_barriers_complete": all(run["ready_count"] == run["nodes"] for run in runs),
            "all_netem_parameters_verified": all(run["netem_applied"] for run in netem_runs),
            "all_netem_rtt_gates_passed": all(run["rtt_gate_passed"] for run in netem_runs),
            "all_netem_timing_orders_valid": all(run["timing_order_ok"] for run in netem_runs),
            "all_netem_qdiscs_cleared": all(run["cleanup_ok"] for run in netem_runs),
            "zero_safety_violations": sum(run["safety_violations"] for run in runs) == 0,
            "zero_conflicting_commits": sum(run["conflicting_commits"] for run in runs) == 0,
        }
    }
    if not all(validation["checks"].values()):
        raise RuntimeError(f"E9 qualification stop gate failed: {validation['checks']}")

    out_path = output_dir / "aggregate.json"
    out_path.write_text(json.dumps(agg, indent=2))
    (output_dir / "validation_report.json").write_text(json.dumps(validation, indent=2))
    print(json.dumps(agg, indent=2))

    csv_path = output_dir / "aggregate.csv"
    with csv_path.open("w") as f:
        headers = ["label", "profile", "nodes", "protocol", "total", "completed",
                   "completion_rate", "netem_verified", "safety_violations",
                   "mean_view_changes", "mean_p50_ms"]
        f.write(",".join(headers) + "\n")
        for label, s in sorted(per_config.items()):
            row = [label, s["network_profile"], str(s["nodes"]), s["protocol"],
                   str(s["total_runs"]), str(s["completed"]),
                   f"{s['completion_rate']:.4f}", str(s["netem_verified"]),
                   str(s["safety_violations"]), f"{s['mean_view_changes']:.1f}",
                   f"{s['mean_p50_latency_ms']:.1f}"]
            f.write(",".join(row) + "\n")

    checksum_names = ["aggregate.json", "aggregate.csv", "validation_report.json"]
    (output_dir / "checksums.sha256").write_text("".join(
        f"{hashlib.sha256((output_dir / name).read_bytes()).hexdigest()}  {name}\n"
        for name in checksum_names
    ))
    print(f"\nAggregated {len(runs)} runs -> {output_dir}")
    return agg


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    aggregate(args.input_dir, args.output_dir)
