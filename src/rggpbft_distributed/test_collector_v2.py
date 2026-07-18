import unittest
from unittest.mock import patch

import collector_v2


class CollectorTests(unittest.TestCase):
    def test_collection_completes_only_after_finish_and_all_nodes_stop(self):
        events = [
            {"type": "FINISH", "node": -1, "data": {}},
            {"type": "STOPPED", "node": 0, "data": {}},
            {"type": "STOPPED", "node": 1, "data": {}},
        ]

        self.assertFalse(collector_v2.collection_complete(events, 3))
        events.append({"type": "STOPPED", "node": 2, "data": {}})
        self.assertTrue(collector_v2.collection_complete(events, 3))

    @patch.object(collector_v2, "M", 4)
    @patch.object(collector_v2, "N_ROUNDS", 1)
    def test_summary_deduplicates_same_node_sequence_commit_events(self):
        events = [
            {
                "type": "COMMIT",
                "node": 1,
                "data": {"sequence": 0, "digest": "a", "latency_ms": 9},
            },
            {
                "type": "COMMIT",
                "node": 1,
                "data": {"sequence": 0, "digest": "a", "latency_ms": 10},
            },
            {
                "type": "COMMIT",
                "node": 2,
                "data": {"sequence": 0, "digest": "a", "latency_ms": 11},
            },
        ]

        summary = collector_v2.summarize(events)

        self.assertEqual(summary["node_commit_count"], 2)
        self.assertEqual(summary["node_commit_completeness"], 0.5)
        self.assertEqual(summary["duplicate_commit_events"], 1)

    @patch.object(collector_v2, "M", 4)
    @patch.object(collector_v2, "N_ROUNDS", 1)
    def test_summary_detects_conflicting_commits(self):
        events = [
            {
                "type": "FAULT_INJECTED",
                "time_ns": 900_000_000,
                "data": {"sequence": 0, "view": 0, "scenario": "f1"},
            },
            {"type": "DRIVER_RESULT", "data": {"success": True, "latency_ms": 10}},
            {"type": "COMMIT", "data": {"sequence": 0, "digest": "a", "latency_ms": 9}},
            {"type": "COMMIT", "data": {"sequence": 0, "digest": "b", "latency_ms": 10}},
        ]
        summary = collector_v2.summarize(events)
        self.assertEqual(summary["driver_success_rate"], 1.0)
        self.assertEqual(summary["conflicting_commit_count"], 1)

    @patch.object(collector_v2, "M", 4)
    @patch.object(collector_v2, "N_ROUNDS", 1)
    def test_summary_reports_view_change_recovery_metrics(self):
        events = [
            {
                "type": "FAULT_INJECTED",
                "time_ns": 900_000_000,
                "data": {"sequence": 0, "view": 0, "scenario": "f1"},
            },
            {
                "type": "VIEW_CHANGE_SENT",
                "time_ns": 1_000_000_000,
                "data": {"sequence": 0, "target_view": 1},
            },
            {
                "type": "NEW_VIEW_ACCEPTED",
                "time_ns": 1_100_000_000,
                "data": {"sequence": 0, "view": 1},
            },
            {
                "type": "DRIVER_RESULT",
                "time_ns": 1_300_000_000,
                "data": {"sequence": 0, "success": True, "latency_ms": 300},
            },
            {
                "type": "COMMIT",
                "time_ns": 1_250_000_000,
                "data": {
                    "sequence": 0,
                    "view": 1,
                    "digest": "a",
                    "latency_ms": 250,
                    "messages_sent": 12,
                    "bytes_sent": 3456,
                },
            },
            {
                "type": "STOPPED",
                "time_ns": 1_400_000_000,
                "data": {"messages_sent": 15, "bytes_sent": 4000},
            },
        ]

        summary = collector_v2.summarize(events)

        self.assertEqual(summary["view_change_sent_events"], 1)
        self.assertEqual(summary["fault_injected_events"], 1)
        self.assertEqual(summary["invalid_view_change_events"], 0)
        self.assertEqual(summary["new_view_accepted_events"], 1)
        self.assertEqual(summary["max_accepted_view"], 1)
        self.assertEqual(summary["recovered_sequence_count"], 1)
        self.assertEqual(summary["recovery_latency_ms"]["p50"], 250.0)
        self.assertEqual(summary["reported_protocol_messages_sent"], 12)
        self.assertEqual(summary["reported_protocol_bytes_sent"], 3456)
        self.assertEqual(summary["final_protocol_messages_sent"], 15)
        self.assertEqual(summary["final_protocol_bytes_sent"], 4000)


if __name__ == "__main__":
    unittest.main()
