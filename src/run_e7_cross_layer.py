#!/usr/bin/env python3
"""E7 cross-layer closed loop: Fabric reputation settlement -> RGG-PBFT consensus."""
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src" / "rggpbft_distributed"))

from grouping import build_group_map

SEED_BASE = 20260705
E1_DIR = ROOT / "results" / "e1-final-2048-topup"
FABRIC_DIR = ROOT / "src" / "fabric"
TEST_NETWORK = FABRIC_DIR / "fabric-samples" / "test-network"
BIN_DIR = TEST_NETWORK.parent / "bin"
RESULTS_DIR = ROOT / "results"


# ---------------------------------------------------------------------------
# Pure data functions (tested in test_e7_cross_layer.py)
# ---------------------------------------------------------------------------

def deterministic_shard(sample_id, m=16):
    material = f"e7-shard|{sample_id}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:8], "big") % m


def compute_u_i(record):
    expected_safe = record.get("expected_input_safe")
    safety = record.get("safety", "")
    refusal = record.get("refusal", "")
    if expected_safe is True:
        return 1 if (safety == "Safe" and refusal == "No") else 0
    elif expected_safe is False:
        return 1 if (safety == "Safe" or refusal == "Yes") else 0
    return 0


def load_e1_records():
    gen = [json.loads(l) for l in (E1_DIR / "generation.jsonl").read_text("utf-8").splitlines() if l.strip()]
    mod = [json.loads(l) for l in (E1_DIR / "moderation.jsonl").read_text("utf-8").splitlines() if l.strip()]
    mod_by_id = {r["sample_id"]: r for r in mod}
    merged = []
    for g in gen:
        sid = g["sample_id"]
        if sid in mod_by_id:
            m = mod_by_id[sid]
            merged.append({
                "sample_id": sid,
                "expected_input_safe": g.get("expected_input_safe"),
                "safety": m.get("safety", ""),
                "refusal": m.get("refusal", ""),
            })
    return merged


def compute_evaluator_scores(records, m=16):
    n_m = {i: 0 for i in range(m)}
    sum_u = {i: 0 for i in range(m)}
    for rec in records:
        s = deterministic_shard(rec["sample_id"], m)
        n_m[s] += 1
        sum_u[s] += compute_u_i(rec)
    q_m = {}
    for i in range(m):
        q_m[i] = round(1_000_000 * sum_u[i] / n_m[i]) if n_m[i] > 0 else 500_000
    return q_m


def tamper_scores_low(q_m, targets, offset=300_000):
    result = dict(q_m)
    for t in targets:
        result[t] = max(0, q_m[t] - offset)
    return result


def tamper_scores_high(q_m, targets, offset=300_000):
    result = dict(q_m)
    for t in targets:
        result[t] = min(1_000_000, q_m[t] + offset)
    return result


def build_reputation_order(evaluator_states):
    entries = []
    for state in evaluator_states:
        eval_id = state.get("evalId", state.get("eval_id", ""))
        parts = eval_id.split("-")
        try:
            node_id = int(parts[-1])
        except (ValueError, IndexError):
            node_id = 0
        entries.append((state.get("reputationPpm", state.get("ReputationPPM", 0)), node_id))
    entries.sort(key=lambda x: (-x[0], x[1]))
    return [e[1] for e in entries]


def derive_pair_seed(block, m, delay, fault, batch, repeat):
    material = (
        f"zte-sci-local-v1|{SEED_BASE}|{block}|M={m}|delay={delay}|"
        f"fault={fault}|batch={batch}|repeat={repeat}"
    )
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()
    seed = int.from_bytes(bytes.fromhex(digest[:16]), "big") & 0x7FFFFFFFFFFFFFFF
    return {"pair_material": material, "pair_sha256": digest, "seed": seed}


# ---------------------------------------------------------------------------
# Identity binding manifest
# ---------------------------------------------------------------------------

def build_identity_binding(evaluator_states, rgg_public_keys, cert_sha256_map=None):
    """Build cross-layer identity binding manifest.
    Requires actual Fabric certificate hashes - no placeholders allowed.
    cert_sha256_map: {eval_id: sha256_hex} from actual signcert files."""
    bindings = []
    for state in evaluator_states:
        eval_id = state.get("evalId", state.get("eval_id", ""))
        parts = eval_id.split("-")
        try:
            node_id = int(parts[-1])
        except (ValueError, IndexError):
            node_id = 0
        cert_hash = "MISSING"
        if cert_sha256_map and eval_id in cert_sha256_map:
            cert_hash = cert_sha256_map[eval_id]
        binding = {
            "eval_id": eval_id,
            "node_id": node_id,
            "msp_id": state.get("mspId", state.get("MSPID", "")),
            "fabric_client_id_sha256": hashlib.sha256(
                state.get("clientId", state.get("ClientID", "")).encode()
            ).hexdigest(),
            "fabric_certificate_sha256": cert_hash,
            "rgg_ed25519_public_key_sha256": hashlib.sha256(
                rgg_public_keys.get(node_id, f"pk-{node_id}").encode()
            ).hexdigest(),
        }
        if cert_hash == "MISSING":
            raise ValueError(
                f"STOP-GATE: certificate SHA-256 missing for {eval_id}. "
                "Identity binding must contain real cert hashes, not placeholders."
            )
        bindings.append(binding)
    bindings.sort(key=lambda b: b["node_id"])
    return bindings


# ---------------------------------------------------------------------------
# Fabric CLI helpers (delegate to peer binary)
# ---------------------------------------------------------------------------

def peer_env(org_msp, msp_path, peer_addr="localhost:7051"):
    env = os.environ.copy()
    env["FABRIC_CFG_PATH"] = str(TEST_NETWORK.parent / "config")
    env["CORE_PEER_TLS_ENABLED"] = "true"
    env["CORE_PEER_LOCALMSPID"] = org_msp
    env["CORE_PEER_MSPCONFIGPATH"] = str(msp_path)
    env["CORE_PEER_ADDRESS"] = peer_addr
    env["CORE_PEER_TLS_ROOTCERT_FILE"] = str(
        TEST_NETWORK / "organizations" / "peerOrganizations"
        / f"{'org1' if 'Org1' in org_msp else 'org2' if 'Org2' in org_msp else 'org3'}.example.com"
        / "peers" / f"peer0.{'org1' if 'Org1' in org_msp else 'org2' if 'Org2' in org_msp else 'org3'}.example.com"
        / "tls" / "ca.crt"
    )
    env["PATH"] = str(BIN_DIR) + os.pathsep + env.get("PATH", "")
    return env


def run_peer(args, env, timeout=120):
    cmd = ["peer"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return result


ORDERER_CA = str(TEST_NETWORK / "organizations" / "ordererOrganizations" / "example.com" / "tlsca" / "tlsca.example.com-cert.pem")

ORDERER_BASE = [
    "-o", "localhost:7050", "--ordererTLSHostnameOverride", "orderer.example.com",
    "--tls", "--cafile", ORDERER_CA, "-C", "trustchannel", "-n", "tce",
]


def invoke_chaincode(env, args_json, peer_tls_pairs):
    cmd = ["chaincode", "invoke"] + ORDERER_BASE
    for addr, tls_file in peer_tls_pairs:
        cmd.extend(["--peerAddresses", addr, "--tlsRootCertFiles", tls_file])
    cmd.extend(["-c", args_json])
    return run_peer(cmd, env, timeout=120)


def query_chaincode(env, args_json):
    cmd = ["chaincode", "query"] + ORDERER_BASE + ["-c", args_json]
    return run_peer(cmd, env, timeout=60)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_e7(config_path, output_dir, run_consensus=False):
    config = json.loads(pathlib.Path(config_path).read_text("utf-8"))
    output_dir = pathlib.Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    m = config["evaluator_count"]
    kg = config["groups"]

    # Load E1 data and compute scores
    print("Loading E1 records...")
    records = load_e1_records()
    print(f"  {len(records)} merged records loaded")

    q_m = compute_evaluator_scores(records, m)
    print("Base scores (q_m):")
    for i in range(m):
        print(f"  eval-{i:02d}: {q_m[i]}")

    # Create orchestrator-friendly output
    run_records = []
    for scenario in config["scenarios"]:
        sid = scenario["id"]
        for r in range(scenario["repeats"]):
            derived = derive_pair_seed(
                block=f"e7-{sid.lower().replace('e7-', '').replace('_','-')}",
                m=m, delay=5, fault="none", batch="na", repeat=r,
            )
            attack_targets = scenario.get("attack_nodes", [])

            # Compute scores for this scenario
            if sid == "E7-S1":
                scores = tamper_scores_low(q_m, attack_targets)
            elif sid == "E7-S2":
                scores = tamper_scores_high(q_m, attack_targets)
            else:
                scores = dict(q_m)

            rec = {
                "run_id": f"{sid.lower()}-r{r}",
                "scenario": sid,
                "repeat": r,
                "evaluator_count": m,
                "attack_nodes": attack_targets,
                "scores": {str(k): v for k, v in scores.items()},
                "launch_consensus": scenario["launch_consensus"],
                "expected_confirmation": scenario["expected_confirmation"],
                **derived,
            }
            run_records.append(rec)

    (output_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    (output_dir / "environment.json").write_text(json.dumps({
        "python_version": sys.version,
        "e1_dir": str(E1_DIR),
        "seed_base": SEED_BASE,
    }, indent=2), encoding="utf-8")

    # Reputation order derived from E1 scores (no fake Fabric state).
    # The actual Fabric settlement will independently produce the canonical order.
    # This is the pre-Fabric best-effort estimate used only for planning.
    items = [(q_m[i], i) for i in range(m)]
    items.sort(key=lambda x: (-x[0], x[1]))
    rep_order_estimate = [e[1] for e in items]
    group_map, leaders, l_gl = build_group_map(rep_order_estimate, kg)

    summary = {
        "evaluator_count": m,
        "groups": kg,
        "q_m_scores": q_m,
        "reputation_order_estimate": rep_order_estimate,
        "group_map": {str(k): v for k, v in group_map.items()},
        "group_leaders": {str(k): v for k, v in leaders.items()},
        "l_gl": list(l_gl),
        "total_runs": len(run_records),
        "scenarios": [s["id"] for s in config["scenarios"]],
        "note": "reputation_order_estimate is from E1 data pre-Fabric. "
                "Canonical order must come from Fabric settlement output.",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))

    # Save run records
    with (output_dir / "runs.jsonl").open("w", encoding="utf-8") as f:
        for rec in run_records:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    # Checksums
    checksums = []
    for name in ["config.json", "environment.json", "summary.json", "runs.jsonl"]:
        p = output_dir / name
        if p.exists():
            d = hashlib.sha256(p.read_bytes()).hexdigest()
            checksums.append(f"{d}  {name}")
    (output_dir / "checksums.sha256").write_text("\n".join(checksums) + "\n")

    print(f"\nE7 data preparation complete: {output_dir}")
    return output_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    run_e7(args.config, args.output_dir)
