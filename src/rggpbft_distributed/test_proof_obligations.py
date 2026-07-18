import itertools
import unittest

from protocol import quorum
from view_change import expected_primary


class ProofObligationTests(unittest.TestCase):
    def test_po_intersect_leader_quorums_include_an_honest_leader(self):
        leaders = set(range(4))
        quorums = [set(values) for values in itertools.combinations(leaders, 3)]
        for first, second in itertools.product(quorums, repeat=2):
            intersection = first & second
            self.assertGreaterEqual(len(intersection), 2)
            for faulty in leaders:
                self.assertGreaterEqual(len(intersection - {faulty}), 1)

    def test_po_local_thresholds_match_group_sizes(self):
        self.assertEqual(
            {members: quorum(members) for members in (4, 6, 8)},
            {4: 3, 6: 3, 8: 5},
        )
        for members in (4, 6, 8):
            faults = (members - 1) // 3
            self.assertGreaterEqual(members, 3 * faults + 1)

    def test_po_freeze_primary_rotation_is_deterministic(self):
        rank = (7, 3, 11, 1)
        self.assertEqual(
            [expected_primary(rank, view) for view in range(8)],
            [7, 3, 11, 1, 7, 3, 11, 1],
        )


if __name__ == "__main__":
    unittest.main()
