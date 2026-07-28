from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mj5_common import read_json
from mj5_fabric_client import FabricCommandError, MJ5FabricClient
from mj5_rgg_bridge import extract_protocol_certificates


CORE_CONTAINERS = (
    "couchdb0",
    "couchdb1",
    "orderer.example.com",
    "peer0.org1.example.com",
    "peer0.org2.example.com",
)


def docker_containers(action: str, *, include_ipfs: bool = True) -> None:
    containers = list(CORE_CONTAINERS)
    if include_ipfs:
        containers.append("ipfs-kubo")
    subprocess.run(
        ["docker", action, *containers],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    if action == "start":
        time.sleep(8)


def negative_freeze(
    client: MJ5FabricClient,
    base_record: dict[str, Any],
    cid: str,
    judge_ids: dict[str, str],
    decision_id: str,
    mutation: str,
    expected_error: str,
) -> dict[str, Any]:
    outputs = client.signed_outputs(base_record, decision_id, judge_ids)
    if mutation == "label":
        outputs[0]["label"] = (
            "safe" if outputs[0]["label"] == "unsafe" else "unsafe"
        )
    elif mutation == "identity":
        outputs[0]["judgeId"] = outputs[1]["judgeId"]
    elif mutation == "policy":
        outputs[0]["policySha256"] = "f" * 64
    else:
        raise ValueError(mutation)
    payload = {
        "decisionId": decision_id,
        "sampleId": base_record["sample_id"],
        "evidenceCid": cid,
        "evidenceSha256": base_record["evidence_sha256"],
        "judgeOutputs": outputs,
        "provisionalLabel": base_record["provisional_label"],
        "posteriorUnsafePpm": base_record["posterior_unsafe_ppm"],
        "committeeQuorum": 2,
        "certificateQuorum": 3,
        "leaderValidatorIds": [
            f"validator-{node_id:02d}"
            for node_id in client.registrations["validator_order"][:4]
        ],
        "deadlineUnix": int(time.time()) + 3600,
    }
    latency, _ = client.invoke(
        "FreezeDecisionSnapshot",
        json.dumps(payload, separators=(",", ":")),
        expect_error=expected_error,
    )
    return {
        "case": mutation,
        "expected_error": expected_error,
        "passed": True,
        "latency_ms": latency,
    }


def run(args: argparse.Namespace) -> None:
    repo_root = args.repo_root.resolve()
    workload_dir = args.workload.resolve()
    output_root = args.output.resolve()
    run_id = (
        f"mj5-smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        f"-i{args.record_index}"
    )
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "schema": "mj5-cross-layer-smoke-v1",
        "run_id": run_id,
        "started_at_unix": int(time.time()),
        "negative_cases": [],
    }

    client = MJ5FabricClient(repo_root, workload_dir)
    judge_ids = client.ensure_registrations(f"{run_id}::")
    manifest = read_json(workload_dir / "j3_manifest.json")
    records = manifest["records"]
    if args.record_index < 0 or args.record_index >= len(records):
        raise ValueError(
            f"record index {args.record_index} is outside 0..{len(records) - 1}"
        )
    record = records[args.record_index]
    bundle_path = workload_dir / record["bundle_path"]
    bundle = bundle_path.read_bytes()
    cid, ipfs_ms = client.ipfs_add(bundle)
    if client.ipfs_cat(cid) != bundle:
        raise RuntimeError("IPFS content verification failed")
    report["ipfs"] = {
        "cid": cid,
        "sha256": hashlib.sha256(bundle).hexdigest(),
        "bytes": len(bundle),
        "add_latency_ms": ipfs_ms,
        "verified": True,
    }

    decision_id = f"{run_id}::main"
    snapshot, freeze_ms = client.freeze(
        record,
        decision_id,
        cid,
        judge_ids,
        int(time.time()) + 3600,
    )
    vote_ms = []
    for node_id in client.registrations["validator_order"][:2]:
        vote_ms.append(client.submit_vote(snapshot, node_id, "ACK"))
        snapshot = client.query("QueryDecisionSnapshot", decision_id)
    if snapshot["status"] != "CommitteeConfirmed":
        raise RuntimeError(f"unexpected confirmation state {snapshot['status']}")
    report["fabric_confirmation"] = {
        "decision_id": decision_id,
        "decision_digest": snapshot["decisionDigest"],
        "status": snapshot["status"],
        "freeze_latency_ms": freeze_ms,
        "vote_latency_ms": vote_ms,
        "frozen_reliability_versions": [
            item["version"] for item in snapshot["frozenReliabilities"]
        ],
    }

    _, _ = client.invoke(
        "FreezeDecisionSnapshot",
        json.dumps(
            {
                "decisionId": decision_id,
                "sampleId": record["sample_id"],
                "evidenceCid": cid,
                "evidenceSha256": record["evidence_sha256"],
                "judgeOutputs": client.signed_outputs(
                    record, decision_id, judge_ids
                ),
                "provisionalLabel": record["provisional_label"],
                "posteriorUnsafePpm": record["posterior_unsafe_ppm"],
                "committeeQuorum": 2,
                "certificateQuorum": 3,
                "leaderValidatorIds": [
                    f"validator-{node_id:02d}"
                    for node_id in client.registrations["validator_order"][:4]
                ],
                "deadlineUnix": int(time.time()) + 3600,
            },
            separators=(",", ":"),
        ),
        expect_error="ERR_DECISION_EXISTS",
    )
    report["negative_cases"].append(
        {"case": "replay", "expected_error": "ERR_DECISION_EXISTS", "passed": True}
    )
    report["negative_cases"].extend(
        [
            negative_freeze(
                client,
                record,
                cid,
                judge_ids,
                f"{run_id}::tamper-label",
                "label",
                "ERR_JUDGE_SIGNATURE",
            ),
            negative_freeze(
                client,
                record,
                cid,
                judge_ids,
                f"{run_id}::tamper-identity",
                "identity",
                "ERR_ADAPTER_VERSION_MISMATCH",
            ),
            negative_freeze(
                client,
                record,
                cid,
                judge_ids,
                f"{run_id}::tamper-policy",
                "policy",
                "ERR_POLICY_HASH_MISMATCH",
            ),
        ]
    )

    review_id = f"{run_id}::review"
    review_snapshot, _ = client.freeze(
        record, review_id, cid, judge_ids, int(time.time()) + 3600
    )
    client.submit_vote(
        review_snapshot, client.registrations["validator_order"][0], "OBJECT"
    )
    review_snapshot = client.query("QueryDecisionSnapshot", review_id)
    if review_snapshot["status"] != "Review":
        raise RuntimeError("OBJECT did not route to Review")
    report["negative_cases"].append(
        {"case": "object_review", "status": "Review", "passed": True}
    )

    timeout_id = f"{run_id}::timeout"
    _, _ = client.freeze(
        record, timeout_id, cid, judge_ids, int(time.time()) + 2
    )
    time.sleep(3)
    client.invoke("FinalizeDecisionTimeout", timeout_id)
    timeout_snapshot = client.query("QueryDecisionSnapshot", timeout_id)
    if timeout_snapshot["status"] != "Review":
        raise RuntimeError("timeout did not route to Review")
    report["negative_cases"].append(
        {"case": "timeout_review", "status": "Review", "passed": True}
    )

    digest_file = run_dir / "decision_digests.json"
    digest_file.write_text(
        json.dumps(
            {
                "schema": "mj5-rgg-input-v1",
                "entries": [
                    {
                        "decision_id": decision_id,
                        "digest": snapshot["decisionDigest"],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    docker_containers("stop")
    rgg_dir = run_dir / "rgg"
    rgg_dir.mkdir()
    (rgg_dir / "decision_digests.json").write_bytes(digest_file.read_bytes())
    reputation_order = client.registrations["validator_order"]
    rgg_source = repo_root / "src/rggpbft"
    rgg_started = time.perf_counter()
    subprocess.run(
        [
            sys.executable,
            str(rgg_source / "run_v2.py"),
            "--mode",
            "rgg",
            "--nodes",
            "16",
            "--groups",
            "4",
            "--rounds",
            "1",
            "--delay-ms",
            "5",
            "--round-timeout",
            "15",
            "--view-timeout",
            "1",
            "--reputation-order",
            ",".join(map(str, reputation_order)),
            "--seed",
            "20260705",
            "--run-dir",
            str(rgg_dir),
            "--image",
            "zte-rggpbft:mj5",
        ],
        check=True,
        cwd=rgg_source,
    )
    rgg_elapsed_ms = (time.perf_counter() - rgg_started) * 1000
    protocol_certificate = extract_protocol_certificates(
        rgg_dir / "events.jsonl",
        [{"decision_id": decision_id, "digest": snapshot["decisionDigest"]}],
        reputation_order,
        rgg_source,
    )[0]
    report["rgg"] = {
        "elapsed_ms_including_container_orchestration": rgg_elapsed_ms,
        **protocol_certificate,
    }

    docker_containers("start")
    snapshot = client.query("QueryDecisionSnapshot", decision_id)
    bad_digest = "0" * 64
    bad_payload = {
        "decisionId": decision_id,
        "decisionDigest": bad_digest,
        "view": protocol_certificate["view"],
        "sequence": protocol_certificate["sequence"],
        "protocolMessages": protocol_certificate["protocol_certificate"],
    }
    client.invoke(
        "CertifyDecision",
        json.dumps(bad_payload, separators=(",", ":")),
        expect_error="ERR_CERTIFICATE_DIGEST_MISMATCH",
    )
    report["negative_cases"].append(
        {
            "case": "certificate_digest_mismatch",
            "expected_error": "ERR_CERTIFICATE_DIGEST_MISMATCH",
            "passed": True,
        }
    )

    tampered_messages = copy.deepcopy(
        protocol_certificate["protocol_certificate"]
    )
    signature = tampered_messages[0]["signature"]
    tampered_messages[0]["signature"] = (
        ("A" if signature[0] != "A" else "B") + signature[1:]
    )
    client.invoke(
        "CertifyDecision",
        json.dumps(
            {
                "decisionId": decision_id,
                "decisionDigest": snapshot["decisionDigest"],
                "view": protocol_certificate["view"],
                "sequence": protocol_certificate["sequence"],
                "protocolMessages": tampered_messages,
            },
            separators=(",", ":"),
        ),
        expect_error="ERR_CERTIFICATE_SIGNATURE",
    )
    report["negative_cases"].append(
        {
            "case": "protocol_signature_tamper",
            "expected_error": "ERR_CERTIFICATE_SIGNATURE",
            "passed": True,
        }
    )

    non_leader_messages = copy.deepcopy(
        protocol_certificate["protocol_certificate"]
    )
    leaders = set(reputation_order[:4])
    non_leader = next(
        node_id for node_id in reputation_order if node_id not in leaders
    )
    non_leader_messages[-1]["sender"] = non_leader
    client.invoke(
        "CertifyDecision",
        json.dumps(
            {
                "decisionId": decision_id,
                "decisionDigest": snapshot["decisionDigest"],
                "view": protocol_certificate["view"],
                "sequence": protocol_certificate["sequence"],
                "protocolMessages": non_leader_messages,
            },
            separators=(",", ":"),
        ),
        expect_error="ERR_CERTIFICATE_SIGNER_NOT_LEADER",
    )
    report["negative_cases"].append(
        {
            "case": "non_leader_protocol_signer",
            "expected_error": "ERR_CERTIFICATE_SIGNER_NOT_LEADER",
            "passed": True,
        }
    )

    certify_ms = client.certify(
        snapshot,
        protocol_certificate["protocol_certificate"],
        protocol_certificate["view"],
        protocol_certificate["sequence"],
    )
    settle_ms = client.settle(decision_id, record["expected_label"])
    settled = client.query("QueryDecisionSnapshot", decision_id)
    if settled["status"] != "Settled" or not settled["settled"]:
        raise RuntimeError("main decision did not settle")
    payload = json.dumps(
        {
            "decisionId": decision_id,
            "independentLabel": record["expected_label"],
        },
        separators=(",", ":"),
    )
    client.invoke(
        "SettleDecision", payload, expect_error="ERR_ALREADY_SETTLED"
    )
    report["negative_cases"].append(
        {
            "case": "duplicate_settlement",
            "expected_error": "ERR_ALREADY_SETTLED",
            "passed": True,
        }
    )
    report["fabric_final"] = {
        "status": settled["status"],
        "certificate_digest": settled["certificate"]["certificateSha256"],
        "protocol_certificate_sha256": settled["certificate"][
            "protocolCertificateSha256"
        ],
        "certificate_signers": [
            item["validatorId"] for item in settled["certificate"]["signers"]
        ],
        "certify_latency_ms": certify_ms,
        "settle_latency_ms": settle_ms,
    }
    report["passed"] = all(
        item["passed"] for item in report["negative_cases"]
    )
    report["ended_at_unix"] = int(time.time())
    (run_dir / "smoke_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    default_repo = Path(__file__).resolve().parents[2]
    parser.add_argument("--repo-root", type=Path, default=default_repo)
    parser.add_argument(
        "--workload",
        type=Path,
        default=default_repo / "results/cross_layer/workload",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_repo / "results/cross_layer/smoke",
    )
    parser.add_argument("--record-index", type=int, default=0)
    return parser


if __name__ == "__main__":
    try:
        run(build_parser().parse_args())
    except (FabricCommandError, RuntimeError) as error:
        print(f"MJ5 smoke failed: {error}", file=sys.stderr)
        raise
