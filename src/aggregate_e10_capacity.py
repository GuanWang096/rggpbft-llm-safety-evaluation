#!/usr/bin/env python3
"""Aggregate E10 Fabric capacity curve results from raw summary.json outputs.
Reads actual fields: workflow_throughput_per_s, measurement_duration_ms, per_operation."""
import argparse, hashlib, json, pathlib, statistics
from collections import defaultdict


def find_summaries(result_dir):
    result_dir = pathlib.Path(result_dir)
    summaries = []
    for sp in sorted(result_dir.rglob("summary.json")):
        if sp.parent.parent == result_dir:
            continue
        summaries.append(sp)
    if not summaries:
        summaries = sorted(result_dir.rglob("summary.json"))
    return summaries


def aggregate(input_dir, output_dir):
    input_dir = pathlib.Path(input_dir)
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = find_summaries(input_dir)
    if not summaries:
        raise SystemExit(f"No summary.json found in {input_dir}")

    runs = []
    for sp in summaries:
        summary = json.loads(sp.read_text())
        pair_dir = sp.parent
        run_id = pair_dir.name

        # Compute aggregate per-operation latency from per_operation dict
        per_op = summary.get("per_operation", {})
        op_latencies = []
        for op_name, op_data in per_op.items():
            if isinstance(op_data, dict) and "p50_ms" in op_data:
                op_latencies.append(op_data["p50_ms"])

        runs.append({
            "run_id": run_id,
            "workflow_count": summary.get("workflow_count", 0),
            "successful_workflows": summary.get("successful_workflows", 0),
            "failed_workflows": summary.get("failed_workflows", 0),
            "workflow_success_rate": summary.get("workflow_success_rate", 0),
            "workflow_throughput_per_s": summary.get("workflow_throughput_per_s", 0),
            "measurement_duration_ms": summary.get("measurement_duration_ms", 0),
            "evidence_record_throughput_per_s": summary.get("evidence_record_throughput_per_s", 0),
            "mean_op_latency_p50_ms": statistics.mean(op_latencies) if op_latencies else 0,
        })

    # Group by concurrency level (extract from run_id like e10-fabric-c4-r1)
    by_concurrency = defaultdict(list)
    for r in runs:
        parts = r["run_id"].split("-")
        for p in parts:
            if p.startswith("c") and p[1:].isdigit():
                c = int(p[1:])
                by_concurrency[c].append(r)
                break
        else:
            by_concurrency[0].append(r)

    # Compute c=1 baseline from actual data (not hardcoded)
    c1_group = by_concurrency.get(1, [])
    baseline_wf_rate = statistics.mean([r["workflow_throughput_per_s"] for r in c1_group]) if c1_group else None

    per_c = {}
    for c, group in sorted(by_concurrency.items()):
        n = len(group)
        wf_rates = [r["workflow_throughput_per_s"] for r in group if r["workflow_throughput_per_s"] > 0]
        success_rates = [r["workflow_success_rate"] for r in group]
        op_lats = [r["mean_op_latency_p50_ms"] for r in group if r["mean_op_latency_p50_ms"] > 0]

        mean_wf = statistics.mean(wf_rates) if wf_rates else 0
        stdev_wf = statistics.stdev(wf_rates) if len(wf_rates) > 1 else 0
        median_wf = statistics.median(wf_rates) if wf_rates else 0

        # Throughput efficiency relative to c=1 baseline
        if baseline_wf_rate and baseline_wf_rate > 0 and mean_wf > 0:
            efficiency = (mean_wf / (c * baseline_wf_rate)) * 100
            relative_speedup = mean_wf / baseline_wf_rate
        else:
            efficiency = None
            relative_speedup = None

        # P95 latency (max across runs as conservative estimate)
        p95_lat = max(wf_rates) if wf_rates else 0

        per_c[str(c)] = {
            "concurrency": c,
            "repeats": n,
            "mean_workflow_throughput_per_s": round(mean_wf, 6),
            "stdev_workflow_throughput_per_s": round(stdev_wf, 6),
            "median_workflow_throughput_per_s": round(median_wf, 6),
            "mean_success_rate": statistics.mean(success_rates) if success_rates else 0,
            "mean_op_latency_p50_ms": round(statistics.mean(op_lats), 1) if op_lats else 0,
            "throughput_efficiency_pct": round(efficiency, 1) if efficiency is not None else None,
            "relative_speedup_vs_c1": round(relative_speedup, 2) if relative_speedup is not None else None,
        }

    # Determine saturation: marginal gain < 15% of linear scaling
    rates = [(int(c), d["mean_workflow_throughput_per_s"]) for c, d in per_c.items()]
    rates.sort()
    saturation_point = None
    for i in range(1, len(rates)):
        c_prev, r_prev = rates[i - 1]
        c_curr, r_curr = rates[i]
        if r_prev > 0 and c_prev > 0:
            expected_linear = r_prev * (c_curr / c_prev)
            if r_curr < expected_linear * 0.85:
                saturation_point = c_prev
                break

    agg = {
        "experiment": "E10",
        "description": "Fabric/IPFS capacity curve (single-host Docker)",
        "endorsement_config": "Org1+Org2 peers only",
        "total_runs": len(runs),
        "baseline_workflow_throughput_per_s_c1": round(baseline_wf_rate, 6) if baseline_wf_rate else None,
        "saturation_point": saturation_point,
        "saturation_note": (
            f"Marginal throughput gain falls below 85% of linear scaling after c={saturation_point}. "
            "Single-host observation, not a strict capacity bound."
        ) if saturation_point else "Insufficient data to determine saturation",
        "per_concurrency": per_c,
    }

    out_path = output_dir / "aggregate.json"
    out_path.write_text(json.dumps(agg, indent=2))
    print(json.dumps(agg, indent=2))

    # CSV
    csv_path = output_dir / "aggregate.csv"
    with csv_path.open("w") as f:
        headers = ["concurrency", "repeats", "mean_wf_s", "stdev_wf_s", "median_wf_s",
                   "mean_success_rate", "mean_op_p50_ms", "efficiency_pct", "speedup_vs_c1"]
        f.write(",".join(headers) + "\n")
        for c, d in sorted(per_c.items(), key=lambda x: int(x[0])):
            row = [c, str(d["repeats"]),
                   f"{d['mean_workflow_throughput_per_s']:.6f}",
                   f"{d['stdev_workflow_throughput_per_s']:.6f}",
                   f"{d['median_workflow_throughput_per_s']:.6f}",
                   f"{d['mean_success_rate']:.4f}",
                   f"{d['mean_op_latency_p50_ms']:.1f}",
                   f"{d['throughput_efficiency_pct']}" if d["throughput_efficiency_pct"] else "",
                   f"{d['relative_speedup_vs_c1']}" if d["relative_speedup_vs_c1"] else ""]
            f.write(",".join(row) + "\n")

    # Checksums
    out_files = sorted([f.name for f in output_dir.glob("*.json") if f.name != "checksums.sha256"])
    out_files.extend([f.name for f in output_dir.glob("*.csv")])
    chk_lines = [f"{hashlib.sha256((output_dir / fn).read_bytes()).hexdigest()}  {fn}" for fn in out_files]
    (output_dir / "checksums.sha256").write_text("\n".join(chk_lines) + "\n")

    print(f"\nAggregated {len(runs)} runs ({len(by_concurrency)} concurrency levels) -> {output_dir}")
    return agg


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    aggregate(args.input_dir, args.output_dir)
