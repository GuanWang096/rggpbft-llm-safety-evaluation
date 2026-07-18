import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aggregate_b2 import bootstrap_mean_interval, percentile, workflow_latencies


class AggregateB2Tests(unittest.TestCase):
    def test_workflow_latency_spans_first_start_to_last_end(self):
        operations = [
            {
                "task_id": "a",
                "started_at_ms": 100,
                "latency_ms": 20,
                "warmup": False,
                "success": True,
            },
            {
                "task_id": "a",
                "started_at_ms": 115,
                "latency_ms": 30,
                "warmup": False,
                "success": True,
            },
            {
                "task_id": "warmup",
                "started_at_ms": 0,
                "latency_ms": 999,
                "warmup": True,
                "success": True,
            },
        ]

        self.assertEqual(workflow_latencies(operations), {"a": 45})

    def test_percentile_uses_deterministic_nearest_rank_index(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5], 0.95), 4)

    def test_bootstrap_interval_is_returned_and_deterministic(self):
        first = bootstrap_mean_interval([1.0, 2.0, 3.0], 7, iterations=100)
        second = bootstrap_mean_interval([1.0, 2.0, 3.0], 7, iterations=100)

        self.assertEqual(first, second)
        self.assertEqual(set(first), {"low", "high"})


if __name__ == "__main__":
    unittest.main()
