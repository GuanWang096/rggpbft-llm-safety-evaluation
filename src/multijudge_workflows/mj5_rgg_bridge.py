from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def extract_protocol_certificates(
    events_path: Path,
    expected: list[dict[str, Any]],
    reputation_order: list[int],
    rgg_source: Path,
) -> list[dict[str, Any]]:
    if str(rgg_source) not in sys.path:
        sys.path.insert(0, str(rgg_source))
    from protocol import quorum, validate_certificate

    leaders = list(reputation_order[:4])
    threshold = quorum(len(leaders))
    expected_by_sequence = {
        index: {
            "decision_id": entry["decision_id"],
            "digest": entry["digest"],
        }
        for index, entry in enumerate(expected)
    }
    found: dict[int, dict[str, Any]] = {}
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        data = event.get("data", {})
        certificate = data.get("global_commit_certificate")
        sequence = data.get("sequence")
        if (
            event.get("type") != "COMMIT"
            or certificate is None
            or sequence not in expected_by_sequence
            or sequence in found
        ):
            continue
        target = expected_by_sequence[sequence]
        if data.get("digest") != target["digest"]:
            continue
        view = int(data["view"])
        valid = validate_certificate(
            certificate,
            message_type="GLOBAL_COMMIT",
            view=view,
            sequence=int(sequence),
            digest=target["digest"],
            group=-1,
            allowed_members=leaders,
            threshold=threshold,
        )
        signer_ids = sorted({int(message["sender"]) for message in valid})
        found[int(sequence)] = {
            "decision_id": target["decision_id"],
            "digest": target["digest"],
            "view": view,
            "sequence": int(sequence),
            "signer_node_ids": signer_ids,
            "protocol_certificate": valid,
            "protocol_certificate_sha256": hashlib.sha256(
                json.dumps(
                    valid, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
        }
    missing = sorted(set(expected_by_sequence) - set(found))
    if missing:
        raise RuntimeError(
            f"RGG-PBFT exported no valid global certificate for sequences {missing[:10]}"
        )
    return [found[index] for index in range(len(expected))]
