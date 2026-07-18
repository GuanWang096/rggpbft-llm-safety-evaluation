#!/usr/bin/env python3
"""E8 protocol-level grouping strategy ablation matrix generator."""
import hashlib
import json
import pathlib
import random
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "rggpbft_distributed"))
from grouping import build_group_map, generate_reputation_order

SEED_BASE = 20260705


def derive_pair_seed(block, nodes, delay, fault, batch, repeat):
    material = (
        f"zte-sci-local-v1|{SEED_BASE}|{block}|M={nodes}|delay={delay}|"
        f"fault={fault}|batch={batch}|repeat={repeat}"
    )
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()
    seed = int.from_bytes(bytes.fromhex(digest[:16]), "big") & 0x7FFFFFFFFFFFFFFF
    return material, digest, seed


def make_run(*, block, protocol, strategy, nodes, delay, fault, repeat, rounds,
             reputation_order, fault_nodes, run_id_suffix="-groupingv2"):
    material, digest, seed = derive_pair_seed(
        block=block, nodes=nodes, delay=delay, fault=fault, batch="na", repeat=repeat,
    )
    pair_id = f"{block.lower()}-m{nodes}-d{delay}-{fault}-r{repeat}"
    rec = {
        "run_id": f"{pair_id}-{protocol}-{strategy}{run_id_suffix}",
        "pair_id": pair_id,
        "pair_material": material,
        "pair_sha256": digest,
        "seed": seed,
        "protocol": protocol,
        "strategy": strategy,
        "nodes": nodes,
        "groups": 4,
        "delay_ms": delay,
        "fault": fault,
        "fault_nodes": fault_nodes,
        "repeat": repeat,
        "rounds": rounds,
        "view_timeout": 0.5 if fault != "none" else 2.0,
        "round_timeout": 12 if fault != "none" else 8,
    }
    if reputation_order is not None:
        rec["reputation_order"] = reputation_order
        order = [int(value) for value in reputation_order.split(",")]
        group_map, leaders, l_gl = build_group_map(order, rec["groups"])
        rec["group_map"] = group_map
        rec["group_leaders"] = [leaders[group] for group in range(rec["groups"])]
        rec["global_primary"] = l_gl[0]
    return rec


def _fault_nodes_from_lgl(fault, rep_order, k_g=4):
    _, _, l_gl = build_group_map(rep_order, k_g)
    if fault == "f5":
        return f"{l_gl[0]},{l_gl[1]}"
    if fault in ("none",):
        return ""
    return str(l_gl[0])


def make_identity_order(nodes):
    return ",".join(str(i) for i in range(nodes))


def make_seeded_random_order(nodes, seed):
    rng = random.Random(seed)
    perm = list(range(nodes))
    rng.shuffle(perm)
    return ",".join(str(i) for i in perm)


def make_reputation_order(nodes):
    return ",".join(str(i) for i in generate_reputation_order(nodes))


def make_ranked_order(nodes, rank_condition, byzantine_nodes=None):
    """Generate reputation order with ranking condition applied."""
    base = generate_reputation_order(nodes)
    if byzantine_nodes is None:
        byzantine_nodes = {0, 1}
    if rank_condition == "separable":
        honest = [n for n in base if n not in byzantine_nodes]
        byz = sorted(byzantine_nodes)
        return honest + byz
    elif rank_condition == "build-then-exploit":
        byz = sorted(byzantine_nodes)
        honest = [n for n in base if n not in byzantine_nodes]
        return byz + honest
    return base


def build_order(strategy, nodes, rank_condition=None, pair_seed=None):
    if strategy == "identity_round_robin":
        return make_identity_order(nodes)
    elif strategy == "seeded_random":
        return make_seeded_random_order(nodes, pair_seed or 0)
    elif strategy == "reputation_round_robin":
        if rank_condition in ("separable", "build-then-exploit"):
            return ",".join(str(i) for i in make_ranked_order(nodes, rank_condition))
        return make_reputation_order(nodes)
    return None


def e8_normal_path_matrix():
    """Normal path: 5 configs x 4 strategies x 10 repeats = 200 runs.
    PBFT and reputation_round_robin reuse B5 corrective (block=B5).
    identity_round_robin and seeded_random are new (block=B5 for pairing)."""
    runs = []
    normal_configs = [
        (16, 5), (20, 5), (24, 0), (24, 5), (24, 20),
    ]
    for nodes, delay in normal_configs:
        for repeat in range(1, 11):
            material, digest, seed = derive_pair_seed(
                block="B5", nodes=nodes, delay=delay, fault="none", batch="na", repeat=repeat,
            )
            pair_id = f"b5-m{nodes}-d{delay}-none-r{repeat}"

            # PBFT (ungrouped) — reuse B5
            runs.append({
                "run_id": f"{pair_id}-pbft-groupingv2",
                "pair_id": pair_id,
                "pair_material": material,
                "pair_sha256": digest,
                "seed": seed,
                "protocol": "pbft",
                "strategy": "pbft_baseline",
                "nodes": nodes, "groups": 4,
                "delay_ms": delay, "fault": "none", "repeat": repeat, "rounds": 20,
                "view_timeout": 2.0, "round_timeout": 8,
                "source_series": "B5",
            })

            # identity_round_robin — NEW
            id_order = make_identity_order(nodes)
            runs.append(make_run(
                block="B5", protocol="rgg", strategy="identity_round_robin",
                nodes=nodes, delay=delay, fault="none", repeat=repeat, rounds=20,
                reputation_order=id_order, fault_nodes="",
            ))
            runs[-1]["source_series"] = "E8"

            # seeded_random — NEW
            rand_order = make_seeded_random_order(nodes, seed)
            runs.append(make_run(
                block="B5", protocol="rgg", strategy="seeded_random",
                nodes=nodes, delay=delay, fault="none", repeat=repeat, rounds=20,
                reputation_order=rand_order, fault_nodes="",
            ))
            runs[-1]["source_series"] = "E8"

            # reputation_round_robin — reuse B5 corrective
            rep_order = make_reputation_order(nodes)
            runs.append({
                "run_id": f"{pair_id}-rgg-groupingv2",
                "pair_id": pair_id,
                "pair_material": material,
                "pair_sha256": digest,
                "seed": seed,
                "protocol": "rgg",
                "strategy": "reputation_round_robin",
                "nodes": nodes, "groups": 4,
                "delay_ms": delay, "fault": "none", "repeat": repeat, "rounds": 20,
                "view_timeout": 2.0, "round_timeout": 8,
                "reputation_order": rep_order,
                "fault_nodes": "",
                "source_series": "B5",
            })

    return runs


def e8_fault_matrix():
    """Fault path: 240 runs (full) + 12 F2L/F5 qualification tests."""
    runs = []
    strategies = ["identity_round_robin", "seeded_random", "reputation_round_robin"]
    rank_conditions = ["separable", "build-then-exploit"]
    faults_main = ["f1", "f4"]

    # F2L/F5 qualification tests (12 runs) — M=16 only
    for rank_cond in rank_conditions:
        for strategy in strategies:
            for fault in ("f2l", "f5"):
                nodes = 16
                material, digest, seed = derive_pair_seed(
                    block=f"e8-qual-{strategy}-{rank_cond}", nodes=nodes, delay=5,
                    fault=fault, batch="na", repeat=1,
                )
                rep_order_str = build_order(strategy, nodes, rank_cond, seed)
                rep_order_list = [int(x) for x in rep_order_str.split(",")]
                fn = _fault_nodes_from_lgl(fault, rep_order_list)
                runs.append(make_run(
                    block=f"e8-qual-{strategy}-{rank_cond}", protocol="rgg",
                    strategy=strategy, nodes=nodes, delay=5, fault=fault,
                    repeat=1, rounds=1,
                    reputation_order=rep_order_str, fault_nodes=fn,
                ))
                runs[-1]["rank_condition"] = rank_cond

    # Main fault matrix
    for nodes in (16, 24):
        for rank_cond in rank_conditions:
            for fault in faults_main:
                for strategy in strategies:
                    for repeat in range(1, 11):
                        block = f"e8-fault-{strategy}-{rank_cond}"
                        material, digest, seed = derive_pair_seed(
                            block=block, nodes=nodes, delay=5, fault=fault,
                            batch="na", repeat=repeat,
                        )
                        rep_order_str = build_order(strategy, nodes, rank_cond, seed)
                        rep_order_list = [int(x) for x in rep_order_str.split(",")]
                        fn = _fault_nodes_from_lgl(fault, rep_order_list)
                        runs.append(make_run(
                            block=block, protocol="rgg", strategy=strategy,
                            nodes=nodes, delay=5, fault=fault,
                            repeat=repeat, rounds=1,
                            reputation_order=rep_order_str, fault_nodes=fn,
                        ))
                        runs[-1]["rank_condition"] = rank_cond

    return runs


def e8_fault_m16_main_matrix():
    """Return only the 120 M=16 F1/F4 corrective runs."""
    return [
        run for run in e8_fault_matrix()
        if run["nodes"] == 16 and run["fault"] in ("f1", "f4")
    ]


def validate_strategy_differentiation(matrix, label):
    """Stop-gate: verify that different strategies produce different orders/groups.
    Within each (rank_condition, fault, repeat) tuple, strategy orders must differ."""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in matrix:
        if r["protocol"] == "pbft":
            continue
        # Extract rank_condition from block (e.g. "e8-fault-identity_round_robin-separable")
        block = r.get("pair_id", "")
        rank_cond = "default"
        if "separable" in block:
            rank_cond = "separable"
        elif "build-then-exploit" in block or "buildthenexploit" in block:
            rank_cond = "build-then-exploit"
        key = (r.get("nodes"), r.get("delay_ms"), r["fault"], rank_cond, r["repeat"])
        groups[key].append(r)

    violations = []
    for key, runs in sorted(groups.items()):
        # Different strategies must have different orders
        orders_by_strat = {}
        for r in runs:
            strat = r["strategy"]
            order = r.get("reputation_order", "")
            if strat in orders_by_strat:
                if orders_by_strat[strat] != order:
                    violations.append(f"{key}: {strat} has conflicting orders within same config")
            orders_by_strat[strat] = order

        unique_orders = set(orders_by_strat.values())
        if len(unique_orders) < len(orders_by_strat):
            violations.append(
                f"STOP-GATE {label}: strategies share identical orders at {key}: "
                + str({s: o[:30] for s, o in orders_by_strat.items()})
            )

    if violations:
        for v in violations:
            print(f"  ERROR: {v}")
        raise SystemExit(
            f"STOP-GATE: {label} has {len(violations)} strategy differentiation violations. "
            "Different grouping strategies must produce different reputation orders."
        )
    print(f"  Strategy differentiation check passed for {label}")


def validate_topology_evidence(matrix, label):
    """Stop if a grouped run lacks topology evidence or strategies collapse to one map."""
    from collections import defaultdict

    grouped = defaultdict(list)
    violations = []
    for run in matrix:
        if run["protocol"] == "pbft":
            continue
        required = ("rank_condition", "group_map", "group_leaders", "global_primary")
        missing = [field for field in required if field not in run]
        if missing:
            violations.append(f"{run['run_id']}: missing {missing}")
            continue
        if len(run["group_map"]) != run["nodes"] or len(run["group_leaders"]) != run["groups"]:
            violations.append(f"{run['run_id']}: incomplete realized topology")
        key = (run["nodes"], run["delay_ms"], run["fault"], run["rank_condition"], run["repeat"])
        grouped[key].append(run)

    for key, runs in sorted(grouped.items()):
        if len(runs) <= 1:
            continue
        maps = {json.dumps(run["group_map"], sort_keys=True) for run in runs}
        if len(maps) != len(runs):
            violations.append(f"{key}: {len(runs)} strategies realize only {len(maps)} group maps")
    if violations:
        raise SystemExit(f"STOP-GATE {label}: " + "; ".join(violations[:10]))
    print(f"  Realized topology check passed for {label}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--fault-m24-only", action="store_true",
                        help="Generate only M=24 fault matrix (skip M=16 and normal path)")
    parser.add_argument("--fault-m16-main-only", action="store_true",
                        help="Generate only the 120 M=16 F1/F4 corrective runs")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.fault_m24_only and args.fault_m16_main_only:
        raise SystemExit("choose only one fault-matrix subset")
    normal = [] if (args.fault_m24_only or args.fault_m16_main_only) else e8_normal_path_matrix()
    fault = e8_fault_matrix()
    if args.fault_m24_only:
        fault = [r for r in fault if r["nodes"] == 24 and r["fault"] in ("f1", "f4")]
    elif args.fault_m16_main_only:
        fault = e8_fault_m16_main_matrix()

    # Stop-gate: verify strategy differentiation
    if normal:
        validate_strategy_differentiation(normal, "normal_path")
    validate_strategy_differentiation(fault, "fault_matrix")
    validate_topology_evidence(fault, "fault_matrix")

    for name, matrix, suffix in [
        ("e8_normal_path", normal, ""),
        ("e8_fault_matrix", fault, ""),
    ]:
        if not matrix:
            continue
        payload = {
            "schema": "zte-sci-local-consensus-matrix-v1",
            "seed_base": SEED_BASE,
            "matrix_revision": "grouping-v3-topology-audited",
            "experiment": "E8",
            "run_count": len(matrix),
            "runs": matrix,
        }
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"{name}: {len(matrix)} runs")

    # Count summary
    normal_new = [r for r in normal if r.get("source_series") == "E8"]
    normal_reuse = [r for r in normal if r.get("source_series") == "B5"]
    qual = [r for r in fault if r["fault"] in ("f2l", "f5")]
    fault_main = [r for r in fault if r["fault"] in ("f1", "f4")]
    print(f"  Normal: {len(normal)} total ({len(normal_reuse)} B5 reuse + {len(normal_new)} new)")
    print(f"  Fault qual: {len(qual)} F2L/F5")
    print(f"  Fault main: {len(fault_main)} F1/F4")


if __name__ == "__main__":
    main()
