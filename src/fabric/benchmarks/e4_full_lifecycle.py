#!/usr/bin/env python3
import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import pathlib
import platform
import queue
import random
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone


def load_manifest_payloads(manifest_path):
    manifest_path = pathlib.Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payloads = []
    for expected_index, batch in enumerate(manifest.get("batches", [])):
        if int(batch["batch_index"]) != expected_index:
            raise ValueError("batch indexes must be contiguous and ordered")
        path = manifest_path.parent / batch["filename"]
        data = path.read_bytes()
        if len(data) != int(batch["bytes"]):
            raise ValueError(f"batch byte length mismatch: {path}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != batch["sha256"]:
            raise ValueError(f"batch SHA-256 mismatch: {path}")
        payloads.append(
            {
                "batch_index": expected_index,
                "record_count": int(batch["record_count"]),
                "filename": batch["filename"],
                "sha256": digest,
                "data": data,
            }
        )
    if not payloads:
        raise ValueError("batch manifest contains no payloads")
    record_count = sum(item["record_count"] for item in payloads)
    if record_count != int(manifest["record_count"]):
        raise ValueError("batch manifest record count mismatch")
    return payloads


def make_payload(rng, task_id, sequence, size):
    prefix = json.dumps(
        {"taskId": task_id, "sequence": sequence, "evidence": ""},
        separators=(",", ":"),
    ).encode("utf-8")
    marker = b'"}'
    prefix = prefix[: -len(marker)]
    if size < len(prefix) + len(marker):
        raise ValueError(f"payload size {size} is too small for metadata")
    alphabet = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    padding = bytes(alphabet[rng.randrange(len(alphabet))] for _ in range(size - len(prefix) - len(marker)))
    return prefix + padding + marker


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))]


def summarize_records(records, workflow_status):
    measured = [record for record in records if not record["warmup"]]
    measured_tasks = {record["task_id"] for record in measured}
    statuses = [workflow_status[task_id] for task_id in measured_tasks]
    starts = [record["started_at_ms"] for record in measured]
    ends = [record["started_at_ms"] + record["latency_ms"] for record in measured]
    duration_ms = max(ends) - min(starts) if measured else 0
    per_operation = {}
    grouped = defaultdict(list)
    for record in measured:
        grouped[record["op"]].append(record)
    for op, rows in sorted(grouped.items()):
        successful = [row for row in rows if row["success"]]
        latencies = [row["latency_ms"] for row in successful]
        per_operation[op] = {
            "count": len(rows),
            "success_count": len(successful),
            "failure_count": len(rows) - len(successful),
            "p50_ms": percentile(latencies, 0.50),
            "p95_ms": percentile(latencies, 0.95),
            "p99_ms": percentile(latencies, 0.99),
        }
    successful_workflows = sum(statuses)
    return {
        "operation_count": len(measured),
        "successful_operations": sum(record["success"] for record in measured),
        "failed_operations": sum(not record["success"] for record in measured),
        "workflow_count": len(statuses),
        "successful_workflows": successful_workflows,
        "failed_workflows": len(statuses) - successful_workflows,
        "workflow_success_rate": successful_workflows / len(statuses) if statuses else None,
        "measurement_duration_ms": duration_ms,
        "workflow_throughput_per_s": successful_workflows / (duration_ms / 1000) if duration_ms else None,
        "per_operation": per_operation,
    }


class CommandError(RuntimeError):
    pass


class LifecycleBenchmark:
    def __init__(self, args):
        self.args = args
        self.root = pathlib.Path(args.fabric_root).resolve()
        self.test_network = self.root / "fabric-samples" / "test-network"
        self.peer = self.root / "fabric-samples" / "bin" / "peer"
        self.config = self.root / "fabric-samples" / "config"
        self.payloads = (
            load_manifest_payloads(args.batch_manifest)
            if args.batch_manifest
            else None
        )
        if self.payloads is not None:
            if args.tasks is not None and args.tasks != len(self.payloads):
                raise ValueError("--tasks must equal the manifest batch count")
            args.tasks = len(self.payloads)
        elif args.tasks is None:
            args.tasks = 10
        payload_label = "real" if self.payloads is not None else f"s{args.payload_size}"
        self.run_id = args.run_id or f"e4-lifecycle-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-c{args.concurrency}-{payload_label}"
        self.run_dir = pathlib.Path(args.output) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.records = []
        self.workflow_status = {}
        self.record_lock = threading.Lock()
        self.sequence = 0
        self.slots = []
        self.slot_queue = queue.Queue()

        orgs = self.test_network / "organizations"
        self.orderer_ca = orgs / "ordererOrganizations" / "example.com" / "tlsca" / "tlsca.example.com-cert.pem"
        self.peer_tls = [
            orgs / "peerOrganizations" / f"org{i}.example.com" / "tlsca" / f"tlsca.org{i}.example.com-cert.pem"
            for i in (1, 2, 3)
        ]

    def identity_env(self, identity):
        env = os.environ.copy()
        env.update(
            {
                "FABRIC_CFG_PATH": str(self.config),
                "CORE_PEER_TLS_ENABLED": "true",
                "CORE_PEER_LOCALMSPID": "Org1MSP",
                "CORE_PEER_TLS_ROOTCERT_FILE": str(
                    self.test_network
                    / "organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
                ),
                "CORE_PEER_MSPCONFIGPATH": str(
                    self.test_network
                    / "organizations/peerOrganizations/org1.example.com/users"
                    / identity
                    / "msp"
                ),
                "CORE_PEER_ADDRESS": "localhost:7051",
            }
        )
        return env

    def run_command(self, command, identity="Admin@org1.example.com", input_bytes=None):
        result = subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=self.identity_env(identity),
            check=False,
        )
        if result.returncode:
            text = result.stdout.decode("utf-8", errors="replace")
            raise CommandError(text[-1000:])
        return result.stdout

    @staticmethod
    def chaincode_payload(*args):
        return json.dumps({"Args": list(args)}, separators=(",", ":"))

    def invoke(self, identity, *args):
        command = [
            str(self.peer),
            "chaincode",
            "invoke",
            "-o",
            "localhost:7050",
            "--ordererTLSHostnameOverride",
            "orderer.example.com",
            "--tls",
            "--cafile",
            str(self.orderer_ca),
            "-C",
            "trustchannel",
            "-n",
            "tce",
        ]
        for port, cert in zip((7051, 9051), self.peer_tls[:2]):
            command.extend(["--peerAddresses", f"localhost:{port}", "--tlsRootCertFiles", str(cert)])
        command.extend(["--waitForEvent", "--waitForEventTimeout", "60s", "-c", self.chaincode_payload(*args)])
        return self.run_command(command, identity)

    def query(self, identity, *args):
        command = [
            str(self.peer),
            "chaincode",
            "query",
            "-C",
            "trustchannel",
            "-n",
            "tce",
            "-c",
            self.chaincode_payload(*args),
        ]
        return json.loads(self.run_command(command, identity).decode("utf-8"))

    def ensure_evaluator(self, eval_id, identity, capability):
        # Always try to register first (goes to both peers via invoke).
        # If already exists, fall back to query.
        registration = json.dumps(
            {"evalId": eval_id, "capabilities": [capability]}, separators=(",", ":")
        )
        try:
            self.invoke(identity, "RegisterEvaluator", registration)
        except CommandError as e:
            if "ERR_EVALUATOR_EXISTS" not in str(e):
                raise
        state = self.query(identity, "QueryEvaluator", eval_id)
        return {
            "eval_id": eval_id,
            "identity": identity,
            "client_id": state["clientId"],
            "msp_id": state["mspId"],
            "capability": capability,
        }

    def prepare_slots(self):
        print(f"Preparing {self.args.concurrency} committee slots (excluded from metrics)", flush=True)
        identities = ("evaluator-e1", "evaluator-e2", "evaluator-e3")
        capabilities = ("safety-pass", "unsafe-refusal", "safe-utility")
        for slot_index in range(self.args.concurrency):
            members = []
            for member_index, (identity, capability) in enumerate(zip(identities, capabilities), start=1):
                eval_id = f"e4-c{self.args.concurrency}-slot{slot_index + 1:02d}-e{member_index}"
                members.append(self.ensure_evaluator(eval_id, identity, capability))
            self.slots.append(members)
            self.slot_queue.put(slot_index)

    def record(self, task_id, op, success, latency_ms, started_at_ms, warmup, error="", bytes_sent=0, cid=""):
        with self.record_lock:
            self.sequence += 1
            self.records.append(
                {
                    "seq": self.sequence,
                    "run_id": self.run_id,
                    "task_id": task_id,
                    "op": op,
                    "success": success,
                    "error": error,
                    "latency_ms": latency_ms,
                    "started_at_ms": started_at_ms,
                    "bytes_sent": bytes_sent,
                    "cid": cid,
                    "warmup": warmup,
                }
            )

    def measure(self, task_id, op, warmup, function, bytes_sent=0, cid=""):
        started = time.time_ns() // 1_000_000
        try:
            result = function()
        except Exception as exc:
            elapsed = time.time_ns() // 1_000_000 - started
            self.record(task_id, op, False, elapsed, started, warmup, str(exc), bytes_sent, cid)
            raise
        elapsed = time.time_ns() // 1_000_000 - started
        self.record(task_id, op, True, elapsed, started, warmup, bytes_sent=bytes_sent, cid=cid)
        return result

    def ipfs_add(self, payload):
        output = self.run_command(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "-X",
                "POST",
                "-F",
                "file=@-;filename=evidence.json",
                "http://localhost:5001/api/v0/add",
            ],
            input_bytes=payload,
        )
        return json.loads(output)["Hash"]

    def ipfs_verify(self, cid, expected_sha, expected_size):
        output = self.run_command(
            ["curl", "--fail", "--silent", "--show-error", "-X", "POST", f"http://localhost:5001/api/v0/cat?arg={cid}"]
        )
        if len(output) != expected_size or hashlib.sha256(output).hexdigest() != expected_sha:
            raise CommandError("IPFS retrieval integrity mismatch")

    def run_workflow(self, index, warmup=False):
        slot_index = self.slot_queue.get()
        members = self.slots[slot_index]
        task_id = f"{self.run_id}-{'warmup' if warmup else 'task'}-{index:04d}"
        if self.payloads is None:
            rng = random.Random(self.args.seed + index + (1_000_000 if warmup else 0))
            payload = make_payload(rng, task_id, index, self.args.payload_size)
            evidence_record_count = 1
            source_batch = None
        else:
            source = self.payloads[index % len(self.payloads)]
            payload = source["data"]
            evidence_record_count = source["record_count"]
            source_batch = source["filename"]
        evidence_sha = hashlib.sha256(payload).hexdigest()
        deadline = int(time.time()) + 3600
        success = False
        try:
            cid = self.measure(task_id, "ipfs_add", warmup, lambda: self.ipfs_add(payload), len(payload))
            task = {
                "taskId": task_id,
                "subjectId": "e4-benchmark-subject",
                "riskCategories": ["multimodal-safety"],
                "modalities": ["text", "image"],
                "workload": evidence_record_count,
                "deadlineUnix": deadline,
                "inputBytes": len(payload),
                "priority": 5,
                "minEvaluators": 3,
                "minReputationPpm": 0,
                "cid": cid,
                "sha256": evidence_sha,
            }
            self.measure(
                task_id,
                "fabric_post_task",
                warmup,
                lambda: self.invoke("Admin@org1.example.com", "PostTaskConstraint", json.dumps(task, separators=(",", ":"))),
                len(payload),
                cid,
            )
            allocation = {
                "taskId": task_id,
                "members": [
                    {"evalId": member["eval_id"], "sharePpm": share}
                    for member, share in zip(members, (333334, 333333, 333333))
                ],
            }
            self.measure(
                task_id,
                "fabric_post_allocation",
                warmup,
                lambda: self.invoke("audit-service", "PostAllocation", json.dumps(allocation, separators=(",", ":"))),
            )
            snapshot = {
                "taskId": task_id,
                "evalItems": [
                    {"evalId": member["eval_id"], "scorePpm": score, "verdict": "benchmark-fixture"}
                    for member, score in zip(members, (800000, 750000, 900000))
                ],
                "evidenceRefs": [
                    {
                        "evalId": member["eval_id"],
                        "taskId": task_id,
                        "cid": cid,
                        "sha256": evidence_sha,
                        "submitterClientId": member["client_id"],
                        "submitterMspId": member["msp_id"],
                    }
                    for member in members
                ],
                "deadlineUnix": deadline,
            }
            self.measure(
                task_id,
                "fabric_post_snapshot",
                warmup,
                lambda: self.invoke("audit-service", "PostEvalSnapshot", json.dumps(snapshot, separators=(",", ":"))),
            )
            confirmation = self.measure(
                task_id,
                "fabric_query_confirmation",
                warmup,
                lambda: self.query("audit-service", "QueryConfirmation", task_id),
            )
            digest = confirmation["digest"]
            for vote_index, member in enumerate(members[:2], start=1):
                self.measure(
                    task_id,
                    f"fabric_vote_{vote_index}",
                    warmup,
                    lambda member=member: self.invoke(member["identity"], "SubmitVote", task_id, digest, "ACK"),
                )
            self.measure(
                task_id,
                "fabric_settlement",
                warmup,
                lambda: self.invoke("audit-service", "ProcessSettlement", task_id),
            )
            final_state = self.measure(
                task_id,
                "fabric_query_final",
                warmup,
                lambda: {
                    "confirmation": self.query("audit-service", "QueryConfirmation", task_id),
                    "allocation": self.query("audit-service", "QueryAllocation", task_id),
                    "reputation": self.query("audit-service", "QueryTaskReputation", "e4-benchmark-subject", task_id),
                },
            )
            if final_state["confirmation"]["status"] != "Accept" or not final_state["confirmation"]["consumed"]:
                raise CommandError("confirmation did not reach consumed Accept state")
            if final_state["allocation"]["status"] != "Settled":
                raise CommandError("allocation did not reach Settled state")
            self.measure(
                task_id,
                "ipfs_verify",
                warmup,
                lambda: self.ipfs_verify(cid, evidence_sha, len(payload)),
                cid=cid,
            )
            success = True
        except Exception as exc:
            print(f"Workflow {task_id} failed: {exc}", flush=True)
        finally:
            with self.record_lock:
                self.workflow_status[task_id] = success
            self.slot_queue.put(slot_index)
        return success

    def write_artifacts(self):
        records = sorted(self.records, key=lambda row: row["seq"])
        summary = summarize_records(records, self.workflow_status)
        if self.payloads is not None:
            evidence_record_count = sum(item["record_count"] for item in self.payloads)
            summary["evidence_record_count"] = evidence_record_count
            duration_ms = summary["measurement_duration_ms"]
            summary["evidence_record_throughput_per_s"] = (
                evidence_record_count / (duration_ms / 1000)
                if duration_ms
                else None
            )
        config = {
            "run_id": self.run_id,
            "seed": self.args.seed,
            "concurrency": self.args.concurrency,
            "payload_size": self.args.payload_size,
            "batch_manifest": (
                str(pathlib.Path(self.args.batch_manifest).resolve())
                if self.args.batch_manifest
                else None
            ),
            "payload_mode": "real-e1-batches" if self.payloads is not None else "synthetic-fixed-size",
            "tasks": self.args.tasks,
            "warmup_tasks": self.args.warmup,
            "topology": "single-host, 3 organizations, 1 peer per organization, 1 Raft orderer, Kubo IPFS",
            "workflow": [
                "ipfs_add",
                "fabric_post_task",
                "fabric_post_allocation",
                "fabric_post_snapshot",
                "fabric_query_confirmation",
                "fabric_vote_1",
                "fabric_vote_2",
                "fabric_settlement",
                "fabric_query_final",
                "ipfs_verify",
            ],
            "fixture_note": "Fixed scores affect state values but not the measured control path.",
        }
        environment = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "chaincode": "tce 1.2 sequence 3",
            "fabric": "2.5.16",
            "ipfs": "Kubo 0.42.0",
        }
        for name, value in (("config.json", config), ("environment.json", environment), ("summary.json", summary)):
            (self.run_dir / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with (self.run_dir / "operations.jsonl").open("w", encoding="utf-8") as handle:
            for row in records:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        with (self.run_dir / "operations.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]) if records else [])
            if records:
                writer.writeheader()
                writer.writerows(records)
        (self.run_dir / "workflows.json").write_text(
            json.dumps(self.workflow_status, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        files = ["config.json", "environment.json", "summary.json", "operations.jsonl", "operations.csv", "workflows.json"]
        with (self.run_dir / "checksums.sha256").open("w", encoding="ascii") as handle:
            for name in files:
                digest = hashlib.sha256((self.run_dir / name).read_bytes()).hexdigest()
                handle.write(f"{digest}  {name}\n")
        return summary

    def run(self):
        self.prepare_slots()
        for index in range(self.args.warmup):
            self.run_workflow(index, warmup=True)
        print(f"Running {self.args.tasks} measured workflows at concurrency {self.args.concurrency}", flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.concurrency) as executor:
            futures = [executor.submit(self.run_workflow, index) for index in range(self.args.tasks)]
            for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                future.result()
                print(f"Progress: {completed}/{self.args.tasks}", flush=True)
        summary = self.write_artifacts()
        print(json.dumps(summary, indent=2), flush=True)
        print(f"Results: {self.run_dir}", flush=True)
        if summary["failed_workflows"]:
            raise SystemExit(1)


def parse_args():
    default_root = pathlib.Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--fabric-root", default=str(default_root))
    parser.add_argument("--output", default=str(default_root / "results" / "e4-lifecycle"))
    parser.add_argument("--run-id")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--payload-size", type=int, default=65536)
    parser.add_argument("--tasks", type=int)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260704)
    parser.add_argument("--batch-manifest")
    return parser.parse_args()


if __name__ == "__main__":
    LifecycleBenchmark(parse_args()).run()
