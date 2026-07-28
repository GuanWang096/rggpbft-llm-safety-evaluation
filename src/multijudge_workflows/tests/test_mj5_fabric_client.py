from __future__ import annotations

import subprocess
import sys
import json
from pathlib import Path

import pytest


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
if str(EXPERIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_DIR))

from mj5_fabric_client import FabricCommandError, MJ5FabricClient


VALID_COMMIT = (
    "txid [" + "a" * 64 + "] committed with status (VALID) "
    "at localhost:7051\n"
    "txid [" + "a" * 64 + "] committed with status (VALID) "
    "at localhost:9051"
)


def completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["peer"], returncode=returncode, stdout="", stderr=stderr
    )


def bare_client(monkeypatch: pytest.MonkeyPatch, results: list[subprocess.CompletedProcess[str]]) -> MJ5FabricClient:
    client = object.__new__(MJ5FabricClient)
    client.orderer_ca = Path("/tmp/orderer-ca.pem")
    client.peer_tls = [Path("/tmp/org1.pem"), Path("/tmp/org2.pem")]
    iterator = iter(results)
    monkeypatch.setattr(client, "run_peer", lambda *_args, **_kwargs: next(iterator))
    monkeypatch.setattr("mj5_fabric_client.time.sleep", lambda _seconds: None)
    return client


def test_invoke_retries_only_explicit_mvcc_conflicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = bare_client(
        monkeypatch,
        [
            completed(1, "MVCC_READ_CONFLICT"),
            completed(1, "MVCC_READ_CONFLICT"),
            completed(0),
        ],
    )

    _latency, output = client.invoke(
        "SettleDecision", "payload", max_mvcc_retries=2
    )

    assert output.endswith("MJ5_MVCC_RETRIES=2")


def test_invoke_does_not_retry_non_mvcc_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = bare_client(monkeypatch, [completed(1, "ENDORSEMENT_POLICY_FAILURE")])

    with pytest.raises(FabricCommandError, match="after 0 MVCC retries"):
        client.invoke("SettleDecision", "payload", max_mvcc_retries=20)


def test_certify_submits_raw_protocol_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object.__new__(MJ5FabricClient)
    captured: dict[str, str] = {}

    def invoke(function: str, payload: str, **_kwargs: object) -> tuple[float, str]:
        captured["function"] = function
        captured["payload"] = payload
        return 1.25, VALID_COMMIT

    monkeypatch.setattr(client, "invoke", invoke)
    messages = [
        {
            "type": "GLOBAL_COMMIT",
            "sender": node_id,
            "view": 0,
            "sequence": 7,
            "digest": "a" * 64,
            "group": -1,
            "payload": {"start_ns": 123},
            "signature": f"signature-{node_id}",
        }
        for node_id in (1, 9, 10)
    ]
    latency = client.certify(
        {
            "decisionId": "decision-1",
            "decisionDigest": "a" * 64,
        },
        messages,
        0,
        7,
    )

    submitted = json.loads(captured["payload"])
    assert latency == 1.25
    assert captured["function"] == "CertifyDecision"
    assert submitted["protocolMessages"] == messages
    assert "signers" not in submitted


def test_freeze_binds_ranked_leader_committee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = object.__new__(MJ5FabricClient)
    client.registrations = {"validator_order": [9, 1, 10, 6, 0]}
    captured: dict[str, str] = {}
    monkeypatch.setattr(client, "signed_outputs", lambda *_args: [])
    monkeypatch.setattr(
        client,
        "invoke",
        lambda function, payload: (
            captured.update(function=function, payload=payload) or 2.0,
            VALID_COMMIT,
        ),
    )
    monkeypatch.setattr(
        client,
        "query",
        lambda *_args: {"decisionId": "decision-1"},
    )

    snapshot, latency = client.freeze(
        {
            "sample_id": "sample-1",
            "evidence_sha256": "b" * 64,
            "provisional_label": "unsafe",
            "posterior_unsafe_ppm": 800_000,
        },
        "decision-1",
        "bafy-test",
        {},
        1_700_000_100,
    )

    submitted = json.loads(captured["payload"])
    assert snapshot["decisionId"] == "decision-1"
    assert latency == 2.0
    assert submitted["leaderValidatorIds"] == [
        "validator-09",
        "validator-01",
        "validator-10",
        "validator-06",
    ]
    assert submitted["certificateQuorum"] == 3


def test_commit_evidence_requires_one_valid_transaction() -> None:
    assert MJ5FabricClient.commit_evidence(VALID_COMMIT) == {
        "tx_id": "a" * 64,
        "validation_status": "VALID",
        "endorsing_peers": ["localhost:7051", "localhost:9051"],
    }

    with pytest.raises(FabricCommandError, match="one VALID transaction"):
        MJ5FabricClient.commit_evidence(
            VALID_COMMIT.replace("(VALID)", "(MVCC_READ_CONFLICT)")
        )
