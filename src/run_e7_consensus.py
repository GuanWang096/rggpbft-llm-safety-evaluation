#!/usr/bin/env python3
"""M2: E7 consensus phase - launch RGG-PBFT with Fabric-exported reputation vectors."""
import hashlib, json, pathlib, subprocess, sys, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEED_BASE = 20260705

RUN_V2 = ROOT / "src" / "rggpbft_distributed" / "run_v2.py"
MATRIX_RUNNER = HERE / "run_consensus_matrix.py"


def derive_pair_seed(block, m, delay, fault, batch, repeat):
    material = (
        "zte-sci-local-v1|%d|%s|M=%d|delay=%d|"
        "fault=%s|batch=%s|repeat=%d"
    ) % (SEED_BASE, block, m, delay, fault, batch, repeat)
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()
    seed = int.from_bytes(bytes.fromhex(digest[:16]), "big") & 0x7FFFFFFFFFFFFFFF
    return {"pair_material": material, "pair_sha256": digest, "seed": seed}


def build_e7_consensus_matrix(fabric_results):
    """Build consensus matrix from Fabric-exported reputation vectors."""
    matrix = []
    for r in fabric_results:
        sid = r["scenario"]
        if sid not in ("E7-S0", "E7-S5"):
            continue
        if "error" in r:
            continue

        rep_order = r.get("reputation_order", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
        rep_str = ",".join(str(n) for n in rep_order)
        repeat = r["repeat"]

        # Derive deterministic seed using E7-S0 or E7-S5 as block label
        block_label = sid.lower().replace("e7-", "")
        seed_info = derive_pair_seed(block_label, 16, 5, "none", "fabric", repeat)

        entry = {
            "run_id": "%s-r%d" % (sid.lower(), repeat),
            "pair_id": "%s-r%d" % (sid.lower(), repeat),
            "protocol": "rgg",
            "nodes": 16,
            "groups": 4,
            "delay_ms": 5,
            "fault": "none",
            "fault_nodes": "",
            "repeat": repeat,
            "rounds": 20,
            "round_timeout": 8,
            "view_timeout": 2.0,
            "seed": seed_info["seed"],
            "pair_material": seed_info["pair_material"],
            "reputation_order": rep_str,
            "fabric_task_id": r.get("task_id", ""),
            "fabric_digest": r.get("digest", ""),
            "fabric_evaluator_count": r.get("evaluator_count", 16),
            "score_schema": r.get("score_schema", ""),
            "score_reference_semantics": r.get("score_reference_semantics", ""),
            "score_reference_is_human_ground_truth": r.get("score_reference_is_human_ground_truth"),
            "score_file_sha256": r.get("score_file_sha256", ""),
        }
        matrix.append(entry)
    return matrix


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run E7 consensus from explicit Fabric results")
    parser.add_argument("--fabric-results", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--matrix-path", type=pathlib.Path)
    args = parser.parse_args()
    fabric_results_path = args.fabric_results.resolve()
    if not fabric_results_path.exists():
        print("ERROR: e7_all_results.json not found. Run E7 Fabric phase first.")
        sys.exit(1)

    with open(fabric_results_path) as f:
        fabric_results = json.load(f)

    matrix = build_e7_consensus_matrix(fabric_results)
    if not matrix:
        print("ERROR: No valid consensus runs found in Fabric results")
        sys.exit(1)

    print("Building E7 consensus matrix: %d runs" % len(matrix))
    for entry in matrix:
        print("  %s: protocol=%s, nodes=%d, rounds=%d, rep_order=%s" % (
            entry["run_id"], entry["protocol"], entry["nodes"],
            entry["rounds"], entry["reputation_order"][:30] + "..."
        ))

    # Write matrix
    matrix_dir = HERE / "configs"
    matrix_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = (args.matrix_path or (matrix_dir / "e7_v2_consensus_matrix.json")).resolve()
    matrix_path.write_text(json.dumps({"runs": matrix, "run_count": len(matrix)}, indent=2))
    print("\nMatrix: %s" % matrix_path)

    # Launch consensus runs
    output_dir = args.output_dir.resolve()
    cmd = [
        sys.executable, str(MATRIX_RUNNER),
        "--matrix", str(matrix_path),
        "--output-dir", str(output_dir),
        "--skip-build",
    ]
    print("\nLaunching:\n  %s" % " ".join(cmd))
    r = subprocess.run(cmd)
    print("\nExit: %d" % r.returncode)

    if r.returncode == 0:
        print("Output: %s" % output_dir)

        # Auto-aggregate
        agg_script = HERE / "aggregate_consensus.py"
        agg_dir = pathlib.Path(str(output_dir) + "-agg")
        if agg_script.exists():
            agg_cmd = [
                sys.executable, str(agg_script),
                "--input-dir", str(output_dir),
                "--output-dir", str(agg_dir),
            ]
            print("\nAggregating:\n  %s" % " ".join(agg_cmd))
            r2 = subprocess.run(agg_cmd)
            print("Aggregate exit: %d" % r2.returncode)
            if r2.returncode == 0:
                print("Aggregate output: %s" % agg_dir)
        else:
            print("\nNOTE: aggregate_consensus.py not found, skipping aggregation")

    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
