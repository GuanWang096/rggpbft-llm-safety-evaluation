import hashlib
import struct
import unittest

from grouping import build_group_map, validate_reputation_order


def _seed_from(seed_base, repeat):
    material = f"test-grouping|{seed_base}|r{repeat}"
    raw = hashlib.sha256(material.encode()).digest()[:8]
    return struct.unpack(">Q", raw)[0] & 0x7FFFFFFFFFFFFFFF


class IdentityRankingRegressionTests(unittest.TestCase):
    """Identity ranking at M=16, K_g=4 must match legacy modulo grouping."""

    def test_group_map_matches_modulo_for_identity_rank(self):
        M, K_g = 16, 4
        reputation = list(range(M))
        group_map, leaders, l_gl = build_group_map(reputation, K_g)
        for node_id in range(M):
            self.assertEqual(group_map[node_id], node_id % K_g,
                             f"node {node_id}: identity rank group differs from modulo")

    def test_leaders_are_group_0_1_2_3_for_identity_rank(self):
        M, K_g = 16, 4
        reputation = list(range(M))
        _, leaders, l_gl = build_group_map(reputation, K_g)
        self.assertEqual(leaders, {0: 0, 1: 1, 2: 2, 3: 3})
        self.assertEqual(l_gl, (0, 1, 2, 3))


class NonIdentityRankingTests(unittest.TestCase):
    """Non-identity ranking must actually change group membership."""

    def test_non_identity_changes_members(self):
        M, K_g = 16, 4
        reputation = list(range(M))
        identity_map, _, _ = build_group_map(reputation, K_g)
        swapped = [5, 0, 1, 2, 3, 4, 9, 6, 7, 8, 13, 10, 11, 12, 14, 15]
        swapped_map, _, _ = build_group_map(swapped, K_g)
        for node_id in range(M):
            if node_id in {0, 5}:
                self.assertNotEqual(identity_map[node_id], swapped_map[node_id],
                                    f"node {node_id} stayed in same group")

    def test_every_group_has_correct_size(self):
        M, K_g = 16, 4
        reputation = [15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
        group_map, _, _ = build_group_map(reputation, K_g)
        for g in range(K_g):
            count = sum(1 for n in range(M) if group_map[n] == g)
            self.assertEqual(count, M // K_g, f"group {g} has {count} != {M // K_g}")


class MembershipCompletenessTests(unittest.TestCase):
    def test_each_node_appears_once(self):
        M, K_g = 16, 4
        reputation = [7, 3, 11, 0, 15, 8, 12, 1, 4, 9, 13, 2, 5, 10, 14, 6]
        group_map, _, _ = build_group_map(reputation, K_g)
        self.assertEqual(sorted(group_map.keys()), list(range(M)))
        self.assertEqual(len(set(group_map.keys())), M)

    def test_union_of_groups_equals_full_set(self):
        M, K_g = 16, 4
        reputation = [7, 3, 11, 0, 15, 8, 12, 1, 4, 9, 13, 2, 5, 10, 14, 6]
        group_map, _, _ = build_group_map(reputation, K_g)
        all_members = set()
        for g in range(K_g):
            members = {n for n in range(M) if group_map[n] == g}
            all_members.update(members)
        self.assertEqual(all_members, set(range(M)))


class LeaderCorrectnessTests(unittest.TestCase):
    def test_group_leader_is_highest_ranked_member_in_group(self):
        M, K_g = 16, 4
        reputation = [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15]
        group_map, leaders, _ = build_group_map(reputation, K_g)
        for g in range(K_g):
            group_members = [n for n in range(M) if group_map[n] == g]
            leader = leaders[g]
            leader_pos = reputation.index(leader)
            for member in group_members:
                self.assertGreaterEqual(reputation.index(member), leader_pos,
                                        f"member {member} ahead of leader {leader}")

    def test_l_gl_length_and_content(self):
        M, K_g = 16, 4
        reputation = [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15]
        _, leaders, l_gl = build_group_map(reputation, K_g)
        self.assertEqual(len(l_gl), K_g)
        self.assertEqual(set(l_gl), set(leaders.values()))
        for i, leader in enumerate(l_gl):
            self.assertLess(i, len(l_gl))
            self.assertIn(leader, leaders.values())


class ErrorRejectionTests(unittest.TestCase):
    def test_duplicate_node_rejected(self):
        with self.assertRaises(ValueError):
            validate_reputation_order([0, 0, 2, 3], 4)

    def test_missing_node_rejected(self):
        with self.assertRaises(ValueError):
            validate_reputation_order([0, 1, 2], 4)

    def test_out_of_range_node_rejected(self):
        with self.assertRaises(ValueError):
            validate_reputation_order([0, 1, 2, 4], 4)

    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            validate_reputation_order([0, 1, 2, 3, 4], 4)

    def test_indivisible_scale_rejected(self):
        with self.assertRaises(ValueError):
            build_group_map(list(range(15)), 4)


class DeterminismTests(unittest.TestCase):
    def test_same_seed_produces_same_mapping_twice(self):
        M, K_g = 16, 4
        seed = _seed_from(20260705, 1)
        reputation1 = list(range(M))
        reputation1.sort(key=lambda n: _seed_from(seed, n))
        map1, leaders1, l_gl1 = build_group_map(reputation1, K_g)
        reputation2 = list(range(M))
        reputation2.sort(key=lambda n: _seed_from(seed, n))
        map2, leaders2, l_gl2 = build_group_map(reputation2, K_g)
        self.assertEqual(map1, map2)
        self.assertEqual(leaders1, leaders2)
        self.assertEqual(l_gl1, l_gl2)
