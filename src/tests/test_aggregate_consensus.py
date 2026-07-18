import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aggregate_consensus import derive_grouping_manifest, paired_differences, parse_size


class AggregateConsensusTests(unittest.TestCase):
    def test_parse_size_supports_docker_binary_and_decimal_units(self):
        self.assertEqual(parse_size("1KiB"), 1024)
        self.assertEqual(parse_size("1.5MiB"), 1572864)
        self.assertEqual(parse_size("2MB"), 2_000_000)

    def test_paired_difference_is_rgg_minus_pbft(self):
        rows = [
            {"pair_id": "p1", "protocol": "pbft", "latency": 10.0},
            {"pair_id": "p1", "protocol": "rgg", "latency": 7.0},
            {"pair_id": "rgg-only", "protocol": "rgg", "latency": 5.0},
        ]

        self.assertEqual(
            paired_differences(rows, "latency"),
            [{"pair_id": "p1", "pbft": 10.0, "rgg": 7.0, "difference": -3.0}],
        )

    def test_grouping_manifest_matches_ready_events(self):
        entry = {
            "protocol": "rgg",
            "nodes": 8,
            "groups": 2,
            "reputation_order": "4,1,6,3,0,2,5,7",
        }
        ready = {
            0: {"group": 0, "leader": False, "primary": False},
            1: {"group": 1, "leader": True, "primary": False},
            2: {"group": 1, "leader": False, "primary": False},
            3: {"group": 1, "leader": False, "primary": False},
            4: {"group": 0, "leader": True, "primary": True},
            5: {"group": 0, "leader": False, "primary": False},
            6: {"group": 0, "leader": False, "primary": False},
            7: {"group": 1, "leader": False, "primary": False},
        }

        manifest = derive_grouping_manifest(entry, ready)
        self.assertEqual(manifest["group_leaders"], {"0": 4, "1": 1})
        self.assertEqual(manifest["l_gl"], [4, 1])

    def test_grouping_manifest_rejects_runtime_mismatch(self):
        entry = {
            "protocol": "rgg",
            "nodes": 4,
            "groups": 1,
            "reputation_order": "2,0,1,3",
        }
        ready = {
            node: {"group": 0, "leader": node == 0, "primary": node == 0}
            for node in range(4)
        }
        with self.assertRaises(ValueError):
            derive_grouping_manifest(entry, ready)


if __name__ == "__main__":
    unittest.main()
