import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


SIGNED_FIELDS = ("type", "sender", "view", "sequence", "digest", "group", "payload")


def quorum(member_count):
    if member_count < 1:
        raise ValueError("member count must be positive")
    faults = (member_count - 1) // 3
    return 2 * faults + 1


def private_key(node_id):
    seed = hashlib.sha256(f"zte-sci-rggpbft-node-{node_id}".encode("ascii")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def public_key(node_id):
    return private_key(node_id).public_key()


def canonical_bytes(message):
    unsigned = {field: message[field] for field in SIGNED_FIELDS}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def make_message(message_type, sender, view, sequence, digest, group, payload=None):
    message = {
        "type": message_type,
        "sender": int(sender),
        "view": int(view),
        "sequence": int(sequence),
        "digest": str(digest),
        "group": int(group),
        "payload": payload or {},
    }
    signature = private_key(sender).sign(canonical_bytes(message))
    message["signature"] = base64.b64encode(signature).decode("ascii")
    return message


def verify_message(message, allowed_members):
    try:
        sender = int(message["sender"])
        if sender not in set(allowed_members):
            return False
        if any(field not in message for field in SIGNED_FIELDS) or "signature" not in message:
            return False
        signature = base64.b64decode(message["signature"], validate=True)
        public_key(sender).verify(signature, canonical_bytes(message))
        return True
    except (KeyError, TypeError, ValueError):
        return False
    except Exception:
        return False


def validate_certificate(messages, *, message_type, view, sequence, digest, group, allowed_members, threshold):
    allowed_members = set(allowed_members)
    accepted = {}
    for message in messages:
        if not verify_message(message, allowed_members):
            continue
        signed_tuple = (
            message["type"],
            message["view"],
            message["sequence"],
            message["digest"],
            message["group"],
        )
        expected = (message_type, view, sequence, digest, group)
        if signed_tuple != expected:
            continue
        accepted.setdefault(message["sender"], message)
    if len(accepted) < threshold:
        raise ValueError(f"certificate has {len(accepted)} valid distinct votes; need {threshold}")
    return [accepted[sender] for sender in sorted(accepted)]


class EquivocationTracker:
    def __init__(self):
        self._digests = {}
        self.conflicts = []

    def observe(self, message):
        key = (
            message["sender"],
            message["type"],
            message["view"],
            message["sequence"],
            message["group"],
        )
        prior = self._digests.setdefault(key, message["digest"])
        if prior == message["digest"]:
            return False
        self.conflicts.append(
            {
                "sender": message["sender"],
                "type": message["type"],
                "view": message["view"],
                "sequence": message["sequence"],
                "group": message["group"],
                "first_digest": prior,
                "conflicting_digest": message["digest"],
            }
        )
        return True
