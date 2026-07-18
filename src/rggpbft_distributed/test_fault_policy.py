import unittest

from fault_policy import FaultPolicy, split_equivocation_targets


class FaultPolicyTests(unittest.TestCase):
    def policy(self, scenario, node_id=0, fault_nodes=(0,)):
        return FaultPolicy(
            scenario=scenario,
            node_id=node_id,
            fault_nodes=fault_nodes,
            ranked_primaries=(0, 1, 2, 3),
        )

    def test_f1_crashes_initial_primary_before_proposal(self):
        policy = self.policy("f1")
        self.assertEqual(policy.before_proposal(0), "crash")
        self.assertIsNone(policy.before_proposal(1))

    def test_f2_crashes_after_preprepare(self):
        policy = self.policy("f2")
        self.assertEqual(policy.after_preprepare(0), "crash")
        self.assertEqual(
            policy.preprepare_targets(0, range(4), mode="pbft", quorum_size=3),
            (1,),
        )
        self.assertEqual(
            policy.preprepare_targets(0, range(4), mode="rgg", quorum_size=3),
            (1,),
        )

    def test_f2l_triggers_only_after_first_local_certificate(self):
        policy = self.policy("f2l")
        self.assertEqual(policy.after_group_certificate(0, 1), "crash")
        self.assertIsNone(policy.after_group_certificate(0, 0))

    def test_f3_triggers_after_protocol_lock(self):
        policy = self.policy("f3")
        self.assertEqual(policy.after_protocol_lock(0), "crash")
        backup = self.policy("f3", node_id=2)
        self.assertEqual(
            backup.prepare_targets(0, range(4), mode="pbft"), (0,)
        )
        self.assertEqual(
            backup.prepare_targets(1, range(4), mode="pbft"), (0, 1, 3)
        )

    def test_f4_equivocates_only_from_current_primary(self):
        policy = self.policy("f4")
        self.assertTrue(policy.equivocate_preprepare(0))
        self.assertFalse(policy.equivocate_preprepare(1))

    def test_f4_split_does_not_depend_on_node_id_parity(self):
        self.assertEqual(split_equivocation_targets((2, 4, 10, 12), sender=2), (10,))

    def test_f4_split_is_deterministic_for_unsorted_targets(self):
        first = split_equivocation_targets((12, 4, 10, 2), sender=2)
        second = split_equivocation_targets((10, 12, 2, 4), sender=2)
        self.assertEqual(first, second)
        self.assertTrue(first)

    def test_f5_omits_two_consecutive_primary_proposals_without_permanent_crash(self):
        first = self.policy("f5", node_id=0, fault_nodes=(0, 1))
        second = self.policy("f5", node_id=1, fault_nodes=(0, 1))
        self.assertEqual(first.before_proposal(0), "omit")
        self.assertEqual(second.before_proposal(1), "omit")
        self.assertIsNone(first.before_proposal(2))


if __name__ == "__main__":
    unittest.main()
