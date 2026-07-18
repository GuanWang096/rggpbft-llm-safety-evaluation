#!/usr/bin/env python3
"""E7 Fabric phase: execute chaincode scenarios and export reputation vectors.

Scenarios (22 runs total):
  E7-S0 (x5): No attack baseline, full Fabric + consensus
  E7-S1 (x3): Low-score tampered snapshot, OBJECT from attack nodes
  E7-S2 (x3): High-score ballot stuffing, OBJECT from attack nodes
  E7-S3 (x3): Sub-quorum ACK (below-quorum timeout), deadline too short for full quorum
  E7-S4 (x3): Settlement replay rejection
  E7-S5 (x5): Legal new round, cross-round consistency
"""
import hashlib, json, os, pathlib, re, subprocess, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEED_BASE = 20260705

TDIR = ROOT / "src" / "fabric" / "fabric-samples" / "test-network"
BINDIR = TDIR.parent / "bin"
ORDERER_CA = TDIR / "organizations" / "ordererOrganizations" / "example.com" / "tlsca" / "tlsca.example.com-cert.pem"
PEER1_TLS = TDIR / "organizations" / "peerOrganizations" / "org1.example.com" / "peers" / "peer0.org1.example.com" / "tls" / "ca.crt"
PEER2_TLS = TDIR / "organizations" / "peerOrganizations" / "org2.example.com" / "peers" / "peer0.org2.example.com" / "tls" / "ca.crt"

E1_DIR = ROOT / "results" / "e1-final-2048-topup"
sys.path.insert(0, str(ROOT / "src" / "rggpbft_distributed"))
from grouping import build_group_map

PRIMARY_PEER = "localhost:7051"
CHAINCODE_NAME = os.environ.get("E7_CHAINCODE_NAME", "tce")

PEER_BASE = [
    "peer", "chaincode", "invoke",
    "--waitForEvent", "--waitForEventTimeout", "60s",
    "-o", "localhost:7050", "--ordererTLSHostnameOverride", "orderer.example.com",
    "--tls", "--cafile", str(ORDERER_CA),
    "-C", "trustchannel", "-n", CHAINCODE_NAME,
    "--peerAddresses", "localhost:7051", "--tlsRootCertFiles", str(PEER1_TLS),
    "--peerAddresses", "localhost:9051", "--tlsRootCertFiles", str(PEER2_TLS),
]

QUERY_BASE = [
    "peer", "chaincode", "query",
    "-o", "localhost:7050", "--ordererTLSHostnameOverride", "orderer.example.com",
    "--tls", "--cafile", str(ORDERER_CA),
    "-C", "trustchannel", "-n", CHAINCODE_NAME,
]


def make_env(org_msp, msp_path, peer_addr):
    e = os.environ.copy()
    e["PATH"] = str(BINDIR) + os.pathsep + e.get("PATH", "")
    e["FABRIC_CFG_PATH"] = str(TDIR.parent / "config")
    e["CORE_PEER_TLS_ENABLED"] = "true"
    e["CORE_PEER_LOCALMSPID"] = org_msp
    e["CORE_PEER_TLS_ROOTCERT_FILE"] = str(
        TDIR / "organizations" / "peerOrganizations" / "org1.example.com"
        / "peers" / "peer0.org1.example.com" / "tls" / "ca.crt"
    )
    e["CORE_PEER_MSPCONFIGPATH"] = str(msp_path)
    e["CORE_PEER_ADDRESS"] = peer_addr
    return e


def run_peer(args, env, timeout=120):
    cmd = ["peer"] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    return r


def invoke(env, args_json, timeout=120):
    return run_peer(PEER_BASE[1:] + ["-c", args_json], env, timeout)


def query(env, args_json, timeout=60):
    return run_peer(QUERY_BASE[1:] + ["-c", args_json], env, timeout)


def parse_invoke_payload(stdout, stderr):
    """Parse JSON payload from chaincode invoke response (embedded in stderr)."""
    combined = stdout + stderr
    m = re.search(r'payload:"((?:[^"\\]|\\.)*)"', combined)
    if m:
        raw = m.group(1)
        try:
            return json.loads(json.loads('"' + raw + '"'))
        except (json.JSONDecodeError, Exception):
            pass
    if stdout.strip():
        try:
            return json.loads(stdout.strip())
        except json.JSONDecodeError:
            pass
    return None


def extract_txids_from_decoded_block(block):
    txids = []
    for envelope in block.get("data", {}).get("data", []) or []:
        txid = (
            envelope.get("payload", {})
            .get("header", {})
            .get("channel_header", {})
            .get("tx_id", "")
        )
        if txid:
            txids.append(txid)
    return txids


def channel_height(env):
    result = run_peer(["channel", "getinfo", "-c", "trustchannel"], env, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"peer channel getinfo failed: {(result.stderr or result.stdout)[-300:]}")
    match = re.search(r'\{.*"height"\s*:\s*(\d+).*\}', result.stdout + result.stderr)
    if not match:
        raise RuntimeError(f"cannot parse channel height: {(result.stdout + result.stderr)[-300:]}")
    return int(match.group(1))


def committed_txids_since(env, height_before):
    height_after = channel_height(env)
    if height_after <= height_before:
        return []
    txids = []
    with tempfile.TemporaryDirectory(prefix="e7-block-") as tmp:
        tmp_path = pathlib.Path(tmp)
        for block_number in range(height_before, height_after):
            block_path = tmp_path / f"block-{block_number}.pb"
            decoded_path = tmp_path / f"block-{block_number}.json"
            fetch_args = [
                "channel", "fetch", str(block_number), str(block_path),
                "-c", "trustchannel", "-o", "localhost:7050",
                "--ordererTLSHostnameOverride", "orderer.example.com",
                "--tls", "--cafile", str(ORDERER_CA),
            ]
            fetched = run_peer(fetch_args, env, timeout=60)
            if fetched.returncode != 0:
                raise RuntimeError(f"cannot fetch block {block_number}: {(fetched.stderr or fetched.stdout)[-300:]}")
            decoded = subprocess.run(
                ["configtxlator", "proto_decode", "--input", str(block_path),
                 "--type", "common.Block", "--output", str(decoded_path)],
                capture_output=True, text=True, timeout=30, env=env,
            )
            if decoded.returncode != 0:
                raise RuntimeError(f"cannot decode block {block_number}: {(decoded.stderr or decoded.stdout)[-300:]}")
            txids.extend(extract_txids_from_decoded_block(json.loads(decoded_path.read_text())))
    return txids


def admin_env():
    msp = TDIR / "organizations" / "peerOrganizations" / "org1.example.com" / "users" / "Admin@org1.example.com" / "msp"
    return make_env("Org1MSP", msp, PRIMARY_PEER)


def audit_env():
    msp = TDIR / "organizations" / "peerOrganizations" / "org1.example.com" / "users" / "audit-service" / "msp"
    return make_env("Org1MSP", msp, PRIMARY_PEER)


def eval_env_for(i):
    eid = f"eval-{i:02d}"
    if i <= 5:
        org_idx, org_msp = 0, "Org1MSP"
    elif i <= 10:
        org_idx, org_msp = 1, "Org2MSP"
    else:
        org_idx, org_msp = 2, "Org3MSP"
    org_dir = "org1.example.com" if org_idx == 0 else ("org2.example.com" if org_idx == 1 else "org3.example.com")
    msp_path = TDIR / "organizations" / "peerOrganizations" / org_dir / "users" / eid / "msp"
    return make_env(org_msp, msp_path, PRIMARY_PEER)


def org_msp_for(i):
    if i <= 5: return "Org1MSP"
    elif i <= 10: return "Org2MSP"
    else: return "Org3MSP"


def load_e1_scores():
    """Legacy workload-utility scores retained only to reproduce the superseded E7."""
    records = []
    gen = [json.loads(l) for l in (E1_DIR / "generation.jsonl").read_text("utf-8").splitlines() if l.strip()]
    mod = [json.loads(l) for l in (E1_DIR / "moderation.jsonl").read_text("utf-8").splitlines() if l.strip()]
    mod_by_id = {r["sample_id"]: r for r in mod}
    for g in gen:
        sid = g["sample_id"]
        if sid in mod_by_id:
            m = mod_by_id[sid]
            records.append({"sample_id": sid, "expected_input_safe": g.get("expected_input_safe"),
                           "safety": m.get("safety", ""), "refusal": m.get("refusal", "")})

    m = 16
    n_m = {i: 0 for i in range(m)}
    sum_u = {i: 0 for i in range(m)}
    for rec in records:
        s = int.from_bytes(hashlib.sha256(f"e7-shard|{rec['sample_id']}".encode()).digest()[:8], "big") % m
        n_m[s] += 1
        es = rec["expected_input_safe"]
        safety = rec["safety"]
        refusal = rec["refusal"]
        if es is True:
            sum_u[s] += 1 if (safety == "Safe" and refusal == "No") else 0
        elif es is False:
            sum_u[s] += 1 if (safety == "Safe" or refusal == "Yes") else 0

    return {i: round(1_000_000 * sum_u[i] / n_m[i]) if n_m[i] > 0 else 500_000 for i in range(m)}


def load_score_artifact(score_file):
    """Load controlled evaluator-reliability scores with explicit semantic provenance."""
    payload = json.loads(pathlib.Path(score_file).read_text(encoding="utf-8"))
    if payload.get("schema") != "zte-sci-e7-evaluator-reliability-v1":
        raise ValueError("unsupported E7 score schema")
    if payload.get("reference_semantics") != "frozen_guard_operational_proxy":
        raise ValueError("E7 v2 scores must use the frozen Guard operational proxy")
    if payload.get("reference_is_human_ground_truth") is not False:
        raise ValueError("E7 v2 must not represent the proxy as human ground truth")
    raw_scores = payload.get("scores_ppm", {})
    expected_nodes = set(range(16))
    try:
        scores = {int(node_id): int(value) for node_id, value in raw_scores.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid E7 score map") from exc
    if set(scores) != expected_nodes:
        raise ValueError("E7 score map must contain exactly nodes 0 through 15")
    if any(value < 0 or value > 1_000_000 for value in scores.values()):
        raise ValueError("E7 scores must be integer PPM values in [0, 1000000]")
    provenance = {
        "score_schema": payload["schema"],
        "reference_semantics": payload["reference_semantics"],
        "reference_is_human_ground_truth": payload["reference_is_human_ground_truth"],
        "score_file": str(pathlib.Path(score_file).resolve()),
        "score_file_sha256": hashlib.sha256(pathlib.Path(score_file).read_bytes()).hexdigest(),
    }
    return scores, provenance


def register_all_evaluators(count=16):
    for i in range(count):
        eid = f"eval-{i:02d}"
        e_env = eval_env_for(i)
        args = json.dumps({"Args": ["RegisterEvaluator", json.dumps({
            "evalId": eid, "mspId": org_msp_for(i), "capabilities": ["text", "image"]
        })]})
        r = invoke(e_env, args)
        if r.returncode == 0:
            pass  # print(f"  Registered {eid}")
        elif "ERR_EVALUATOR_EXISTS" not in (r.stderr + r.stdout):
            err_short = (r.stderr + r.stdout).strip().split("\n")[-1][:200]
            print(f"  WARN: Register {eid} failed: {err_short}")
        else:
            print(f"  INFO: {eid} already exists")


def post_task(env, task_id, deadline=None):
    if deadline is None:
        deadline = int(time.time()) + 3600
    args = json.dumps({"Args": ["PostTaskConstraint", json.dumps({
        "taskId": task_id, "subjectId": "e7-subj",
        "riskCategories": ["toxic", "bias"], "modalities": ["text", "image"],
        "workload": 100, "deadlineUnix": deadline,
        "inputBytes": 1024, "priority": 1, "minEvaluators": 11,
        "minReputationPpm": 300000, "cid": "e7-cid", "sha256": "e" * 64,
    })]})
    r = invoke(env, args)
    return r.returncode == 0


def post_allocation(task_id, num_evaluators=16):
    members = []
    for i in range(num_evaluators):
        members.append({"evalId": f"eval-{i:02d}", "sharePpm": 1000000 // num_evaluators})
    total = (1000000 // num_evaluators) * num_evaluators
    members[-1]["sharePpm"] += (1000000 - total)
    args = json.dumps({"Args": ["PostAllocation", json.dumps({
        "taskId": task_id, "members": members
    })]})
    env = audit_env()
    r = invoke(env, args)
    if r.returncode != 0:
        print(f"  DEBUG PostAlloc stderr: {r.stderr[-300:]}")
    return r.returncode == 0


def post_review_decision(task_id, decision="Reject", reason=""):
    """Call PostReviewDecision on a task in Review state."""
    args = json.dumps({"Args": ["PostReviewDecision", task_id, decision, reason]})
    r = invoke(audit_env(), args)
    return r.returncode == 0, r.stderr.strip().split("\n")[-1][:200]


def query_confirmation(task_id):
    """Query confirmation status for a task."""
    args = json.dumps({"Args": ["QueryConfirmation", task_id]})
    r = query(admin_env(), args)
    if r.returncode == 0 and r.stdout.strip():
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            pass
    return None


def reset_network_for_fresh_run():
    """Restart Fabric network to clear world state between scenario groups.
    Uses the supported test-network restart script, not direct CouchDB manipulation."""
    net_dir = str(TDIR)
    subprocess.run(
        ["docker", "compose", "-f", net_dir + "/compose/compose-ca.yaml", "down"],
        capture_output=True, check=False
    )
    subprocess.run(
        ["docker", "compose", "-f", net_dir + "/compose/docker/docker-compose-test-net.yaml", "down"],
        capture_output=True, check=False
    )
    subprocess.run(
        ["docker", "compose", "-f", net_dir + "/addOrg3/compose/compose-ca-org3.yaml", "down"],
        capture_output=True, check=False
    )
    subprocess.run(
        ["docker", "compose", "-f", net_dir + "/addOrg3/compose/docker/docker-compose-couch-org3.yaml", "down"],
        capture_output=True, check=False
    )
    subprocess.run(
        ["docker", "compose", "-f", net_dir + "/addOrg3/compose/docker/docker-compose-org3.yaml", "down"],
        capture_output=True, check=False
    )


def build_snapshot_input(task_id, deadline, scores, modifier=None):
    """Build PostEvalSnapshot input with optional score modifier."""
    eval_items = []
    evidence_refs = []
    for i in range(16):
        eid = f"eval-{i:02d}"
        score = scores.get(i, 500000)
        verdict = "OK"

        # Apply modifier (for S1/S2 attack scenarios)
        if modifier and i in modifier.get("eval_ids", []):
            score = modifier.get("score", score)
            verdict = modifier.get("verdict", verdict)

        eval_items.append({"evalId": eid, "scorePpm": score, "verdict": verdict})

        # Query evaluator using admin identity (queries don't need evaluator's own MSP)
        q_args = json.dumps({"Args": ["QueryEvaluator", eid]})
        qr = query(admin_env(), q_args)
        ev_state = {}
        if qr.returncode == 0 and qr.stdout.strip():
            try:
                ev_state = json.loads(qr.stdout)
            except json.JSONDecodeError:
                pass

        evidence_refs.append({
            "evalId": eid, "taskId": task_id,
            "cid": "e7-cid", "sha256": "e" * 64,
            "submitterClientId": ev_state.get("clientId", ""),
            "submitterMspId": ev_state.get("mspId", ""),
        })

    return {
        "taskId": task_id,
        "deadlineUnix": deadline,
        "evalItems": eval_items,
        "evidenceRefs": evidence_refs,
    }


def post_snapshot(task_id, deadline, scores, modifier=None):
    """Post eval snapshot and return parsed Confirmation with digest."""
    snap_input = build_snapshot_input(task_id, deadline, scores, modifier)
    args = json.dumps({"Args": ["PostEvalSnapshot", json.dumps(snap_input)]})
    r = invoke(audit_env(), args)
    if r.returncode != 0:
        return None, r.stderr.strip().split("\n")[-1][:200]
    return parse_invoke_payload(r.stdout, r.stderr), None


def submit_votes(task_id, digest, vote_config=None):
    """Submit votes per config: {eval_id: vote_type} or default all ACK.
    Org3 evaluators (eval-11..15) vote first to verify cross-org voting before quorum.
    Returns (ack_count, obj_count, object_eval_ids, vote_records).
    Each vote_record: {evaluator_id, msp_id, cert_sha256, vote, tx_id, return_code, timestamp_utc}.
    """
    if vote_config is None:
        vote_config = {i: "ACK" for i in range(16)}

    ack_count, obj_count = 0, 0
    object_eval_ids = []
    vote_records = []
    ordered_ids = list(range(11, 16)) + list(range(0, 11))
    for i in ordered_ids:
        vote_type = vote_config.get(i)
        if vote_type is None:
            continue

        eid = f"eval-{i:02d}"
        voter_env = eval_env_for(i)
        # Read cert fingerprint for this evaluator
        org_dir = "org1.example.com" if i <= 5 else ("org2.example.com" if i <= 10 else "org3.example.com")
        signcert_dir = TDIR / "organizations" / "peerOrganizations" / org_dir / "users" / eid / "msp" / "signcerts"
        certs = list(signcert_dir.glob("*.pem")) if signcert_dir.exists() else []
        cert_fp = hashlib.sha256(certs[0].read_bytes()).hexdigest() if certs else ""
        msp_id = "Org1MSP" if i <= 5 else ("Org2MSP" if i <= 10 else "Org3MSP")

        args = json.dumps({"Args": ["SubmitVote", task_id, digest, vote_type]})
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record = {
            "evaluator_id": eid, "node_id": i, "msp_id": msp_id,
            "expected_msp_id": msp_id,
            "cert_sha256": cert_fp, "vote": vote_type, "timestamp_utc": ts,
        }
        try:
            audit_reader_env = admin_env()
            height_before = channel_height(audit_reader_env)
            r = invoke(voter_env, args, timeout=60)
        except Exception as e:
            record["return_code"] = -1
            record["error"] = str(e)
            print(f"  VOTE_EXCEPTION eval-{i:02d}: {e}")
            vote_records.append(record)
            continue
        record["return_code"] = r.returncode
        record["tx_id"] = extract_txid(r.stdout, r.stderr) or ""
        if r.returncode == 0:
            if not record["tx_id"]:
                new_txids = committed_txids_since(audit_reader_env, height_before)
                if not new_txids:
                    raise RuntimeError(f"successful vote for {eid} has no committed transaction ID")
                record["tx_id"] = new_txids[-1]
            record["success"] = True
            if vote_type == "ACK":
                ack_count += 1
            else:
                obj_count += 1
                object_eval_ids.append(i)
        else:
            record["success"] = False
            err = r.stderr + r.stdout
            err_short = err.strip().split("\n")[-1][:200] if err.strip() else "no output"
            record["error_tail"] = err_short
            print(f"  VOTE_RET eval-{i:02d} rc={r.returncode}: {err_short}")
            if "ERR_CONFIRMATION_FINAL" in err:
                record["quorum_already_reached"] = True
        vote_records.append(record)
    return ack_count, obj_count, object_eval_ids, vote_records


def finalize_confirmation(task_id):
    args = json.dumps({"Args": ["FinalizeConfirmation", task_id]})
    r = invoke(admin_env(), args)
    return r.returncode == 0, r.stderr.strip().split("\n")[-1][:200]


def process_settlement(task_id):
    args = json.dumps({"Args": ["ProcessSettlement", task_id]})
    r = invoke(audit_env(), args)
    return r.returncode == 0, r.stderr.strip().split("\n")[-1][:200]


def query_evaluators(count=16):
    """Query all evaluators via admin env."""
    states = []
    for i in range(count):
        args = json.dumps({"Args": ["QueryEvaluator", f"eval-{i:02d}"]})
        r = query(admin_env(), args)
        if r.returncode == 0 and r.stdout.strip():
            try:
                states.append(json.loads(r.stdout))
            except json.JSONDecodeError:
                pass
    return states


def build_reputation_order(states):
    entries = []
    for s in states:
        eid = s.get("evalId", "")
        try:
            node_id = int(eid.split("-")[-1])
        except:
            node_id = 0
        entries.append((s.get("reputationPpm", 0), node_id))
    entries.sort(key=lambda x: (-x[0], x[1]))
    return [e[1] for e in entries]


def extract_txid(stdout, stderr):
    """Extract transaction ID from Fabric peer CLI output."""
    import re
    combined = stdout + stderr
    m = re.search(r'\[txid:\s*([0-9a-fA-F]+)\]', combined)
    if m:
        return m.group(1)
    m = re.search(r'txid[=:]\s*"?([0-9a-fA-F]+)', combined)
    if m:
        return m.group(1)
    return None


def qualify_evaluator_identities(count=16):
    """Verify each evaluator's on-chain registration: query-verified and transaction-capable are separate.
    query_verified=true means chaincode QueryEvaluator returned the registration.
    transaction_capable is only set when we have actual evidence of successful invoke (from formal runs).
    Returns list of {eval_id, node_id, client_id, msp_id, cert_sha256, query_verified}."""
    results = []
    for i in range(count):
        eid = f"eval-{i:02d}"
        q_env = eval_env_for(i)
        q_args = json.dumps({"Args": ["QueryEvaluator", eid]})
        r = query(q_env, q_args)
        entry = {"eval_id": eid, "node_id": i, "query_verified": False, "transaction_capable": False}
        if r.returncode == 0 and r.stdout.strip():
            try:
                ev = json.loads(r.stdout)
                entry["client_id"] = ev.get("clientId", "")
                entry["msp_id"] = ev.get("mspId", "")
                org_dir = "org1.example.com" if i <= 5 else ("org2.example.com" if i <= 10 else "org3.example.com")
                signcert_dir = TDIR / "organizations" / "peerOrganizations" / org_dir / "users" / eid / "msp" / "signcerts"
                certs = list(signcert_dir.glob("*.pem")) if signcert_dir.exists() else []
                if certs:
                    cert_bytes = certs[0].read_bytes()
                    entry["cert_sha256"] = hashlib.sha256(cert_bytes).hexdigest()
                entry["query_verified"] = True
            except json.JSONDecodeError:
                entry["error"] = "query parse failed"
        else:
            entry["error"] = "query failed: " + (r.stderr[:100] if r.stderr else "no output")
        results.append(entry)

    client_ids = [e.get("client_id") for e in results if e.get("client_id")]
    cert_hashes = [e.get("cert_sha256") for e in results if e.get("cert_sha256")]
    unique_clients = len(set(client_ids))
    unique_certs = len(set(cert_hashes))
    query_ok = sum(1 for e in results if e["query_verified"])
    print(f"  Identity query-verified: {query_ok}/{count}")
    print(f"  Unique ClientIDs: {unique_clients}/{count}")
    print(f"  Unique certs: {unique_certs}/{count}")
    if unique_clients < count or unique_certs < count:
        print("  STOP-GATE: Non-unique evaluator identities detected!")
    return results, query_ok == count and unique_clients == count and unique_certs == count


def ensure_evaluators_registered():
    """Ensure all 16 evaluators exist on ledger."""
    args = json.dumps({"Args": ["QueryEvaluator", "eval-00"]})
    r = query(admin_env(), args)
    if r.returncode != 0 or not r.stdout.strip():
        register_all_evaluators()
    else:
        # Check all exist
        for i in range(16):
            args = json.dumps({"Args": ["QueryEvaluator", f"eval-{i:02d}"]})
            r = query(admin_env(), args)
            if r.returncode != 0 or not r.stdout.strip():
                register_all_evaluators()
                break


def new_task_suffix(repeat, clock_ns=time.time_ns):
    """Return a collision-resistant, auditable suffix for a formal task ID."""
    return f"{clock_ns()}-r{repeat}"


def scenario_deadline_seconds(scenario_id):
    # Ten committed votes take roughly 40 seconds because each transaction is
    # followed by a block lookup. Keep enough margin for Fabric host jitter.
    return 90 if scenario_id == "E7-S3" else 7200


def validate_e7_results(all_results):
    """Validate formal-run counts and scenario-specific protocol outcomes."""
    expected = {"E7-S0": 5, "E7-S1": 3, "E7-S2": 3,
                "E7-S3": 3, "E7-S4": 3, "E7-S5": 5}
    errors = []
    if len(all_results) != sum(expected.values()):
        errors.append(f"expected 22 formal runs, got {len(all_results)}")

    task_ids = [r.get("task_id", "") for r in all_results]
    if any(not task_id for task_id in task_ids):
        errors.append("one or more formal runs have an empty task_id")
    if len(task_ids) != len(set(task_ids)):
        errors.append("duplicate task_id in formal results")

    for scenario, count in expected.items():
        runs = [r for r in all_results if r.get("scenario") == scenario]
        if len(runs) != count:
            errors.append(f"{scenario}: expected {count} runs, got {len(runs)}")
        repeats = sorted(r.get("repeat") for r in runs)
        if repeats != list(range(count)):
            errors.append(f"{scenario}: invalid repeat indices {repeats}")

    for result in all_results:
        scenario = result.get("scenario", "unknown")
        repeat = result.get("repeat", "unknown")
        prefix = f"{scenario} repeat {repeat}"
        if result.get("error"):
            errors.append(f"{prefix}: {result['error']}")
            continue
        if scenario in ("E7-S0", "E7-S5"):
            if result.get("ack_count") != 11:
                errors.append(f"{prefix}: expected 11 ACK votes")
            if result.get("finalize_ok") is not True or result.get("settlement_ok") is not True:
                errors.append(f"{prefix}: finalization or settlement failed")
        elif scenario in ("E7-S1", "E7-S2"):
            if result.get("object_count", 0) < 1:
                errors.append(f"{prefix}: no OBJECT vote was committed")
            if result.get("review_decision_ok") is not True or result.get("final_confirmation_status") != "Reject":
                errors.append(f"{prefix}: review rejection did not complete")
        elif scenario == "E7-S3":
            if result.get("ack_count") != 10:
                errors.append(f"{prefix}: expected 10 sub-quorum ACK votes")
            if result.get("post_timeout_status") != "Review" or result.get("review_decision_ok") is not True:
                errors.append(f"{prefix}: timeout review path did not complete")
        elif scenario == "E7-S4":
            if result.get("settlement_1_ok") is not True or result.get("settlement_2_ok") is not False:
                errors.append(f"{prefix}: settlement replay gate failed")
    return errors


def run_scenario(scenario_id, scores, repeat=0, task_suffix=None, score_provenance=None):
    """Run a single scenario repeat.

    Scenario types:
      S0/S5: Full flow, all ACK, settle
      S1: Low-score attack, attack nodes OBJECT -> Review
      S2: High-score attack, attack nodes OBJECT -> Review
      S3: Sub-quorum ACK -> stays Pending -> timeout -> Review -> Reject
      S4: Double settlement test
    """
    suffix = task_suffix or new_task_suffix(repeat)
    task_id = f"{scenario_id.lower()}-{suffix}"
    deadline = int(time.time()) + scenario_deadline_seconds(scenario_id)
    result = {"scenario": scenario_id, "task_id": task_id, "repeat": repeat,
              "deadline": deadline}
    if score_provenance:
        result.update({
            "score_schema": score_provenance["score_schema"],
            "score_reference_semantics": score_provenance["reference_semantics"],
            "score_reference_is_human_ground_truth": score_provenance["reference_is_human_ground_truth"],
            "score_file_sha256": score_provenance["score_file_sha256"],
        })

    print(f"\n{'='*60}")
    print(f"{scenario_id} task={task_id} repeat={repeat}")
    print(f"{'='*60}")

    ensure_evaluators_registered()

    # Phase 1: Setup (common to all scenarios)
    if not post_task(admin_env(), task_id, deadline):
        result["error"] = "PostTaskConstraint failed"
        print(f"  ERROR: {result['error']}")
        return result

    if not post_allocation(task_id):
        result["error"] = "PostAllocation failed"
        print(f"  ERROR: {result['error']}")
        return result

    # Build scenario-specific snapshot modifier
    modifier = None
    vote_config = {}
    if scenario_id == "E7-S1":
        # Low-score tampered snapshot on eval-00, eval-01
        modifier = {"eval_ids": [0, 1], "score": 100000, "verdict": "OK"}
        # Attack nodes OBJECT immediately; others don't need to vote
        vote_config = {0: "OBJECT", 1: "OBJECT"}
    elif scenario_id == "E7-S2":
        # High-score ballot stuffing
        modifier = {"eval_ids": [0, 1], "score": 1000000, "verdict": "OK"}
        vote_config = {0: "OBJECT", 1: "OBJECT"}
    elif scenario_id == "E7-S3":
        # Sub-quorum ACK scenario: only 10 evaluators vote, quorum (11) not reached
        for i in range(10):
            vote_config[i] = "ACK"
        # eval-10 through eval-15: no vote (None)
    else:
        # S0, S4, S5: all ACK
        for i in range(16):
            vote_config[i] = "ACK"

    # Phase 2: Post snapshot
    confirmation, err = post_snapshot(task_id, deadline, scores, modifier)
    if confirmation is None:
        result["error"] = f"PostEvalSnapshot: {err}"
        print(f"  ERROR: {result['error']}")
        return result
    digest = confirmation.get("digest", "")
    print(f"  Digest: {digest[:16]}...  Status: {confirmation.get('status')}")

    # Phase 3: Submit votes
    ack_count, obj_count, object_ids, vote_records = submit_votes(task_id, digest, vote_config)
    quorum = (2 * 16 + 2) // 3  # ceil(2n/3) = 11 for n=16
    print(f"  Votes: ACK={ack_count} OBJECT={obj_count} (quorum={quorum})")

    # Phase 4: Post-vote actions based on scenario
    result["ack_count"] = ack_count
    result["object_count"] = obj_count
    result["object_eval_ids"] = object_ids
    result["quorum"] = quorum
    result["digest"] = digest
    result["vote_records"] = vote_records

    if scenario_id == "E7-S4":
        # First settlement (should succeed)
        ok1, msg1 = process_settlement(task_id)
        result["settlement_1_ok"] = ok1
        result["settlement_1_msg"] = msg1
        # Second settlement (should fail - replay rejection)
        ok2, msg2 = process_settlement(task_id)
        result["settlement_2_ok"] = ok2
        result["settlement_2_msg"] = msg2
        print(f"  Settlement 1: {'OK' if ok1 else msg1[:80]}")
        print(f"  Settlement 2 (replay): {'OK' if ok2 else msg2[:80]}")
    elif scenario_id in ("E7-S1", "E7-S2"):
        # OBJECT scenarios: after OBJECT, confirmation moves to Review
        # Must call PostReviewDecision("Reject") to properly close the state machine
        cf = query_confirmation(task_id)
        cf_status = cf.get("status", "") if cf else ""
        print(f"  Confirmation status: {cf_status}")
        if cf_status == "Review":
            ok_rd, msg_rd = post_review_decision(task_id, "Reject", "attack detected via OBJECT")
            result["review_decision_ok"] = ok_rd
            result["review_decision_msg"] = msg_rd
            print(f"  PostReviewDecision(Reject): {'OK' if ok_rd else msg_rd[:80]}")
            # Verify final status is Reject
            cf2 = query_confirmation(task_id)
            result["final_confirmation_status"] = cf2.get("status", "") if cf2 else ""
            print(f"  Final status: {result['final_confirmation_status']}")
        else:
            result["review_decision_note"] = f"unexpected status: {cf_status}, expected Review"
            print(f"  WARNING: expected Review status, got {cf_status}")
    elif scenario_id == "E7-S3":
        # Only 10 ACKs, quorum not met, stays Pending
        # Use short deadline to verify timeout behavior
        cf = query_confirmation(task_id)
        cf_status = cf.get("status", "") if cf else ""
        result["pending_status"] = cf_status
        print(f"  Pre-timeout status: {cf_status}")
        if cf_status == "Pending":
            # Wait for deadline to expire
            wait_sec = max(0, deadline - int(time.time()) + 5)
            if wait_sec > 0:
                print(f"  Waiting {wait_sec}s for deadline expiry...")
                time.sleep(wait_sec)
            ok_f, msg_f = finalize_confirmation(task_id)
            result["timeout_finalize_ok"] = ok_f
            result["timeout_finalize_msg"] = msg_f
            print(f"  Finalize after timeout: {'OK' if ok_f else msg_f[:80]}")
            cf2 = query_confirmation(task_id)
            result["post_timeout_status"] = cf2.get("status", "") if cf2 else ""
            print(f"  Post-timeout status: {result['post_timeout_status']}")
            # Release locks via PostReviewDecision (same as S1/S2)
            if cf2.get("status") == "Review":
                ok_rd, msg_rd = post_review_decision(task_id, "Reject", "timeout - insufficient quorum")
                result["review_decision_ok"] = ok_rd
                result["review_decision_msg"] = msg_rd
                print(f"  PostReviewDecision(Reject): {'OK' if ok_rd else msg_rd[:80]}")
        else:
            result["pending_note"] = f"unexpected pre-timeout status: {cf_status}"
    else:
        # S0, S5: Full flow with finalize + settlement
        ok_f, msg_f = finalize_confirmation(task_id)
        result["finalize_ok"] = ok_f
        result["finalize_msg"] = msg_f
        ok_s, msg_s = process_settlement(task_id)
        result["settlement_ok"] = ok_s
        result["settlement_msg"] = msg_s
        print(f"  Finalize: {'OK' if ok_f else msg_f[:80]}")
        print(f"  Settlement: {'OK' if ok_s else msg_s[:80]}")

    # Phase 5: Query evaluator states
    states = query_evaluators()
    if states:
        rep_order = build_reputation_order(states)
        group_map, leaders, l_gl = build_group_map(rep_order, 4)
        result["evaluator_count"] = len(states)
        result["reputation_order"] = rep_order
        result["group_leaders"] = {str(k): v for k, v in leaders.items()}
        result["l_gl"] = list(l_gl)
        result["reputations"] = {s.get("evalId", ""): s.get("reputationPpm", 0) for s in states}
        print(f"  Rep order: {rep_order}")
        print(f"  L_GL: {list(l_gl)}")

    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result


def sha256_file(path):
    """SHA-256 hash of a file's contents."""
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def generate_identity_binding(identity_results):
    """Generate identity_binding.json: eval_id -> MSP -> cert SHA-256 -> RGG node_id -> Ed25519 pubkey SHA-256.
    Also includes Ed25519 private key hash for completeness (mapping verification).
    """
    from protocol import public_key as ed25519_public_key
    binding = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "description": "16 evaluator identity binding: Fabric cert <-> RGG Ed25519 key",
               "identities": []}
    for entry in identity_results:
        node_id = entry["node_id"]
        pk = ed25519_public_key(node_id)
        pk_sha256 = hashlib.sha256(pk.public_bytes_raw()).hexdigest()
        binding["identities"].append({
            "eval_id": entry["eval_id"],
            "node_id": node_id,
            "msp_id": entry.get("msp_id", ""),
            "cert_sha256": entry.get("cert_sha256", ""),
            "ed25519_pubkey_sha256": pk_sha256,
            "client_id": entry.get("client_id", ""),
            "query_verified": entry.get("query_verified", False),
        })
    # Verify uniqueness
    node_ids = [i["node_id"] for i in binding["identities"]]
    certs = [i["cert_sha256"] for i in binding["identities"]]
    pubkeys = [i["ed25519_pubkey_sha256"] for i in binding["identities"]]
    binding["uniqueness"] = {
        "eval_ids": len(set(b["eval_id"] for b in binding["identities"])) == 16,
        "node_ids": len(set(node_ids)) == 16,
        "cert_sha256": len(set(certs)) == 16 and all(c != "" for c in certs),
        "ed25519_pubkey_sha256": len(set(pubkeys)) == 16 and all(p != "" for p in pubkeys),
    }
    binding["validation_errors"] = validate_identity_audit(binding["identities"], expected_count=16)
    binding["valid"] = not binding["validation_errors"]
    return binding


def validate_identity_audit(identities, expected_count=16):
    errors = []
    if len(identities) != expected_count:
        errors.append(f"identity count: expected {expected_count}, got {len(identities)}")
    required = (
        "eval_id", "node_id", "msp_id", "cert_sha256",
        "ed25519_pubkey_sha256", "client_id",
    )
    for index, identity in enumerate(identities):
        for field in required:
            if identity.get(field) in (None, ""):
                errors.append(f"identity {index} missing {field}")
        if identity.get("query_verified") is not True:
            errors.append(f"identity {index} is not query verified")
    for field in ("eval_id", "node_id", "cert_sha256", "ed25519_pubkey_sha256", "client_id"):
        values = [identity.get(field) for identity in identities]
        if len(values) != len(set(values)):
            errors.append(f"identity field is not unique: {field}")
    return errors


def generate_identity_qualification(identity_results, all_results):
    """Generate identity_qualification.json: identity query results + actual successful vote submissions.
    transaction_capable is derived from actual successful chaincode invoke in formal S0/S5 runs.
    """
    qualification = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "description": "Query verification + transaction capability evidence from formal runs",
                     "evaluators": []}

    # Collect successful vote submissions from S0/S5
    successful_voters = set()
    voter_tx_map = {}
    for r in all_results:
        if r.get("scenario") in ("E7-S0", "E7-S5"):
            for vr in r.get("vote_records", []):
                if vr.get("success") and re.fullmatch(r"[0-9a-fA-F]{64}", vr.get("tx_id", "")):
                    eid = vr["evaluator_id"]
                    successful_voters.add(eid)
                    if eid not in voter_tx_map:
                        voter_tx_map[eid] = []
                    voter_tx_map[eid].append({
                        "tx_id": vr.get("tx_id", ""),
                        "scenario": r["scenario"],
                        "task_id": r.get("task_id", ""),
                        "vote": vr["vote"],
                    })

    for entry in identity_results:
        eid = entry["eval_id"]
        entry_out = dict(entry)
        entry_out["transaction_capable"] = eid in successful_voters
        if eid in voter_tx_map:
            entry_out["transaction_evidence"] = voter_tx_map[eid][:3]  # up to 3 samples
        qualification["evaluators"].append(entry_out)

    total = len(qualification["evaluators"])
    query_ok = sum(1 for e in qualification["evaluators"] if e.get("query_verified"))
    tx_ok = sum(1 for e in qualification["evaluators"] if e.get("transaction_capable"))
    qualification["summary"] = {
        "total": total, "query_verified": query_ok, "transaction_capable": tx_ok,
        "org3_transaction_capable": sum(1 for e in qualification["evaluators"]
                                        if e.get("transaction_capable") and "Org3" in str(e.get("msp_id", ""))),
    }
    return qualification


def write_e7_evidence(output_dir, all_results):
    output_dir = pathlib.Path(output_dir)
    identity_results, identity_ok = qualify_evaluator_identities(16)
    binding = generate_identity_binding(identity_results)
    (output_dir / "identity_binding.json").write_text(json.dumps(binding, indent=2))

    qualification = generate_identity_qualification(identity_results, all_results)
    (output_dir / "identity_qualification.json").write_text(json.dumps(qualification, indent=2))

    org3_evidence = []
    for result in all_results:
        if result.get("scenario") not in ("E7-S0", "E7-S5"):
            continue
        for vote in result.get("vote_records", []):
            if (
                vote.get("success")
                and vote.get("expected_msp_id") == "Org3MSP"
                and re.fullmatch(r"[0-9a-fA-F]{64}", vote.get("tx_id", ""))
            ):
                org3_evidence.append({
                    "scenario": result["scenario"],
                    "task_id": result.get("task_id", ""),
                    "evaluator_id": vote["evaluator_id"],
                    "msp_id": vote.get("msp_id", vote.get("expected_msp_id", "")),
                    "vote": vote["vote"],
                    "tx_id": vote["tx_id"],
                    "cert_sha256": vote.get("cert_sha256", ""),
                    "timestamp_utc": vote.get("timestamp_utc", ""),
                })
    (output_dir / "org3_vote_evidence.json").write_text(json.dumps(org3_evidence, indent=2))

    env_snap = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "chaincode_name": CHAINCODE_NAME,
        "peer1_tls_fingerprint": sha256_file(str(PEER1_TLS)) if PEER1_TLS.exists() else "",
        "peer2_tls_fingerprint": sha256_file(str(PEER2_TLS)) if PEER2_TLS.exists() else "",
        "orderer_ca_fingerprint": sha256_file(str(ORDERER_CA)) if ORDERER_CA.exists() else "",
    }
    (output_dir / "environment.json").write_text(json.dumps(env_snap, indent=2))

    errors = list(binding.get("validation_errors", []))
    errors.extend(validate_e7_results(all_results))
    if not identity_ok:
        errors.append("identity query or uniqueness stop gate failed")
    org3_ids = {entry["evaluator_id"] for entry in org3_evidence}
    expected_org3 = {f"eval-{i:02d}" for i in range(11, 16)}
    if org3_ids != expected_org3:
        errors.append(f"Org3 transaction evidence incomplete: {sorted(org3_ids)}")

    status = {
        "stage": "E7-Fabric",
        "state": "completed" if not errors else "failed",
        "runs": len(all_results),
        "errors": errors,
        "identity_query_verified": qualification["summary"]["query_verified"],
        "identity_transaction_capable": qualification["summary"]["transaction_capable"],
        "org3_transaction_capable": qualification["summary"]["org3_transaction_capable"],
    }
    (output_dir / "status.json").write_text(json.dumps(status, indent=2))

    output_files = sorted(f.name for f in output_dir.glob("*.json"))
    checksum_lines = [
        f"{hashlib.sha256((output_dir / name).read_bytes()).hexdigest()}  {name}"
        for name in output_files
    ]
    (output_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n")
    if errors:
        raise RuntimeError("E7 evidence stop gate failed: " + "; ".join(errors))
    return binding, qualification, org3_evidence


def run_all_scenarios(score_file, output_dir):
    config_path = HERE / "configs" / "e7_cross_layer.json"
    with open(config_path) as f:
        config = json.load(f)

    scores, score_provenance = load_score_artifact(score_file)
    output_dir = pathlib.Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results = []
    for scenario in config["scenarios"]:
        sid = scenario["id"]
        repeats = scenario["repeats"]
        for r in range(repeats):
            result = run_scenario(sid, scores, repeat=r, score_provenance=score_provenance)
            all_results.append(result)
            # Save individual result
            fname = f"{sid.lower()}-r{r:02d}-{result.get('task_id', 'unknown')}.json"
            (output_dir / fname).write_text(json.dumps(result, indent=2))

    # Save aggregate
    agg_path = output_dir / "e7_all_results.json"
    agg_path.write_text(json.dumps(all_results, indent=2))

    binding, qualification, org3_evidence = write_e7_evidence(output_dir, all_results)

    print(f"\n{'='*60}")
    print(f"All {len(all_results)} runs complete. Results: {agg_path}")
    print(f"Identity binding: {output_dir / 'identity_binding.json'}")
    print(f"Identity qualification: {output_dir / 'identity_qualification.json'}")
    print(f"Org3 vote evidence: {len(org3_evidence)} records")
    print(f"{'='*60}")

    # Summary
    for scenario in config["scenarios"]:
        sid = scenario["id"]
        runs = [r for r in all_results if r["scenario"] == sid]
        errors = [r for r in runs if "error" in r]
        print(f"  {sid}: {len(runs)} runs, {len(errors)} errors")
        for e in errors:
            print(f"    r{e.get('repeat')}: {e.get('error')}")

    return all_results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="E7 Fabric cross-layer experiment runner")
    parser.add_argument("--scenario", choices=["S0", "S1", "S2", "S3", "S4", "S5"], help="Run single scenario")
    parser.add_argument("--repeat", type=int, default=0, help="Repeat index")
    parser.add_argument("--all", action="store_true", help="Run all scenarios")
    parser.add_argument("--register-only", action="store_true", help="Only register evaluators")
    parser.add_argument("--refresh-evidence", action="store_true", help="Rebuild identity and transaction evidence from live Fabric and existing formal results")
    parser.add_argument("--score-file", type=pathlib.Path, help="Controlled evaluator-reliability scores.json")
    parser.add_argument("--output-dir", type=pathlib.Path, help="Fresh output directory for E7 v2 Fabric results")
    args = parser.parse_args()

    if args.register_only:
        register_all_evaluators()
        print("All 16 evaluators registered")
        return

    if args.refresh_evidence:
        output_dir = ROOT / "results" / "e7-fabric"
        all_results = json.loads((output_dir / "e7_all_results.json").read_text())
        write_e7_evidence(output_dir, all_results)
        print(f"E7 evidence refreshed: {output_dir}")
        return

    if not args.score_file:
        parser.error("--score-file is required for formal E7 execution")
    scores, score_provenance = load_score_artifact(args.score_file)

    if args.scenario:
        sid = f"E7-{args.scenario}"
        result = run_scenario(sid, scores, repeat=args.repeat, score_provenance=score_provenance)
        output_dir = (args.output_dir or (ROOT / "results" / "e7-v2-fabric-single")).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{sid.lower()}-r{args.repeat:02d}-{result.get('task_id', 'unknown')}.json"
        (output_dir / fname).write_text(json.dumps(result, indent=2))
        # Append to aggregate
        agg_path = output_dir / "e7_all_results.json"
        existing = json.loads(agg_path.read_text()) if agg_path.exists() else []
        # Replace existing same-scenario-same-repeat entries
        existing = [r for r in existing if not (r.get("scenario") == sid and r.get("repeat") == args.repeat)]
        existing.append(result)
        agg_path.write_text(json.dumps(existing, indent=2))
        print(json.dumps(result, indent=2))
    elif args.all:
        if not args.output_dir:
            parser.error("--output-dir is required with --all")
        run_all_scenarios(args.score_file, args.output_dir)
    else:
        # Default: run S0 once
        result = run_scenario("E7-S0", scores, repeat=0, score_provenance=score_provenance)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
