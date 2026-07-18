#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "rggpbft_distributed"))
from grouping import build_group_map, generate_reputation_order

SEED_BASE = 20260705


def pair_identity(*, block, nodes, delay, fault, batch, repeat):
    material = (
        f"zte-sci-local-v1|{SEED_BASE}|{block}|M={nodes}|delay={delay}|"
        f"fault={fault}|batch={batch}|repeat={repeat}"
    )
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()
    seed = int(digest[:16], 16) & 0x7FFFFFFFFFFFFFFF
    return material, digest, seed


def make_run(*, block, protocol, nodes, delay, fault, repeat, rounds, reputation_order=None, run_id_suffix="", fault_nodes_override=None):
    material, digest, seed = pair_identity(
        block=block,
        nodes=nodes,
        delay=delay,
        fault=fault,
        batch="na",
        repeat=repeat,
    )
    pair_id = f"{block.lower()}-m{nodes}-d{delay}-{fault}-r{repeat}"
    large_local_fault_run = block == "B4" and nodes == 24 and fault != "none"

    if fault_nodes_override is not None:
        fn = fault_nodes_override
    elif fault == "f5":
        fn = "0,1"
    elif fault == "none":
        fn = ""
    else:
        fn = "0"

    rec = {
        "run_id": f"{pair_id}-{protocol}{run_id_suffix}",
        "pair_id": pair_id,
        "pair_material": material,
        "pair_sha256": digest,
        "seed": seed,
        "protocol": protocol,
        "nodes": nodes,
        "groups": 4,
        "delay_ms": delay,
        "fault": fault,
        "fault_nodes": fn,
        "repeat": repeat,
        "rounds": rounds,
        "view_timeout": 2.0 if large_local_fault_run else (0.5 if fault != "none" else 2.0),
        "round_timeout": 15 if large_local_fault_run else (12 if fault != "none" else 8),
    }
    if reputation_order is not None:
        rec["reputation_order"] = reputation_order
    return rec


def protocol_order(repeat):
    return ("pbft", "rgg") if repeat % 2 else ("rgg", "pbft")


def b3_matrix():
    runs = []
    for fault in ("f1", "f2", "f3", "f4"):
        for protocol in ("pbft", "rgg"):
            runs.append(
                make_run(
                    block="B3",
                    protocol=protocol,
                    nodes=16,
                    delay=5,
                    fault=fault,
                    repeat=1,
                    rounds=1,
                )
            )
    for fault in ("f2l", "f5"):
        runs.append(
            make_run(
                block="B3",
                protocol="rgg",
                nodes=16,
                delay=5,
                fault=fault,
                repeat=1,
                rounds=1,
            )
        )
    return runs


def b4_matrix():
    runs = []
    for repeat in range(1, 11):
        for nodes in (16, 24):
            for fault in ("f1", "f2", "f3", "f4"):
                for protocol in protocol_order(repeat):
                    runs.append(
                        make_run(
                            block="B4",
                            protocol=protocol,
                            nodes=nodes,
                            delay=5,
                            fault=fault,
                            repeat=repeat,
                            rounds=1,
                        )
                    )
            for fault in ("f2l", "f5"):
                runs.append(
                    make_run(
                        block="B4",
                        protocol="rgg",
                        nodes=nodes,
                        delay=5,
                        fault=fault,
                        repeat=repeat,
                        rounds=1,
                    )
                )
    return runs


def b5_matrix():
    runs = []
    for repeat in range(1, 11):
        for nodes in (16, 20, 24):
            for protocol in protocol_order(repeat):
                runs.append(
                    make_run(
                        block="B5",
                        protocol=protocol,
                        nodes=nodes,
                        delay=5,
                        fault="none",
                        repeat=repeat,
                        rounds=20,
                    )
                )
        for delay in (0, 20):
            for protocol in protocol_order(repeat):
                runs.append(
                    make_run(
                        block="B5",
                        protocol=protocol,
                        nodes=24,
                        delay=delay,
                        fault="none",
                        repeat=repeat,
                        rounds=20,
                    )
                )
    return runs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--corrective", action="store_true", help="generate groupingv2 corrective matrices")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.corrective:
        suffix = "-groupingv2"
        matrix_revision = "grouping-v2"
        matrices = [("b3_corrective_groupingv2", b3_corrective_matrix()),
                    ("b4_corrective_groupingv2", b4_corrective_matrix()),
                    ("b5_corrective_groupingv2", b5_corrective_matrix()),
                    ("b6_grouping_ablation", b6_ablation_matrix())]
        for name, matrix in matrices:
            payload = {
                "schema": "zte-sci-local-consensus-matrix-v1",
                "seed_base": SEED_BASE,
                "matrix_revision": matrix_revision,
                "run_count": len(matrix),
                "runs": matrix,
            }
            (args.output_dir / f"{name}.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    else:
        for name, matrix in (("b3", b3_matrix()), ("b4", b4_matrix()), ("b5", b5_matrix())):
            payload = {
                "schema": "zte-sci-local-consensus-matrix-v1",
                "seed_base": SEED_BASE,
                "run_count": len(matrix),
                "runs": matrix,
            }
            (args.output_dir / f"{name}_matrix.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


def b3_corrective_matrix():
    runs = []
    for fault in ("f1", "f2", "f3", "f4"):
        for protocol in ("pbft", "rgg"):
            rep = ",".join(map(str, generate_reputation_order(16))) if protocol == "rgg" else None
            fn = _fault_nodes_for(fault, rep, 16, 4) if protocol == "rgg" else ("0" if fault != "none" else "")
            runs.append(make_run(block="B3", protocol=protocol, nodes=16, delay=5,
                                 fault=fault, repeat=1, rounds=1,
                                 reputation_order=rep, run_id_suffix="-groupingv2",
                                 fault_nodes_override=fn))
    for fault in ("f2l", "f5"):
        rep = ",".join(map(str, generate_reputation_order(16)))
        fn = _fault_nodes_for(fault, rep, 16, 4)
        runs.append(make_run(block="B3", protocol="rgg", nodes=16, delay=5,
                             fault=fault, repeat=1, rounds=1,
                             reputation_order=rep, run_id_suffix="-groupingv2",
                             fault_nodes_override=fn))
    return runs


def b4_corrective_matrix():
    runs = []
    for repeat in range(1, 11):
        for nodes in (16, 24):
            for fault in ("f1", "f2", "f3", "f4"):
                for protocol in protocol_order(repeat):
                    rep = ",".join(map(str, generate_reputation_order(nodes))) if protocol == "rgg" else None
                    fn = _fault_nodes_for(fault, rep, nodes, 4) if protocol == "rgg" else ("0" if fault != "none" else "")
                    runs.append(make_run(block="B4", protocol=protocol, nodes=nodes, delay=5,
                                         fault=fault, repeat=repeat, rounds=1,
                                         reputation_order=rep, run_id_suffix="-groupingv2",
                                         fault_nodes_override=fn))
            for fault in ("f2l", "f5"):
                rep = ",".join(map(str, generate_reputation_order(nodes)))
                fn = _fault_nodes_for(fault, rep, nodes, 4)
                runs.append(make_run(block="B4", protocol="rgg", nodes=nodes, delay=5,
                                     fault=fault, repeat=repeat, rounds=1,
                                     reputation_order=rep, run_id_suffix="-groupingv2",
                                     fault_nodes_override=fn))
    return runs


def b5_corrective_matrix():
    runs = []
    for repeat in range(1, 11):
        for nodes in (16, 20, 24):
            for protocol in protocol_order(repeat):
                rep = ",".join(map(str, generate_reputation_order(nodes))) if protocol == "rgg" else None
                runs.append(make_run(block="B5", protocol=protocol, nodes=nodes, delay=5,
                                     fault="none", repeat=repeat, rounds=20,
                                     reputation_order=rep, run_id_suffix="-groupingv2"))
        for delay in (0, 20):
            for protocol in protocol_order(repeat):
                rep = ",".join(map(str, generate_reputation_order(24))) if protocol == "rgg" else None
                runs.append(make_run(block="B5", protocol=protocol, nodes=24, delay=delay,
                                     fault="none", repeat=repeat, rounds=20,
                                     reputation_order=rep, run_id_suffix="-groupingv2"))
    return runs


def _fault_nodes_for(fault, reputation_str, nodes, k_g):
    """Return fault_nodes string based on L_GL primary for non-identity reputation."""
    if reputation_str is None:
        return "0" if fault not in ("none",) else ""
    rep_order = [int(x) for x in reputation_str.split(",")]
    _, _, l_gl = build_group_map(rep_order, k_g)
    if fault == "f5":
        return f"{l_gl[0]},{l_gl[1]}"
    if fault in ("none",):
        return ""
    return str(l_gl[0])


def b6_ablation_matrix():
    """Structural grouping ablation — CPU only, no network."""
    import struct
    strategies = ["fixed_modulo", "seeded_random", "reputation_round_robin"]
    rank_conditions = ["separable", "build-then-exploit"]
    runs = []
    for repeat in range(1, 11):
        for nodes in (16, 24):
            for rank_cond in rank_conditions:
                pair_material = (
                    f"zte-sci-local-v1|{SEED_BASE}|B6|M={nodes}|delay=na|"
                    f"fault={rank_cond}|batch=na|repeat={repeat}"
                )
                pair_digest = hashlib.sha256(pair_material.encode("ascii")).hexdigest()
                pair_seed = int(pair_digest[:16], 16) & 0x7FFFFFFFFFFFFFFF
                pair_id = f"b6-m{nodes}-{rank_cond}-r{repeat}"
                for strategy in strategies:
                    runs.append({
                        "run_id": f"{pair_id}-rgg-{strategy}-groupingv2",
                        "pair_id": pair_id,
                        "pair_material": pair_material,
                        "pair_sha256": pair_digest,
                        "seed": pair_seed,
                        "protocol": "rgg",
                        "grouping_strategy": strategy,
                        "rank_condition": rank_cond,
                        "nodes": nodes,
                        "groups": 4,
                        "delay_ms": "na",
                        "fault": rank_cond,
                        "batch": "na",
                        "repeat": repeat,
                        "rounds": "na",
                    })
    return runs


if __name__ == "__main__":
    main()
