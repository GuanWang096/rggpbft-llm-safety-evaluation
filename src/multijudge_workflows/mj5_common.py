from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any, Iterable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ONE_PPM = 1_000_000
SEED_BASE = 20260705
PRIMARY_COMMITTEE = ("qwen", "safework", "minicpm")
EXTENDED_COMMITTEE = ("qwen", "safework", "internvl", "minicpm")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("ascii")


def canonical_fields(*fields: str) -> bytes:
    output = bytearray()
    for field in fields:
        encoded = field.encode("utf-8")
        output.extend(struct.pack(">Q", len(encoded)))
        output.extend(encoded)
    return bytes(output)


def deterministic_private_key(domain: str, identity: str) -> Ed25519PrivateKey:
    seed = hashlib.sha256(f"{domain}|{identity}".encode("utf-8")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def judge_private_key(canonical_id: str) -> Ed25519PrivateKey:
    return deterministic_private_key("zte-sci-mj5-replay-judge-v1", canonical_id)


def validator_private_key(node_id: int) -> Ed25519PrivateKey:
    seed = hashlib.sha256(f"zte-sci-rggpbft-node-{node_id}".encode("ascii")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key_hex(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return raw.hex()


def public_key_fingerprint(private_key: Ed25519PrivateKey) -> str:
    return hashlib.sha256(bytes.fromhex(public_key_hex(private_key))).hexdigest()


def canonical_judge_output(output: dict[str, Any]) -> bytes:
    return canonical_fields(
        "MJ5-JUDGE-OUTPUT-v1",
        output["judgeId"],
        output["decisionId"],
        output["sampleId"],
        output["label"],
        output["evidenceSha256"],
        output["policySha256"],
        output["adapterVersion"],
    )


def sign_judge_output(
    output: dict[str, Any],
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    signed = dict(output)
    signed["signatureHex"] = private_key.sign(canonical_judge_output(signed)).hex()
    return signed


def canonical_committee_vote(vote: dict[str, Any]) -> bytes:
    return canonical_fields(
        "MJ5-COMMITTEE-VOTE-v1",
        vote["decisionId"],
        vote["decisionDigest"],
        vote["validatorId"],
        vote["voteType"],
        str(vote["validatorVersion"]),
    )


def sign_committee_vote(
    vote: dict[str, Any],
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    signed = dict(vote)
    signed["signatureHex"] = private_key.sign(canonical_committee_vote(signed)).hex()
    return signed


def canonical_certificate_message(
    decision_id: str,
    decision_digest: str,
    view: int,
    sequence: int,
) -> bytes:
    return canonical_fields(
        "MJ5-RGG-CERTIFICATE-v1",
        decision_id,
        decision_digest,
        str(view),
        str(sequence),
    )


def certificate_signers(
    validator_ids: Iterable[int],
    decision_id: str,
    decision_digest: str,
    view: int,
    sequence: int,
) -> list[dict[str, Any]]:
    message = canonical_certificate_message(
        decision_id, decision_digest, view, sequence
    )
    return [
        {
            "validatorId": f"validator-{node_id:02d}",
            "validatorVersion": 1,
            "signatureHex": validator_private_key(node_id).sign(message).hex(),
        }
        for node_id in validator_ids
    ]


def likelihood_score(
    predictions: dict[str, str],
    reliability: dict[str, tuple[float, float]],
    prior_unsafe: float,
) -> float:
    log_odds = math.log(prior_unsafe / (1.0 - prior_unsafe))
    for judge_id, prediction in predictions.items():
        g_unsafe, g_safe = reliability[judge_id]
        if prediction == "unsafe":
            log_odds += math.log(g_unsafe / (1.0 - g_safe))
        else:
            log_odds += math.log((1.0 - g_unsafe) / g_safe)
    log_odds = max(-40.0, min(40.0, log_odds))
    return 1.0 / (1.0 + math.exp(-log_odds))


def generate_validator_order(node_count: int = 16) -> tuple[list[int], dict[int, int]]:
    raw_scores: dict[int, int] = {}
    for node_id in range(node_count):
        material = f"reputation-v1|{node_count}|{SEED_BASE}|{node_id}".encode()
        raw_scores[node_id] = struct.unpack(
            ">Q", hashlib.sha256(material).digest()[:8]
        )[0]
    order = sorted(range(node_count), key=lambda node_id: (-raw_scores[node_id], node_id))
    denominator = (1 << 64) - 1
    ppm = {
        node_id: 600_000 + (350_000 * raw_scores[node_id] // denominator)
        for node_id in range(node_count)
    }
    return order, ppm


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]
