from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mj5_common import (
    read_json,
    sign_committee_vote,
    sign_judge_output,
    validator_private_key,
)


class FabricCommandError(RuntimeError):
    pass


class MJ5FabricClient:
    def __init__(self, repo_root: Path, workload_dir: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.workload_dir = workload_dir.resolve()
        fabric_samples = self.repo_root / "src/fabric/fabric-samples"
        self.test_network = fabric_samples / "test-network"
        self.peer = fabric_samples / "bin/peer"
        self.config = fabric_samples / "config"
        organizations = self.test_network / "organizations"
        self.orderer_ca = (
            organizations
            / "ordererOrganizations/example.com/tlsca/tlsca.example.com-cert.pem"
        )
        self.peer_tls = [
            organizations
            / f"peerOrganizations/org{index}.example.com/tlsca/tlsca.org{index}.example.com-cert.pem"
            for index in (1, 2)
        ]
        self.audit_msp = (
            organizations
            / "peerOrganizations/org1.example.com/users/audit-service/msp"
        )
        self.registrations = read_json(self.workload_dir / "registrations.json")
        self.keys = read_json(self.workload_dir / "replay_keys.json")

    def environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.peer.parent}:/usr/bin:/bin",
                "FABRIC_CFG_PATH": str(self.config),
                "CORE_PEER_TLS_ENABLED": "true",
                "CORE_PEER_LOCALMSPID": "Org1MSP",
                "CORE_PEER_TLS_ROOTCERT_FILE": str(
                    self.test_network
                    / "organizations/peerOrganizations/org1.example.com/peers/peer0.org1.example.com/tls/ca.crt"
                ),
                "CORE_PEER_MSPCONFIGPATH": str(self.audit_msp),
                "CORE_PEER_ADDRESS": "localhost:7051",
            }
        )
        return environment

    @staticmethod
    def chaincode_payload(*arguments: str) -> str:
        return json.dumps({"Args": list(arguments)}, separators=(",", ":"))

    @staticmethod
    def commit_evidence(output: str) -> dict[str, Any]:
        matches = re.findall(
            r"txid \[([0-9a-fA-F]{64})\] committed with status "
            r"\(([^)]+)\) at ([^\s]+)",
            output,
        )
        transaction_ids = sorted({match[0].lower() for match in matches})
        statuses = sorted({match[1] for match in matches})
        peers = sorted({match[2] for match in matches})
        if len(transaction_ids) != 1 or statuses != ["VALID"]:
            raise FabricCommandError(
                "successful invoke did not expose one VALID transaction: "
                + output[-1200:]
            )
        return {
            "tx_id": transaction_ids[0],
            "validation_status": statuses[0],
            "endorsing_peers": peers,
        }

    def run_peer(self, arguments: list[str], timeout: float = 120) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(self.peer), *arguments],
            capture_output=True,
            text=True,
            env=self.environment(),
            timeout=timeout,
            check=False,
        )
        return result

    def invoke(
        self,
        function: str,
        *arguments: str,
        timeout: float = 120,
        expect_error: str | None = None,
        max_mvcc_retries: int = 0,
    ) -> tuple[float, str]:
        command = [
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
        for port, certificate in zip((7051, 9051), self.peer_tls):
            command.extend(
                [
                    "--peerAddresses",
                    f"localhost:{port}",
                    "--tlsRootCertFiles",
                    str(certificate),
                ]
            )
        command.extend(
            [
                "--waitForEvent",
                "--waitForEventTimeout",
                "60s",
                "-c",
                self.chaincode_payload(function, *arguments),
            ]
        )
        started = time.perf_counter()
        retry_count = 0
        while True:
            result = self.run_peer(command, timeout=timeout)
            combined = result.stdout + result.stderr
            if expect_error is not None:
                elapsed_ms = (time.perf_counter() - started) * 1000
                if result.returncode == 0 or expect_error not in combined:
                    raise FabricCommandError(
                        f"{function} did not fail with {expect_error}: {combined[-1200:]}"
                    )
                return elapsed_ms, combined
            if result.returncode == 0:
                elapsed_ms = (time.perf_counter() - started) * 1000
                return (
                    elapsed_ms,
                    combined + f"\nMJ5_MVCC_RETRIES={retry_count}",
                )
            if (
                "MVCC_READ_CONFLICT" not in combined
                or retry_count >= max_mvcc_retries
            ):
                raise FabricCommandError(
                    f"{function} failed after {retry_count} MVCC retries: "
                    f"{combined[-1200:]}"
                )
            retry_count += 1
            stable_jitter_ms = (
                int(
                    hashlib.sha256(
                        "\x1f".join(arguments).encode("utf-8")
                    ).hexdigest()[:4],
                    16,
                )
                % 100
            )
            time.sleep(
                min(0.1 * (2 ** (retry_count - 1)), 1.5)
                + stable_jitter_ms / 1000
            )

    def query(self, function: str, *arguments: str) -> dict[str, Any]:
        result = self.run_peer(
            [
                "chaincode",
                "query",
                "-C",
                "trustchannel",
                "-n",
                "tce",
                "-c",
                self.chaincode_payload(function, *arguments),
            ],
            timeout=60,
        )
        if result.returncode:
            raise FabricCommandError(
                f"{function} query failed: {(result.stdout + result.stderr)[-1200:]}"
            )
        return json.loads(result.stdout)

    def ensure_registrations(self, judge_namespace: str) -> dict[str, str]:
        judge_ids = {}
        for model, source in self.registrations["judge_registrations"].items():
            registration = dict(source)
            registration["judgeId"] = f"{judge_namespace}{model}"
            judge_ids[model] = registration["judgeId"]
            payload = json.dumps(registration, separators=(",", ":"))
            try:
                self.invoke("RegisterJudge", payload)
            except FabricCommandError as error:
                if "ERR_JUDGE_EXISTS" not in str(error):
                    raise
            state = self.query("QueryJudge", registration["judgeId"])
            for field in (
                "organization",
                "modelId",
                "modelRevision",
                "policySha256",
                "adapterVersion",
                "publicKeyHex",
            ):
                if state[field] != registration[field]:
                    raise RuntimeError(
                        f"judge registration mismatch {registration['judgeId']}:{field}"
                    )

        for registration in self.registrations["validator_registrations"]:
            payload = json.dumps(registration, separators=(",", ":"))
            try:
                self.invoke("RegisterValidator", payload)
            except FabricCommandError as error:
                if "ERR_VALIDATOR_EXISTS" not in str(error):
                    raise
            state = self.query("QueryValidator", registration["validatorId"])
            if (
                state["publicKeyHex"] != registration["publicKeyHex"]
                or state["reliabilityPpm"] != registration["reliabilityPpm"]
                or state["version"] != registration["version"]
            ):
                raise RuntimeError(
                    f"validator registration mismatch {registration['validatorId']}"
                )
        return judge_ids

    def ipfs_add(self, payload: bytes) -> tuple[str, float]:
        boundary = f"----mj5-{uuid.uuid4().hex}"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="evidence.json"\r\n'
            "Content-Type: application/json\r\n\r\n"
        ).encode("ascii") + payload + f"\r\n--{boundary}--\r\n".encode("ascii")
        request = urllib.request.Request(
            "http://localhost:5001/api/v0/add?pin=true",
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read())
        return result["Hash"], (time.perf_counter() - started) * 1000

    def ipfs_cat(self, cid: str) -> bytes:
        request = urllib.request.Request(
            f"http://localhost:5001/api/v0/cat?arg={cid}",
            data=b"",
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    def signed_outputs(
        self,
        record: dict[str, Any],
        decision_id: str,
        judge_ids: dict[str, str],
    ) -> list[dict[str, Any]]:
        outputs = []
        registrations = self.registrations["judge_registrations"]
        for model, label in record["predictions"].items():
            key_hex = self.keys["judge_keys"][model]["private_key_hex"]
            private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex))
            output = {
                "judgeId": judge_ids[model],
                "decisionId": decision_id,
                "sampleId": record["sample_id"],
                "label": label,
                "evidenceSha256": record["evidence_sha256"],
                "policySha256": registrations[model]["policySha256"],
                "adapterVersion": registrations[model]["adapterVersion"],
            }
            outputs.append(sign_judge_output(output, private_key))
        return outputs

    def freeze(
        self,
        record: dict[str, Any],
        decision_id: str,
        evidence_cid: str,
        judge_ids: dict[str, str],
        deadline_unix: int,
    ) -> tuple[dict[str, Any], float]:
        snapshot, latency, _ = self.freeze_with_details(
            record,
            decision_id,
            evidence_cid,
            judge_ids,
            deadline_unix,
        )
        return snapshot, latency

    def freeze_with_details(
        self,
        record: dict[str, Any],
        decision_id: str,
        evidence_cid: str,
        judge_ids: dict[str, str],
        deadline_unix: int,
    ) -> tuple[dict[str, Any], float, dict[str, Any]]:
        payload = {
            "decisionId": decision_id,
            "sampleId": record["sample_id"],
            "evidenceCid": evidence_cid,
            "evidenceSha256": record["evidence_sha256"],
            "judgeOutputs": self.signed_outputs(
                record, decision_id, judge_ids
            ),
            "provisionalLabel": record["provisional_label"],
            "posteriorUnsafePpm": record["posterior_unsafe_ppm"],
            "committeeQuorum": 2,
            "certificateQuorum": 3,
            "leaderValidatorIds": [
                f"validator-{node_id:02d}"
                for node_id in self.registrations["validator_order"][:4]
            ],
            "deadlineUnix": deadline_unix,
        }
        latency, output = self.invoke(
            "FreezeDecisionSnapshot",
            json.dumps(payload, separators=(",", ":")),
        )
        return (
            self.query("QueryDecisionSnapshot", decision_id),
            latency,
            self.commit_evidence(output),
        )

    def submit_vote(
        self,
        snapshot: dict[str, Any],
        node_id: int,
        vote_type: str,
    ) -> float:
        latency, _ = self.submit_vote_with_details(
            snapshot, node_id, vote_type
        )
        return latency

    def submit_vote_with_details(
        self,
        snapshot: dict[str, Any],
        node_id: int,
        vote_type: str,
    ) -> tuple[float, dict[str, Any]]:
        validator_id = f"validator-{node_id:02d}"
        vote = {
            "decisionId": snapshot["decisionId"],
            "decisionDigest": snapshot["decisionDigest"],
            "validatorId": validator_id,
            "voteType": vote_type,
            "validatorVersion": 1,
        }
        signed = sign_committee_vote(vote, validator_private_key(node_id))
        latency, output = self.invoke(
            "SubmitCommitteeVoteV15",
            json.dumps(signed, separators=(",", ":")),
        )
        return latency, self.commit_evidence(output)

    def certify(
        self,
        snapshot: dict[str, Any],
        protocol_messages: list[dict[str, Any]],
        view: int,
        sequence: int,
    ) -> float:
        latency, _ = self.certify_with_details(
            snapshot, protocol_messages, view, sequence
        )
        return latency

    def certify_with_details(
        self,
        snapshot: dict[str, Any],
        protocol_messages: list[dict[str, Any]],
        view: int,
        sequence: int,
    ) -> tuple[float, dict[str, Any]]:
        payload = {
            "decisionId": snapshot["decisionId"],
            "decisionDigest": snapshot["decisionDigest"],
            "view": view,
            "sequence": sequence,
            "protocolMessages": protocol_messages,
        }
        latency, output = self.invoke(
            "CertifyDecision", json.dumps(payload, separators=(",", ":"))
        )
        return latency, self.commit_evidence(output)

    def settle(self, decision_id: str, independent_label: str) -> float:
        latency, _ = self.settle_with_details(
            decision_id, independent_label
        )
        return latency

    def settle_with_details(
        self, decision_id: str, independent_label: str
    ) -> tuple[float, int]:
        latency, retry_count, _ = self.settle_with_commit_details(
            decision_id, independent_label
        )
        return latency, retry_count

    def settle_with_commit_details(
        self, decision_id: str, independent_label: str
    ) -> tuple[float, int, dict[str, Any]]:
        payload = {
            "decisionId": decision_id,
            "independentLabel": independent_label,
        }
        latency, output = self.invoke(
            "SettleDecision",
            json.dumps(payload, separators=(",", ":")),
            max_mvcc_retries=20,
        )
        marker = "MJ5_MVCC_RETRIES="
        retry_count = int(output.rsplit(marker, 1)[1].strip())
        return latency, retry_count, self.commit_evidence(output)
