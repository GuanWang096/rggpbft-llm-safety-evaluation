from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


HERE = Path(__file__).resolve().parent
EXPERIMENT = HERE.parent
if str(EXPERIMENT) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT))

from mj5_common import (
    canonical_certificate_message,
    canonical_committee_vote,
    canonical_fields,
    canonical_judge_output,
    generate_validator_order,
    judge_private_key,
    public_key_hex,
    sign_committee_vote,
    sign_judge_output,
    validator_private_key,
)


def test_canonical_fields_are_length_delimited() -> None:
    assert canonical_fields("ab", "c") != canonical_fields("a", "bc")


def test_judge_signature_binds_label_identity_and_evidence() -> None:
    key = judge_private_key("judge:test")
    output = {
        "judgeId": "qwen",
        "decisionId": "decision-1",
        "sampleId": "sample-1",
        "label": "unsafe",
        "evidenceSha256": "a" * 64,
        "policySha256": "b" * 64,
        "adapterVersion": "adapter-v1",
    }
    signed = sign_judge_output(output, key)
    key.public_key().verify(
        bytes.fromhex(signed["signatureHex"]), canonical_judge_output(signed)
    )
    tampered = dict(signed)
    tampered["label"] = "safe"
    try:
        key.public_key().verify(
            bytes.fromhex(signed["signatureHex"]), canonical_judge_output(tampered)
        )
    except Exception:
        pass
    else:
        raise AssertionError("label modification retained a valid signature")


def test_committee_vote_signature_binds_digest() -> None:
    key = validator_private_key(0)
    vote = {
        "decisionId": "decision-1",
        "decisionDigest": "c" * 64,
        "validatorId": "validator-00",
        "voteType": "ACK",
        "validatorVersion": 1,
    }
    signed = sign_committee_vote(vote, key)
    key.public_key().verify(
        bytes.fromhex(signed["signatureHex"]), canonical_committee_vote(signed)
    )


def test_certificate_message_binds_view_sequence_and_digest() -> None:
    first = canonical_certificate_message("d", "a" * 64, 1, 2)
    assert first != canonical_certificate_message("d", "b" * 64, 1, 2)
    assert first != canonical_certificate_message("d", "a" * 64, 2, 2)
    assert first != canonical_certificate_message("d", "a" * 64, 1, 3)


def test_validator_order_is_deterministic_permutation() -> None:
    first, scores = generate_validator_order(16)
    second, scores_again = generate_validator_order(16)
    assert first == second
    assert scores == scores_again
    assert set(first) == set(range(16))
    assert [scores[node_id] for node_id in first] == sorted(
        scores.values(), reverse=True
    )


def test_replay_keys_are_domain_separated() -> None:
    judge = judge_private_key("judge:test")
    validator = validator_private_key(0)
    assert public_key_hex(judge) != public_key_hex(validator)
    assert isinstance(judge, Ed25519PrivateKey)
    assert hashlib.sha256(bytes.fromhex(public_key_hex(judge))).digest()
