import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

import run_v2


class RunV2Tests(unittest.TestCase):
    def test_compose_propagates_view_timeout_to_nodes(self):
        args = SimpleNamespace(
            nodes=4,
            groups=4,
            mode="pbft",
            delay_ms=5,
            rounds=1,
            round_timeout=5,
            fault_mode="none",
            fault_scenario="f3",
            fault_nodes="",
            fault_delay_ms=100,
            reputation_order="",
            image="test-image",
            view_timeout=0.5,
            seed=123,
        )
        with tempfile.TemporaryDirectory() as run_dir:
            definition = run_v2.compose_definition(args, run_dir)

        self.assertEqual(
            definition["services"]["node0"]["environment"]["VIEW_TIMEOUT_SECONDS"],
            "0.5",
        )
        self.assertEqual(
            definition["services"]["node0"]["environment"]["FAULT_SCENARIO"],
            "f3",
        )
        self.assertEqual(
            definition["services"]["node0"]["environment"]["RUN_SEED"],
            "123",
        )

    def test_ready_barrier_counts_unique_node_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "events.jsonl"
            path.write_text("\n".join([
                json.dumps({"type": "READY", "node": 0, "time_ns": 10}),
                json.dumps({"type": "READY", "node": 0, "time_ns": 11}),
                json.dumps({"type": "READY", "node": 1, "time_ns": 12}),
                json.dumps({"type": "OTHER", "node": 2, "time_ns": 13}),
            ]))
            self.assertEqual(run_v2.ready_node_ids(path), {0, 1})

    def test_first_non_ready_event_uses_unix_nanoseconds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "events.jsonl"
            path.write_text("\n".join([
                json.dumps({"type": "READY", "node": 0, "time_ns": 1_000_000_000}),
                json.dumps({"type": "REQUEST", "node": 0, "time_ns": 2_500_000_000}),
            ]))
            event = run_v2.first_non_ready_event(path)
            self.assertEqual(event["type"], "REQUEST")
            self.assertEqual(run_v2.event_unix_seconds(event), 2.5)

    def test_qdisc_cleanup_requires_every_node_and_no_netem(self):
        clean = [
            {"node": node, "iface": "eth0", "qdisc": "qdisc noqueue 0: root"}
            for node in range(4)
        ]
        self.assertTrue(run_v2.qdisc_cleanup_complete(clean, 4))
        clean[2]["qdisc"] = "qdisc netem 8001: root delay 10ms"
        self.assertFalse(run_v2.qdisc_cleanup_complete(clean, 4))


if __name__ == "__main__":
    unittest.main()
