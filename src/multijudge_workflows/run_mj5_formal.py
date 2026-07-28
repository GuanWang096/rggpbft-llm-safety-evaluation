from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import resource
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mj5_common import (
    canonical_fields,
    chunks,
    deterministic_private_key,
    read_json,
)
from mj5_fabric_client import FabricCommandError, MJ5FabricClient
from mj5_rgg_bridge import extract_protocol_certificates


CORE_CONTAINERS = (
    "couchdb0",
    "couchdb1",
    "orderer.example.com",
    "peer0.org1.example.com",
    "peer0.org2.example.com",
)


def start_containers_with_retries(names: list[str]) -> None:
    last_output = ""
    for attempt in range(5):
        result = subprocess.run(
            ["docker", "start", *names],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        last_output = result.stdout + result.stderr
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(
        "Docker containers failed to start after five attempts: "
        + last_output[-1200:]
    )


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_manifest(
    repo_root: Path,
    roots: list[Path],
) -> dict[str, Any]:
    files: dict[str, str] = {}
    for root in roots:
        candidates = [root] if root.is_file() else sorted(
            path
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and ".git" not in path.parts
            and path.suffix != ".pyc"
        )
        for path in candidates:
            relative = path.resolve().relative_to(repo_root).as_posix()
            files[relative] = sha256_file(path)
    encoded = json.dumps(
        files, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "algorithm": "sha256",
        "file_count": len(files),
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "files": files,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def docker_containers(action: str, *, include_ipfs: bool = True) -> None:
    containers = list(CORE_CONTAINERS)
    if include_ipfs:
        containers.append("ipfs-kubo")
    if action == "start":
        infrastructure = [
            "couchdb0",
            "couchdb1",
            "orderer.example.com",
        ]
        if include_ipfs:
            infrastructure.append("ipfs-kubo")
        start_containers_with_retries(infrastructure)
        time.sleep(8)
        start_containers_with_retries(
            ["peer0.org1.example.com", "peer0.org2.example.com"]
        )
        time.sleep(8)
        return
    subprocess.run(
        ["docker", action, *containers],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def ensure_fabric_running() -> None:
    running = set(
        subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}}"], text=True
        ).splitlines()
    )
    required = set(CORE_CONTAINERS) | {"ipfs-kubo"}
    if not required.issubset(running):
        docker_containers("start")


class ResourceSampler:
    def __init__(self, interval_seconds: float = 2.0) -> None:
        self.interval_seconds = interval_seconds
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "ResourceSampler":
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds + 2)

    def _run(self) -> None:
        while not self._stop.is_set():
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    row["sampled_at_unix"] = time.time()
                    self.samples.append(row)
            self._stop.wait(self.interval_seconds)


def summarize_latencies(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean_ms": statistics.fmean(values) if values else None,
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
    }


def run_parallel_chunks(
    records: list[dict[str, Any]],
    batch_size: int,
    concurrency: int,
    worker: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    for batch_index, batch in enumerate(chunks(records, batch_size)):
        print(
            f"  batch {batch_index + 1}: {len(batch)} records, c={concurrency}",
            flush=True,
        )
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=concurrency
        ) as executor:
            futures = [executor.submit(worker, record) for record in batch]
            for future in concurrent.futures.as_completed(futures):
                output.append(future.result())
    return output


class FormalRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.repo_root = args.repo_root.resolve()
        self.workload_dir = args.workload.resolve()
        self.output_root = args.output.resolve()
        self.qualification = args.qualification
        self.session_id = args.session_id or (
            ("mj5-qualification-" if self.qualification else "mj5-formal-")
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
        self.session_dir = self.output_root / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.client = MJ5FabricClient(self.repo_root, self.workload_dir)
        self.matrix = read_json(self.workload_dir / "matrix.json")
        self.selection = read_json(
            self.workload_dir / "system_selection.json"
        )
        self.rgg_source = self.repo_root / "src/rggpbft"
        self.judge_namespace = f"{self.session_id}::"
        self.judge_ids: dict[str, str] = {}
        self._write_lock = threading.Lock()
        self._settlement_lock = threading.Lock()

        if self.qualification:
            self.entries = [
                {
                    "run_id": "qualification-j4-c16-r1",
                    "judge_count": 4,
                    "concurrency": 16,
                    "repeat": 1,
                    "sample_count": 16,
                    "execution_batch_count": 1,
                }
            ]
            self.selected_ids = self.selection["sample_ids"][:16]
        else:
            self.entries = list(self.matrix["entries"])
            self.selected_ids = list(self.selection["sample_ids"])
        write_json(
            self.session_dir / "session_manifest.json",
            {
                "schema": "mj5-formal-session-v1",
                "session_id": self.session_id,
                "qualification": self.qualification,
                "judge_namespace": self.judge_namespace,
                "entries": self.entries,
                "sample_ids": self.selected_ids,
                "created_at_unix": int(time.time()),
            },
        )
        experiment_dir = Path(__file__).resolve().parent
        self.provenance = {
            "schema": "mj5-formal-provenance-v1",
            "workload": source_manifest(
                self.repo_root, [self.workload_dir]
            ),
            "source": source_manifest(
                self.repo_root,
                [
                    self.repo_root / "src/fabric_chaincode",
                    self.repo_root / "src/rggpbft",
                    experiment_dir / "run_mj5_formal.py",
                    experiment_dir / "mj5_fabric_client.py",
                    experiment_dir / "mj5_common.py",
                    experiment_dir / "mj5_rgg_bridge.py",
                    experiment_dir / "analyze_mj5_formal.py",
                ],
            ),
            "runtime": {},
        }
        write_json(
            self.session_dir / "provenance.json", self.provenance
        )

    def records_for_entry(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        manifest = read_json(
            self.workload_dir / f"j{entry['judge_count']}_manifest.json"
        )
        by_id = {record["sample_id"]: record for record in manifest["records"]}
        return [by_id[sample_id] for sample_id in self.selected_ids]

    def entry_dir(self, entry: dict[str, Any]) -> Path:
        path = self.session_dir / "runs" / entry["run_id"]
        path.mkdir(parents=True, exist_ok=True)
        return path

    def initialize(self) -> None:
        ensure_fabric_running()
        committed = self.client.run_peer(
            [
                "lifecycle",
                "chaincode",
                "querycommitted",
                "--channelID",
                "trustchannel",
                "--name",
                "tce",
            ],
            timeout=60,
        )
        admin_environment = self.client.environment()
        admin_environment["CORE_PEER_MSPCONFIGPATH"] = str(
            self.client.test_network
            / "organizations/peerOrganizations/org1.example.com"
            / "users/Admin@org1.example.com/msp"
        )
        installed = subprocess.run(
            [
                str(self.client.peer),
                "lifecycle",
                "chaincode",
                "queryinstalled",
            ],
            capture_output=True,
            text=True,
            env=admin_environment,
            timeout=60,
            check=False,
        )
        if committed.returncode != 0 or installed.returncode != 0:
            raise RuntimeError("unable to capture Fabric chaincode provenance")
        definition = committed.stdout.strip()
        if "Version: 4.1, Sequence: 5" not in definition:
            raise RuntimeError(
                "MJ5 requires tce chaincode version 4.1 sequence 5"
            )
        package_match = re.search(
            r"Package ID: ([^,\s]+), Label: mj5_4\.1",
            installed.stdout,
        )
        if package_match is None:
            raise RuntimeError("unable to resolve mj5_4.1 package ID")
        image_id = subprocess.check_output(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                "zte-rggpbft:mj5",
            ],
            text=True,
        ).strip()
        self.provenance["runtime"] = {
            "fabric_chaincode_definition": definition,
            "fabric_chaincode_package_id": package_match.group(1),
            "rgg_image_id": image_id,
        }
        write_json(
            self.session_dir / "provenance.json", self.provenance
        )
        self.judge_ids = self.client.ensure_registrations(
            self.judge_namespace
        )
        write_json(
            self.session_dir / "registered_identities.json",
            {
                "judge_ids": self.judge_ids,
                "validator_order": self.client.registrations[
                    "validator_order"
                ],
            },
        )

    def stage_a_worker(
        self,
        entry: dict[str, Any],
        record: dict[str, Any],
    ) -> dict[str, Any]:
        decision_id = (
            f"{self.session_id}::{entry['run_id']}::"
            f"{record['index']:04d}"
        )
        bundle = (self.workload_dir / record["bundle_path"]).read_bytes()
        started = time.perf_counter()
        cid, ipfs_ms = self.client.ipfs_add(bundle)
        freeze_ms = 0.0
        vote_ms: list[float] = []
        freeze_commit: dict[str, Any] | None = None
        vote_commits: list[dict[str, Any]] = []
        try:
            (
                snapshot,
                freeze_ms,
                freeze_commit,
            ) = self.client.freeze_with_details(
                record,
                decision_id,
                cid,
                self.judge_ids,
                int(time.time()) + 86_400,
            )
        except FabricCommandError as error:
            if "ERR_DECISION_EXISTS" not in str(error):
                raise
            snapshot = self.client.query(
                "QueryDecisionSnapshot", decision_id
            )
        for node_id in self.client.registrations["validator_order"][:2]:
            if snapshot["status"] != "SnapshotFrozen":
                break
            try:
                vote_latency, vote_commit = (
                    self.client.submit_vote_with_details(
                        snapshot, node_id, "ACK"
                    )
                )
                vote_ms.append(vote_latency)
                vote_commits.append(vote_commit)
            except FabricCommandError as error:
                if "ERR_DUPLICATE_VOTE" not in str(error):
                    raise
            snapshot = self.client.query(
                "QueryDecisionSnapshot", decision_id
            )
        if snapshot["status"] != "CommitteeConfirmed":
            raise RuntimeError(
                f"{decision_id} stopped at {snapshot['status']}"
            )
        return {
            "index": record["index"],
            "sample_id": record["sample_id"],
            "expected_label": record["expected_label"],
            "decision_id": decision_id,
            "decision_digest": snapshot["decisionDigest"],
            "evidence_cid": cid,
            "evidence_sha256": record["evidence_sha256"],
            "bundle_bytes": len(bundle),
            "ipfs_add_ms": ipfs_ms,
            "freeze_ms": freeze_ms,
            "vote_ms": vote_ms,
            "fabric_commits": {
                "freeze": freeze_commit,
                "committee_votes": vote_commits,
            },
            "stage_a_total_ms": (time.perf_counter() - started) * 1000,
            "status": snapshot["status"],
            "frozen_versions": [
                item["version"]
                for item in snapshot["frozenReliabilities"]
            ],
        }

    def run_stage_a(self) -> None:
        self.initialize()
        for entry_index, entry in enumerate(self.entries, start=1):
            directory = self.entry_dir(entry)
            status_path = directory / "stage_a_status.json"
            if status_path.exists() and read_json(status_path).get(
                "state"
            ) == "completed":
                print(
                    f"[A {entry_index}/{len(self.entries)}] "
                    f"{entry['run_id']} already complete",
                    flush=True,
                )
                continue
            print(
                f"[A {entry_index}/{len(self.entries)}] {entry['run_id']}",
                flush=True,
            )
            records = self.records_for_entry(entry)
            existing = {
                row["sample_id"]: row
                for row in read_jsonl(directory / "stage_a.jsonl")
            }
            pending = [
                record
                for record in records
                if record["sample_id"] not in existing
            ]
            started = time.perf_counter()
            with ResourceSampler() as sampler:
                new_rows = run_parallel_chunks(
                    pending,
                    64,
                    entry["concurrency"],
                    lambda record: self.stage_a_worker(
                        entry, record
                    ),
                )
            rows = list(existing.values()) + new_rows
            rows.sort(key=lambda row: row["index"])
            with (directory / "stage_a.jsonl").open(
                "w", encoding="utf-8"
            ) as output:
                for row in rows:
                    output.write(
                        json.dumps(row, sort_keys=True) + "\n"
                    )
            elapsed = time.perf_counter() - started
            write_json(directory / "stage_a_resources.json", sampler.samples)
            write_json(
                status_path,
                {
                    "state": "completed",
                    "record_count": len(rows),
                    "elapsed_seconds_this_invocation": elapsed,
                    "workflow_throughput_per_second": (
                        len(new_rows) / elapsed if elapsed else None
                    ),
                    "peak_client_rss_kib": resource.getrusage(
                        resource.RUSAGE_SELF
                    ).ru_maxrss,
                },
            )
            if len(rows) != len(records):
                raise RuntimeError(
                    f"{entry['run_id']} stage A count mismatch"
                )

    def run_rgg(self) -> None:
        docker_containers("stop")
        try:
            image_exists = (
                subprocess.run(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "zte-rggpbft:mj5",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0
            )
            for entry_index, entry in enumerate(
                self.entries, start=1
            ):
                directory = self.entry_dir(entry)
                cert_path = directory / "protocol_certificates.json"
                if cert_path.exists():
                    print(
                        f"[R {entry_index}/{len(self.entries)}] "
                        f"{entry['run_id']} already complete",
                        flush=True,
                    )
                    continue
                print(
                    f"[R {entry_index}/{len(self.entries)}] "
                    f"{entry['run_id']}",
                    flush=True,
                )
                stage_a = sorted(
                    read_jsonl(directory / "stage_a.jsonl"),
                    key=lambda row: row["index"],
                )
                inputs = [
                    {
                        "decision_id": row["decision_id"],
                        "digest": row["decision_digest"],
                    }
                    for row in stage_a
                ]
                rgg_dir = directory / "rgg"
                rgg_dir.mkdir(exist_ok=True)
                write_json(
                    rgg_dir / "decision_digests.json",
                    {"schema": "mj5-rgg-input-v1", "entries": inputs},
                )
                command = [
                    sys.executable,
                    str(self.rgg_source / "run_v2.py"),
                    "--mode",
                    "rgg",
                    "--nodes",
                    "16",
                    "--groups",
                    "4",
                    "--rounds",
                    str(len(inputs)),
                    "--delay-ms",
                    "5",
                    "--round-timeout",
                    "15",
                    "--view-timeout",
                    "1",
                    "--reputation-order",
                    ",".join(
                        map(
                            str,
                            self.client.registrations[
                                "validator_order"
                            ],
                        )
                    ),
                    "--seed",
                    "20260705",
                    "--run-dir",
                    str(rgg_dir),
                    "--image",
                    "zte-rggpbft:mj5",
                ]
                if image_exists:
                    command.append("--skip-build")
                started = time.perf_counter()
                subprocess.run(
                    command,
                    check=True,
                    cwd=self.rgg_source,
                )
                image_exists = True
                certificates = extract_protocol_certificates(
                    rgg_dir / "events.jsonl",
                    inputs,
                    self.client.registrations["validator_order"],
                    self.rgg_source,
                )
                write_json(cert_path, certificates)
                summary = read_json(rgg_dir / "summary.json")
                if (
                    summary["driver_success_count"] != len(inputs)
                    or summary["conflicting_commit_count"] != 0
                    or summary["safety_violation_events"] != 0
                ):
                    raise RuntimeError(
                        f"{entry['run_id']} RGG stop gate failed"
                    )
                write_json(
                    directory / "rgg_status.json",
                    {
                        "state": "completed",
                        "record_count": len(inputs),
                        "elapsed_seconds": time.perf_counter()
                        - started,
                    },
                )
        finally:
            docker_containers("start")

    def stage_c_worker(
        self,
        row: dict[str, Any],
        certificate: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        snapshot = self.client.query(
            "QueryDecisionSnapshot", row["decision_id"]
        )
        certify_ms = 0.0
        settle_ms = 0.0
        settlement_mvcc_retries = 0
        settlement_queue_wait_ms = 0.0
        certify_commit: dict[str, Any] | None = None
        settle_commit: dict[str, Any] | None = None
        if snapshot["status"] == "CommitteeConfirmed":
            certify_ms, certify_commit = self.client.certify_with_details(
                snapshot,
                certificate["protocol_certificate"],
                certificate["view"],
                certificate["sequence"],
            )
            snapshot = self.client.query(
                "QueryDecisionSnapshot", row["decision_id"]
            )
        if snapshot["status"] == "Certified":
            queued_at = time.perf_counter()
            with self._settlement_lock:
                settlement_queue_wait_ms = (
                    time.perf_counter() - queued_at
                ) * 1000
                snapshot = self.client.query(
                    "QueryDecisionSnapshot", row["decision_id"]
                )
                if snapshot["status"] == "Certified":
                    (
                        settle_ms,
                        settlement_mvcc_retries,
                        settle_commit,
                    ) = self.client.settle_with_commit_details(
                        row["decision_id"], row["expected_label"]
                    )
                    snapshot = self.client.query(
                        "QueryDecisionSnapshot", row["decision_id"]
                    )
        if snapshot["status"] != "Settled":
            raise RuntimeError(
                f"{row['decision_id']} stopped at {snapshot['status']}"
            )
        if (
            snapshot["decisionDigest"] != certificate["digest"]
            or snapshot["certificate"]["protocolCertificateSha256"]
            != certificate["protocol_certificate_sha256"]
            or sorted(
                int(item["validatorId"].split("-")[-1])
                for item in snapshot["certificate"]["signers"]
            )
            != certificate["signer_node_ids"]
        ):
            raise RuntimeError(
                f"{row['decision_id']} certificate binding mismatch"
            )
        return {
            "index": row["index"],
            "sample_id": row["sample_id"],
            "decision_id": row["decision_id"],
            "certify_ms": certify_ms,
            "settle_ms": settle_ms,
            "settlement_mvcc_retries": settlement_mvcc_retries,
            "settlement_queue_wait_ms": settlement_queue_wait_ms,
            "stage_c_total_ms": (time.perf_counter() - started) * 1000,
            "status": snapshot["status"],
            "certificate_sha256": snapshot["certificate"][
                "certificateSha256"
            ],
            "protocol_certificate_sha256": snapshot["certificate"][
                "protocolCertificateSha256"
            ],
            "fabric_commits": {
                "certify": certify_commit,
                "settle": settle_commit,
            },
        }

    def run_stage_c(self) -> None:
        ensure_fabric_running()
        for entry_index, entry in enumerate(self.entries, start=1):
            directory = self.entry_dir(entry)
            status_path = directory / "stage_c_status.json"
            if status_path.exists() and read_json(status_path).get(
                "state"
            ) == "completed":
                print(
                    f"[C {entry_index}/{len(self.entries)}] "
                    f"{entry['run_id']} already complete",
                    flush=True,
                )
                continue
            print(
                f"[C {entry_index}/{len(self.entries)}] {entry['run_id']}",
                flush=True,
            )
            stage_a = sorted(
                read_jsonl(directory / "stage_a.jsonl"),
                key=lambda row: row["index"],
            )
            certificates = read_json(
                directory / "protocol_certificates.json"
            )
            by_decision = {
                certificate["decision_id"]: certificate
                for certificate in certificates
            }
            existing = {
                row["decision_id"]: row
                for row in read_jsonl(directory / "stage_c.jsonl")
            }
            pending = [
                row
                for row in stage_a
                if row["decision_id"] not in existing
            ]
            started = time.perf_counter()
            with ResourceSampler() as sampler:
                new_rows = run_parallel_chunks(
                    pending,
                    64,
                    entry["concurrency"],
                    lambda row: self.stage_c_worker(
                        row, by_decision[row["decision_id"]]
                    ),
                )
            rows = list(existing.values()) + new_rows
            rows.sort(key=lambda row: row["index"])
            with (directory / "stage_c.jsonl").open(
                "w", encoding="utf-8"
            ) as output:
                for row in rows:
                    output.write(
                        json.dumps(row, sort_keys=True) + "\n"
                    )
            elapsed = time.perf_counter() - started
            write_json(directory / "stage_c_resources.json", sampler.samples)
            write_json(
                status_path,
                {
                    "state": "completed",
                    "record_count": len(rows),
                    "elapsed_seconds_this_invocation": elapsed,
                    "workflow_throughput_per_second": (
                        len(new_rows) / elapsed if elapsed else None
                    ),
                },
            )
            if len(rows) != len(stage_a):
                raise RuntimeError(
                    f"{entry['run_id']} stage C count mismatch"
                )

    def run_signed_log_baseline(self) -> None:
        private_key = deterministic_private_key(
            "zte-sci-mj5-signed-log-v1", self.session_id
        )
        for entry in self.entries:
            directory = self.entry_dir(entry)
            path = directory / "signed_log_baseline.json"
            if path.exists():
                continue
            records = self.records_for_entry(entry)
            previous = "0" * 64
            latencies = []
            rows = []
            started_all = time.perf_counter()
            for sequence, record in enumerate(records):
                started = time.perf_counter()
                message = canonical_fields(
                    "MJ5-SIGNED-HASH-CHAIN-v1",
                    str(sequence),
                    previous,
                    record["sample_id"],
                    record["evidence_sha256"],
                    record["provisional_label"],
                )
                signature = private_key.sign(message)
                entry_hash = hashlib.sha256(
                    message + signature
                ).hexdigest()
                private_key.public_key().verify(signature, message)
                latency = (time.perf_counter() - started) * 1000
                latencies.append(latency)
                rows.append(
                    {
                        "sequence": sequence,
                        "sample_id": record["sample_id"],
                        "previous_hash": previous,
                        "entry_hash": entry_hash,
                        "signature_hex": signature.hex(),
                        "latency_ms": latency,
                    }
                )
                previous = entry_hash
            elapsed = time.perf_counter() - started_all
            write_json(
                path,
                {
                    "schema": "mj5-signed-hash-chain-baseline-v1",
                    "trust_model": "single administrator",
                    "record_count": len(rows),
                    "elapsed_seconds": elapsed,
                    "throughput_per_second": (
                        len(rows) / elapsed if elapsed else None
                    ),
                    "latency": summarize_latencies(latencies),
                    "final_hash": previous,
                    "entries": rows,
                },
            )

    def aggregate(self) -> None:
        aggregate_entries = []
        for entry in self.entries:
            directory = self.entry_dir(entry)
            stage_a = sorted(
                read_jsonl(directory / "stage_a.jsonl"),
                key=lambda row: row["index"],
            )
            stage_c = read_jsonl(directory / "stage_c.jsonl")
            stage_c_by_decision = {
                row["decision_id"]: row for row in stage_c
            }
            if len(stage_c_by_decision) != len(stage_a):
                raise RuntimeError(
                    f"{entry['run_id']} aggregate record count mismatch"
                )
            rgg_summary = read_json(directory / "rgg/summary.json")
            certificates = read_json(
                directory / "protocol_certificates.json"
            )
            if len(certificates) != len(stage_a):
                raise RuntimeError(
                    f"{entry['run_id']} certificate count mismatch"
                )
            baseline = read_json(
                directory / "signed_log_baseline.json"
            )
            driver_by_decision = {
                event["data"]["decision_id"]: event["data"]
                for event in read_jsonl(directory / "rgg/events.jsonl")
                if event.get("type") == "DRIVER_RESULT"
            }
            if set(driver_by_decision) != {
                row["decision_id"] for row in stage_a
            }:
                raise RuntimeError(
                    f"{entry['run_id']} RGG decision join mismatch"
                )
            total = [
                row["stage_a_total_ms"]
                + stage_c_by_decision[row["decision_id"]][
                    "stage_c_total_ms"
                ]
                + driver_by_decision[row["decision_id"]]["latency_ms"]
                for row in stage_a
            ]
            aggregate_entries.append(
                {
                    **entry,
                    "fabric_stage_a": {
                        "workflow_total": summarize_latencies(
                            [
                                row["stage_a_total_ms"]
                                for row in stage_a
                            ]
                        ),
                        "ipfs_add": summarize_latencies(
                            [row["ipfs_add_ms"] for row in stage_a]
                        ),
                        "freeze": summarize_latencies(
                            [row["freeze_ms"] for row in stage_a]
                        ),
                        "vote": summarize_latencies(
                            [
                                value
                                for row in stage_a
                                for value in row["vote_ms"]
                            ]
                        ),
                        "status": read_json(
                            directory / "stage_a_status.json"
                        ),
                    },
                    "rgg": rgg_summary,
                    "fabric_stage_c": {
                        "workflow_total": summarize_latencies(
                            [
                                row["stage_c_total_ms"]
                                for row in stage_c
                            ]
                        ),
                        "certify": summarize_latencies(
                            [row["certify_ms"] for row in stage_c]
                        ),
                        "settle": summarize_latencies(
                            [row["settle_ms"] for row in stage_c]
                        ),
                        "settlement_mvcc_retries": {
                            "total": sum(
                                row["settlement_mvcc_retries"]
                                for row in stage_c
                            ),
                            "max_per_record": max(
                                row["settlement_mvcc_retries"]
                                for row in stage_c
                            ),
                        },
                        "settlement_queue_wait": summarize_latencies(
                            [
                                row["settlement_queue_wait_ms"]
                                for row in stage_c
                            ]
                        ),
                        "status": read_json(
                            directory / "stage_c_status.json"
                        ),
                    },
                    "decision_joined_component_total": {
                        **summarize_latencies(total),
                        "composition_method": (
                            "exact decision_id join of Stage A, "
                            "required-commit RGG, and Stage C"
                        ),
                    },
                    "evidence_bundle_bytes": summarize_latencies(
                        [float(row["bundle_bytes"]) for row in stage_a]
                    ),
                    "signed_log_baseline": {
                        key: baseline[key]
                        for key in (
                            "trust_model",
                            "record_count",
                            "elapsed_seconds",
                            "throughput_per_second",
                            "latency",
                            "final_hash",
                        )
                    },
                }
            )
        result = {
            "schema": "mj5-formal-aggregate-v1",
            "session_id": self.session_id,
            "qualification": self.qualification,
            "entry_count": len(aggregate_entries),
            "all_entries_complete": all(
                entry["fabric_stage_a"]["status"]["state"]
                == "completed"
                and entry["fabric_stage_c"]["status"]["state"]
                == "completed"
                and entry["rgg"]["driver_failure_count"] == 0
                and entry["rgg"]["conflicting_commit_count"] == 0
                and entry["rgg"]["safety_violation_events"] == 0
                for entry in aggregate_entries
            ),
            "entries": aggregate_entries,
            "generated_at_unix": int(time.time()),
        }
        write_json(self.session_dir / "aggregate.json", result)
        print(
            json.dumps(
                {
                    "session_id": self.session_id,
                    "entry_count": result["entry_count"],
                    "all_entries_complete": result[
                        "all_entries_complete"
                    ],
                },
                indent=2,
            ),
            flush=True,
        )

    def run_all(self) -> None:
        started = time.time()
        try:
            self.run_stage_a()
            self.run_rgg()
            self.run_stage_c()
            self.run_signed_log_baseline()
            self.aggregate()
        finally:
            write_json(
                self.session_dir / "runtime_status.json",
                {
                    "ended_at_unix": int(time.time()),
                    "elapsed_seconds": time.time() - started,
                },
            )
            docker_containers("stop")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    default_repo = Path(__file__).resolve().parents[2]
    parser.add_argument("--repo-root", type=Path, default=default_repo)
    parser.add_argument(
        "--workload",
        type=Path,
        default=default_repo
        / "results/cross_layer/workload",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_repo
        / "results/cross_layer/runs",
    )
    parser.add_argument("--session-id", default="")
    parser.add_argument("--qualification", action="store_true")
    return parser


if __name__ == "__main__":
    runner = FormalRunner(build_parser().parse_args())
    runner.run_all()
