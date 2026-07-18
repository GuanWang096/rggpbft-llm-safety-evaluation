#!/usr/bin/env python3
"""B6 structural grouping ablation — CPU-only mapping evaluation, no Docker."""
import argparse
import hashlib
import json
import pathlib
import random
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "rggpbft_distributed"))
from grouping import build_group_map, generate_reputation_order


def build_strategy_mapping(*, strategy, nodes, k_g, reputation_order, rng):
    """Return the node-to-group map and the first-ranked leader of each group."""
    if strategy == "fixed_modulo":
        group_map = {node: node % k_g for node in range(nodes)}
        leaders = {group: group for group in range(k_g)}
    elif strategy == "seeded_random":
        placement_order = list(range(nodes))
        rng.shuffle(placement_order)
        group_map = {
            node: position % k_g for position, node in enumerate(placement_order)
        }
        leaders = {group: placement_order[group] for group in range(k_g)}
    elif strategy == "reputation_round_robin":
        group_map, leaders, _ = build_group_map(reputation_order, k_g)
    else:
        raise ValueError(f"unknown strategy: {strategy}")
    return group_map, leaders


def run_all(config_path):
    config = json.loads(pathlib.Path(config_path).read_text())
    runs = config["runs"]
    base = pathlib.Path(__file__).resolve().parents[1] / "results"
    out_dir = base / ("b6-grouping-ablation-groupingv2-" + time.strftime("%Y%m%dT%H%M%SZ"))
    out_dir.mkdir(parents=True, exist_ok=False)

    (out_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    results = []
    faults_map = {"separable": {0, 1}, "build-then-exploit": {0, 1}}

    for run_cfg in runs:
        nodes = run_cfg["nodes"]
        k_g = run_cfg["groups"]
        strategy = run_cfg["grouping_strategy"]
        rank_cond = run_cfg["rank_condition"]
        repeat = run_cfg["repeat"]
        seed = run_cfg["seed"]
        rng = random.Random(seed)

        # Determine fault node set
        fault_nodes = faults_map.get(rank_cond, set())

        # Determine reputation order
        if rank_cond == "separable":
            rep_order = generate_reputation_order(nodes, seed_base=20260705)
            # Ensure fault nodes are at low-reputation end by swapping them to end
            honest = [n for n in rep_order if n not in fault_nodes]
            byz = sorted(fault_nodes)
            rep_order = honest + byz
        else:
            rep_order = generate_reputation_order(nodes, seed_base=20260705)
            # Build-then-exploit: promote Byzantine nodes to front
            byz = sorted(fault_nodes)
            honest = [n for n in rep_order if n not in fault_nodes]
            rep_order = byz + honest

        group_map, leaders = build_strategy_mapping(
            strategy=strategy,
            nodes=nodes,
            k_g=k_g,
            reputation_order=rep_order,
            rng=rng,
        )

        # Compute metrics
        group_byz = {g: 0 for g in range(k_g)}
        for n in fault_nodes:
            group_byz[group_map[n]] += 1

        max_conc = max(group_byz.values()) if group_byz else 0
        groups_over_threshold = sum(1 for v in group_byz.values() if v > 1)
        num_byz_leaders = sum(1 for leader in leaders.values() if leader in fault_nodes)

        rec = {
            "run_id": run_cfg["run_id"],
            "pair_id": run_cfg["pair_id"],
            "nodes": nodes,
            "groups": k_g,
            "strategy": strategy,
            "rank_condition": rank_cond,
            "repeat": repeat,
            "seed": seed,
            "fault_nodes": sorted(fault_nodes),
            "reputation_order": ",".join(map(str, rep_order)),
            "group_map": {str(k): v for k, v in group_map.items()},
            "group_leaders": {str(k): v for k, v in leaders.items()},
            "byzantine_per_group": {str(k): v for k, v in group_byz.items()},
            "max_group_concentration": max_conc,
            "groups_exceeding_local_threshold": groups_over_threshold,
            "byzantine_leader_count": num_byz_leaders,
        }
        results.append(rec)

    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, separators=(",", ":")) for r in results) + "\n"
    )

    summary = {
        "total_runs": len(results),
        "completed_runs": len(results),
        "strategies": sorted(set(r["strategy"] for r in results)),
        "rank_conditions": sorted(set(r["rank_condition"] for r in results)),
        "scales": sorted(set(r["nodes"] for r in results)),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    env = {
        "python_version": sys.version,
        "grouping_module": "src/rggpbft_distributed/grouping.py",
        "seed_base": 20260705,
    }
    (out_dir / "environment.json").write_text(json.dumps(env, indent=2) + "\n")

    # checksums
    files = ["config.json", "environment.json", "results.jsonl", "summary.json"]
    lines = []
    for name in files:
        digest = hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    (out_dir / "checksums.sha256").write_text("".join(lines))

    print(json.dumps(summary, indent=2))
    return out_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=pathlib.Path, required=True)
    args = parser.parse_args()
    run_all(args.config)
