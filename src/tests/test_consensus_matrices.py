import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from generate_consensus_matrices import b3_matrix, b4_matrix, b5_matrix


class ConsensusMatrixTests(unittest.TestCase):
    def test_b3_qualification_has_ten_unique_runs(self):
        matrix = b3_matrix()
        self.assertEqual(len(matrix), 10)
        self.assertEqual(len({run["run_id"] for run in matrix}), 10)
        self.assertTrue(all(run["nodes"] == 16 for run in matrix))

    def test_b4_fault_matrix_has_200_runs_with_expected_scope(self):
        matrix = b4_matrix()
        self.assertEqual(len(matrix), 200)
        self.assertEqual(len({run["run_id"] for run in matrix}), 200)
        self.assertEqual({run["nodes"] for run in matrix}, {16, 24})
        self.assertTrue(
            all(run["protocol"] == "rgg" for run in matrix if run["fault"] in {"f2l", "f5"})
        )

    def test_b5_performance_matrix_has_100_runs(self):
        matrix = b5_matrix()
        self.assertEqual(len(matrix), 100)
        self.assertEqual(len({run["run_id"] for run in matrix}), 100)
        self.assertEqual({run["nodes"] for run in matrix}, {16, 20, 24})
        self.assertTrue(all(run["view_timeout"] == 2.0 for run in matrix))

    def test_fault_timeout_scales_for_m24_local_docker_transport(self):
        matrix = b4_matrix()
        self.assertTrue(
            all(run["view_timeout"] == 0.5 for run in matrix if run["nodes"] == 16)
        )
        self.assertTrue(
            all(run["view_timeout"] == 2.0 for run in matrix if run["nodes"] == 24)
        )
        self.assertTrue(
            all(run["round_timeout"] == 15 for run in matrix if run["nodes"] == 24)
        )

    def test_protocol_pairs_share_seed(self):
        for matrix in (b4_matrix(), b5_matrix()):
            pairs = {}
            for run in matrix:
                pairs.setdefault(run["pair_id"], set()).add(run["seed"])
            self.assertTrue(all(len(seeds) == 1 for seeds in pairs.values()))


if __name__ == "__main__":
    unittest.main()
