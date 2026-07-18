import unittest

from view_runtime import (
    NetworkAccounting,
    PendingPrepareBuffer,
    SequenceViewState,
    is_sequence_bootstrap_message,
)


class SequenceViewStateTests(unittest.TestCase):
    def setUp(self):
        self.state = SequenceViewState(
            sequence=7,
            initial_digest="digest-a",
            start_ns=123,
            members=(0, 1, 2, 3),
            groups={0: (0, 2), 1: (1, 3)},
            leaders={0: 0, 1: 1},
            rank=(0, 1, 2, 3),
        )

    def test_frozen_configuration_is_immutable(self):
        with self.assertRaises(TypeError):
            self.state.groups[0] = (0,)
        with self.assertRaises(TypeError):
            self.state.leaders[0] = 2

    def test_view_only_advances_monotonically(self):
        self.state.advance_view(1, "digest-b")
        self.assertEqual(self.state.current_view, 1)
        self.assertEqual(self.state.selected_digest, "digest-b")
        with self.assertRaisesRegex(ValueError, "monotonically"):
            self.state.advance_view(1, "digest-b")

    def test_global_lock_cannot_move_backwards_or_conflict(self):
        self.state.record_global_lock(2, "digest-a", {"certificate": "a"})
        with self.assertRaisesRegex(ValueError, "older"):
            self.state.record_global_lock(1, "digest-a", {"certificate": "old"})
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.state.record_global_lock(2, "digest-b", {"certificate": "b"})

    def test_committed_digest_cannot_change(self):
        self.state.record_commit("digest-a", {"certificate": "final"})
        self.state.record_commit("digest-a")
        self.assertEqual(self.state.commit_certificate, {"certificate": "final"})
        with self.assertRaisesRegex(ValueError, "conflicting commit"):
            self.state.record_commit("digest-b")

    def test_advancing_view_clears_only_transient_state(self):
        self.state.record_global_lock(0, "digest-a", {"certificate": "a"})
        self.state.transient["prepares"] = {0: "vote"}
        self.state.advance_view(1, "digest-a")

        self.assertEqual(self.state.transient, {})
        self.assertEqual(self.state.global_lock["digest"], "digest-a")

    def test_timeout_is_based_on_last_protocol_progress(self):
        self.state.touch(10.0)
        self.assertFalse(self.state.is_timed_out(10.4, 0.5))
        self.assertTrue(self.state.is_timed_out(10.5, 0.5))

    def test_repeated_timeouts_target_successive_views(self):
        self.assertEqual(self.state.request_next_view(), 1)
        self.assertEqual(self.state.request_next_view(), 2)
        self.state.advance_view(2, "digest-a")
        self.assertEqual(self.state.request_next_view(), 3)


class NetworkAccountingTests(unittest.TestCase):
    def test_counts_all_successful_messages_by_sequence(self):
        accounting = NetworkAccounting()
        accounting.record(7, 100)
        accounting.record(7, 25)

        self.assertEqual(
            accounting.snapshot(7), {"messages_sent": 2, "bytes_sent": 125}
        )
        accounting.record(8, 10)
        self.assertEqual(accounting.total(), {"messages_sent": 3, "bytes_sent": 135})


class PendingPrepareBufferTests(unittest.TestCase):
    def test_buffers_unique_senders_and_pops_only_matching_tuple(self):
        buffer = PendingPrepareBuffer()
        message = {
            "type": "PREPARE",
            "sender": 1,
            "view": 0,
            "sequence": 7,
            "digest": "digest-a",
            "group": -1,
        }
        buffer.add(message)
        buffer.add(dict(message))
        buffer.add({**message, "sender": 2})

        self.assertEqual(
            [item["sender"] for item in buffer.pop(0, 7, "digest-a", -1)],
            [1, 2],
        )
        self.assertEqual(buffer.pop(0, 7, "digest-a", -1), [])


class SequenceBootstrapTests(unittest.TestCase):
    def test_only_expected_primary_preprepare_can_bootstrap_unknown_sequence(self):
        valid = {"type": "PRE_PREPARE", "sender": 1, "view": 0, "group": -1}
        self.assertTrue(is_sequence_bootstrap_message(valid, 1))
        self.assertFalse(
            is_sequence_bootstrap_message({**valid, "type": "PREPARE"}, 1)
        )
        self.assertFalse(is_sequence_bootstrap_message({**valid, "sender": 2}, 1))
        self.assertFalse(is_sequence_bootstrap_message({**valid, "view": 1}, 1))


if __name__ == "__main__":
    unittest.main()
