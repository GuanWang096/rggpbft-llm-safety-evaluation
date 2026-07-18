from types import MappingProxyType
import threading


def is_sequence_bootstrap_message(message, expected_primary):
    return (
        message.get("type") == "PRE_PREPARE"
        and message.get("sender") == expected_primary
        and message.get("view") == 0
        and message.get("group") == -1
    )


class NetworkAccounting:
    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {}

    def record(self, sequence, byte_count):
        with self._lock:
            counters = self._counts.setdefault(
                int(sequence), {"messages_sent": 0, "bytes_sent": 0}
            )
            counters["messages_sent"] += 1
            counters["bytes_sent"] += int(byte_count)

    def snapshot(self, sequence):
        with self._lock:
            counters = self._counts.get(
                int(sequence), {"messages_sent": 0, "bytes_sent": 0}
            )
            return dict(counters)

    def total(self):
        with self._lock:
            return {
                "messages_sent": sum(
                    counters["messages_sent"] for counters in self._counts.values()
                ),
                "bytes_sent": sum(
                    counters["bytes_sent"] for counters in self._counts.values()
                ),
            }


class PendingPrepareBuffer:
    def __init__(self):
        self._messages = {}

    @staticmethod
    def _key(message):
        return (
            int(message["view"]),
            int(message["sequence"]),
            str(message["digest"]),
            int(message["group"]),
        )

    def add(self, message):
        bucket = self._messages.setdefault(self._key(message), {})
        bucket.setdefault(int(message["sender"]), message)

    def pop(self, view, sequence, digest, group):
        key = (int(view), int(sequence), str(digest), int(group))
        bucket = self._messages.pop(key, {})
        return [bucket[sender] for sender in sorted(bucket)]


class SequenceViewState:
    def __init__(
        self,
        *,
        sequence,
        initial_digest,
        start_ns,
        members,
        groups,
        leaders,
        rank,
    ):
        self.sequence = int(sequence)
        self.start_ns = int(start_ns)
        self.members = tuple(members)
        self.groups = MappingProxyType(
            {int(group): tuple(group_members) for group, group_members in groups.items()}
        )
        self.leaders = MappingProxyType(
            {int(group): int(leader) for group, leader in leaders.items()}
        )
        self.rank = tuple(rank)
        self.current_view = 0
        self.requested_view = 0
        self.selected_digest = str(initial_digest)
        self.prepared = None
        self.global_lock = None
        self.committed_digest = None
        self.commit_certificate = None
        self.transient = {}
        self.last_progress = 0.0

    def advance_view(self, target_view, selected_digest):
        target_view = int(target_view)
        if target_view <= self.current_view:
            raise ValueError("view must advance monotonically")
        self.current_view = target_view
        self.requested_view = max(self.requested_view, target_view)
        self.selected_digest = str(selected_digest)
        self.transient.clear()

    def record_prepared(self, view, digest, certificate):
        candidate = {
            "view": int(view),
            "digest": str(digest),
            "certificate": certificate,
        }
        if self.prepared is not None:
            if candidate["view"] < self.prepared["view"]:
                raise ValueError("cannot replace prepared state with an older view")
            if (
                candidate["view"] == self.prepared["view"]
                and candidate["digest"] != self.prepared["digest"]
            ):
                raise ValueError("conflicting prepared values at one view")
        self.prepared = candidate

    def record_global_lock(self, view, digest, certificate):
        candidate = {
            "view": int(view),
            "digest": str(digest),
            "certificate": certificate,
        }
        if self.global_lock is not None:
            if candidate["view"] < self.global_lock["view"]:
                raise ValueError("cannot replace global lock with an older view")
            if (
                candidate["view"] == self.global_lock["view"]
                and candidate["digest"] != self.global_lock["digest"]
            ):
                raise ValueError("conflicting global locks at one view")
        self.global_lock = candidate

    def record_commit(self, digest, certificate=None):
        digest = str(digest)
        if self.committed_digest is not None and self.committed_digest != digest:
            raise ValueError("conflicting commit for one sequence")
        self.committed_digest = digest
        if certificate is not None:
            self.commit_certificate = certificate

    def touch(self, monotonic_time):
        self.last_progress = float(monotonic_time)

    def is_timed_out(self, monotonic_time, timeout_seconds):
        return float(monotonic_time) - self.last_progress >= float(timeout_seconds)

    def request_next_view(self):
        self.requested_view = max(self.current_view, self.requested_view) + 1
        return self.requested_view
