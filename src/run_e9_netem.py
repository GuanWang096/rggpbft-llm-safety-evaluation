#!/usr/bin/env python3
"""E9: Docker network layer perturbation experiment."""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEED_BASE = 20260705

NETEM_APPLY = ROOT / "src" / "rggpbft_distributed" / "netem_apply.sh"
NETEM_CLEAR = ROOT / "src" / "rggpbft_distributed" / "netem_clear.sh"
RUN_V2 = ROOT / "src" / "rggpbft_distributed" / "run_v2.py"


def derive_pair_seed(block, nodes, delay, fault, batch, repeat):
    material = (
        f"zte-sci-local-v1|{SEED_BASE}|{block}|M={nodes}|delay={delay}|"
        f"fault={fault}|batch={batch}|repeat={repeat}"
    )
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()
    seed = int.from_bytes(bytes.fromhex(digest[:16]), "big") & 0x7FFFFFFFFFFFFFFF
    return material, digest, seed


def generate_matrix():
    network_profiles = {
        "N0": {"delay": 0, "jitter": 0, "loss": 0},
        "N1": {"delay": 10, "jitter": 2, "loss": 0},
        "N2": {"delay": 20, "jitter": 5, "loss": 0},
        "N3": {"delay": 50, "jitter": 10, "loss": 0},
    }
    runs = []
    for profile_id, profile in network_profiles.items():
        for nodes in (16, 24):
            for protocol in ("pbft", "rgg"):
                for repeat in range(1, 11):
                    block = f"e9-{profile_id.lower()}-{protocol}"
                    material, digest, seed = derive_pair_seed(
                        block=block, nodes=nodes, delay=5, fault="none",
                        batch="na", repeat=repeat,
                    )
                    rep_order = None
                    if protocol == "rgg":
                        sys.path.insert(0, str(ROOT / "src" / "rggpbft_distributed"))
                        from grouping import generate_reputation_order
                        rep_order = ",".join(str(i) for i in generate_reputation_order(nodes))

                    rec = {
                        "run_id": f"e9-{profile_id.lower()}-m{nodes}-{protocol}-r{repeat}",
                        "pair_id": f"e9-{profile_id.lower()}-m{nodes}-r{repeat}",
                        "pair_material": material,
                        "pair_sha256": digest,
                        "seed": seed,
                        "protocol": protocol,
                        "nodes": nodes,
                        "groups": 4,
                        "delay_ms": 5,
                        "fault": "none",
                        "fault_nodes": "",
                        "repeat": repeat,
                        "rounds": 20,
                        "view_timeout": max(2.0, 2.0 + profile["delay"] / 10),
                        "round_timeout": max(8, 12 + int(profile["delay"] * 2.0)),
                        "network_profile": profile_id,
                        "netem_delay_ms": profile["delay"],
                        "netem_jitter_ms": profile["jitter"],
                        "netem_loss_pct": profile["loss"],
                    }
                    if rep_order:
                        rec["reputation_order"] = rep_order
                    runs.append(rec)
    return runs, network_profiles


def generate_qualification_matrix():
    runs, profiles = generate_matrix()
    qualification = []
    seen = set()
    for run in runs:
        key = (run["network_profile"], run["nodes"], run["protocol"])
        if key not in seen:
            selected = dict(run)
            selected["series"] = "e9-runner-v3-qualification"
            qualification.append(selected)
            seen.add(key)
    return qualification, profiles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--qualification-only", action="store_true")
    args = parser.parse_args()

    runs, profiles = (generate_qualification_matrix()
                      if args.qualification_only else generate_matrix())
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.smoke:
        runs = [r for r in runs if r["nodes"] == 4][:4]
        print(f"Smoke mode: {len(runs)} runs")

    payload = {
        "schema": "zte-sci-local-e9-netem-v1",
        "seed_base": SEED_BASE,
        "experiment": "E9",
        "network_profiles": profiles,
        "run_count": len(runs),
        "runs": runs,
    }
    (output_dir / "matrix.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"E9 matrix: {len(runs)} runs in {output_dir}")


if __name__ == "__main__":
    main()
