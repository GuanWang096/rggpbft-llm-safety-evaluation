import random
import unittest

from e4_full_lifecycle import make_payload, summarize_records


class E4LifecycleUnitTests(unittest.TestCase):
    def test_payload_is_exact_and_deterministic(self):
        first = make_payload(random.Random(42), "task-1", 1, 1024)
        second = make_payload(random.Random(42), "task-1", 1, 1024)
        self.assertEqual(len(first), 1024)
        self.assertEqual(first, second)

    def test_summary_counts_workflows_and_operations(self):
        records = [
            {"task_id": "a", "op": "one", "success": True, "latency_ms": 10, "started_at_ms": 1000, "warmup": False},
            {"task_id": "a", "op": "two", "success": True, "latency_ms": 20, "started_at_ms": 1010, "warmup": False},
            {"task_id": "b", "op": "one", "success": False, "latency_ms": 30, "started_at_ms": 1020, "warmup": False},
            {"task_id": "w", "op": "one", "success": True, "latency_ms": 1, "started_at_ms": 0, "warmup": True},
        ]
        summary = summarize_records(records, {"a": True, "b": False, "w": True})
        self.assertEqual(summary["operation_count"], 3)
        self.assertEqual(summary["workflow_count"], 2)
        self.assertEqual(summary["successful_workflows"], 1)
        self.assertEqual(summary["failed_workflows"], 1)


if __name__ == "__main__":
    unittest.main()
