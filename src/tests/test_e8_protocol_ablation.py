"""E8 protocol ablation tests."""
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent.parent / "src" / "rggpbft_distributed"))

from grouping import build_group_map
from generate_e8_protocol_ablation import (
    derive_pair_seed, make_identity_order, make_seeded_random_order,
    make_reputation_order, make_ranked_order, build_order,
    e8_normal_path_matrix, e8_fault_matrix, validate_strategy_differentiation,
    e8_fault_m16_main_matrix, validate_topology_evidence,
    _fault_nodes_from_lgl, SEED_BASE,
)
from aggregate_e8_protocol_ablation import recompute_event_checks


class TestE8Seeds:
    def test_normal_path_uses_b5_block(self):
        normal = e8_normal_path_matrix()
        for r in normal:
            assert "B5" in r["pair_material"], f"Expected B5 block in: {r['pair_material']}"

    def test_normal_path_runs_share_pair_material_within_config(self):
        normal = e8_normal_path_matrix()
        # Group by pair_id - all 4 strategies should exist per pair
        by_pair = {}
        for r in normal:
            by_pair.setdefault(r["pair_id"], []).append(r)
        for pair_id, runs in by_pair.items():
            strategies = {r["strategy"] for r in runs}
            assert strategies == {"pbft_baseline", "identity_round_robin", "seeded_random", "reputation_round_robin"}, \
                f"Pair {pair_id} missing strategies: {strategies}"

    def test_normal_path_has_200_runs(self):
        assert len(e8_normal_path_matrix()) == 200

    def test_fault_matrix_has_qualification_tests(self):
        fault = e8_fault_matrix()
        qual = [r for r in fault if r["fault"] in ("f2l", "f5")]
        assert len(qual) == 12

    def test_pbft_has_no_reputation_order(self):
        normal = e8_normal_path_matrix()
        pbft_runs = [r for r in normal if r["protocol"] == "pbft"]
        assert all("reputation_order" not in r for r in pbft_runs)

    def test_all_grouped_have_reputation_order(self):
        normal = e8_normal_path_matrix()
        grouped = [r for r in normal if r["protocol"] != "pbft"]
        assert all("reputation_order" in r for r in grouped)

    def test_b5_reuse_marked_correctly(self):
        normal = e8_normal_path_matrix()
        b5 = [r for r in normal if r.get("source_series") == "B5"]
        assert len(b5) == 100
        strategies_b5 = {r["strategy"] for r in b5}
        assert strategies_b5 == {"pbft_baseline", "reputation_round_robin"}

    def test_e8_new_marked_correctly(self):
        normal = e8_normal_path_matrix()
        e8_new = [r for r in normal if r.get("source_series") == "E8"]
        assert len(e8_new) == 100
        strategies_new = {r["strategy"] for r in e8_new}
        assert strategies_new == {"identity_round_robin", "seeded_random"}


class TestStrategyOrders:
    def test_identity_order_is_sequential(self):
        order = make_identity_order(16)
        assert order == ",".join(str(i) for i in range(16))

    def test_seeded_random_is_deterministic(self):
        o1 = make_seeded_random_order(16, 42)
        o2 = make_seeded_random_order(16, 42)
        assert o1 == o2

    def test_seeded_random_is_permutation(self):
        order = make_seeded_random_order(16, 12345)
        nums = [int(x) for x in order.split(",")]
        assert sorted(nums) == list(range(16))

    def test_reputation_order_is_non_identity(self):
        order = make_reputation_order(16)
        nums = [int(x) for x in order.split(",")]
        assert nums != list(range(16))
        assert sorted(nums) == list(range(16))

    def test_separable_puts_byzantine_at_end(self):
        order = make_ranked_order(16, "separable", {0, 1})
        assert order[-2:] in ([0, 1], [1, 0])

    def test_build_then_exploit_puts_byzantine_at_front(self):
        order = make_ranked_order(16, "build-then-exploit", {0, 1})
        assert order[:2] == [0, 1] or order[:2] == [1, 0]


class TestFaultNodes:
    def test_f1_injects_global_primary(self):
        rep = list(range(16))
        fn = _fault_nodes_from_lgl("f1", rep)
        assert fn == "0"

    def test_f1_injects_reputation_primary(self):
        rep = [8, 7, 11, 14, 6, 2, 15, 3, 10, 9, 5, 0, 1, 13, 12, 4]
        fn = _fault_nodes_from_lgl("f1", rep)
        assert fn == "8"

    def test_f5_injects_two_leaders(self):
        rep = list(range(16))
        fn = _fault_nodes_from_lgl("f5", rep)
        assert fn == "0,1"

    def test_none_returns_empty(self):
        assert _fault_nodes_from_lgl("none", list(range(16))) == ""


class TestPairSeedDerivation:
    def test_same_params_same_seed(self):
        m1, d1, s1 = derive_pair_seed("E8", 16, 5, "none", "na", 1)
        m2, d2, s2 = derive_pair_seed("E8", 16, 5, "none", "na", 1)
        assert m1 == m2
        assert d1 == d2
        assert s1 == s2

    def test_different_block_different_seed(self):
        _, _, s1 = derive_pair_seed("B5", 16, 5, "none", "na", 1)
        _, _, s2 = derive_pair_seed("E8", 16, 5, "none", "na", 1)
        assert s1 != s2


class TestStrategyDifferentiationStopGate:
    """Result-level tests: strategies must produce different orders."""
    def test_normal_path_strategies_differentiate(self):
        normal = e8_normal_path_matrix()
        validate_strategy_differentiation(normal, "normal_path")

    def test_fault_matrix_strategies_differentiate(self):
        fault = e8_fault_matrix()
        validate_strategy_differentiation(fault, "fault_matrix")

    def test_identity_vs_random_orders_differ(self):
        id_order = make_identity_order(16)
        rand_order = make_seeded_random_order(16, 42)
        assert id_order != rand_order, "Identity and random orders must differ"

    def test_identity_vs_reputation_orders_differ(self):
        id_order = make_identity_order(16)
        rep_order = make_reputation_order(16)
        assert id_order != rep_order, "Identity and reputation orders must differ"

    def test_random_vs_reputation_orders_differ(self):
        rand_order = make_seeded_random_order(16, 12345)
        rep_order = make_reputation_order(16)
        # Could theoretically be equal by chance, but probability is negligible
        assert rand_order != rep_order, (
            "Random and reputation orders unexpectedly identical (1/16! ~ 4.8e-14)"
        )

    def test_fault_matrix_separable_vs_build_then_exploit_differ(self):
        """Different rank conditions must produce different orders."""
        sep = make_ranked_order(16, "separable")
        bte = make_ranked_order(16, "build-then-exploit")
        assert sep != bte, "Separable and build-then-exploit must differ"


class TestFaultMatrixClaimScope:
    """Verify fault matrix uses per-strategy leader fault (recovery comparison),
    not fixed Byzantine node set (leader exposure comparison)."""
    def test_fault_nodes_derived_from_strategy_leader(self):
        """F1 should fault the strategy's first leader, not a fixed node."""
        rep_order = [8, 7, 11, 14, 6, 2, 15, 3, 10, 9, 5, 0, 1, 13, 12, 4]
        fn = _fault_nodes_from_lgl("f1", rep_order)
        assert fn == "8", f"Expected leader 8, got {fn}"

    def test_different_strategies_have_different_fault_targets(self):
        """Identity and reputation strategies have different leaders, so different fault targets."""
        id_order = [int(x) for x in make_identity_order(16).split(",")]
        rep_order = [int(x) for x in make_reputation_order(16).split(",")]
        fn_id = _fault_nodes_from_lgl("f1", id_order)
        fn_rep = _fault_nodes_from_lgl("f1", rep_order)
        assert fn_id != fn_rep, (
            f"Identity leader {fn_id} == reputation leader {fn_rep}: "
            "fault targets not differentiating between strategies"
        )


class TestM16CorrectiveMatrix:
    def test_contains_only_m16_main_fault_runs(self):
        matrix = e8_fault_m16_main_matrix()
        assert len(matrix) == 120
        assert {r["nodes"] for r in matrix} == {16}
        assert {r["fault"] for r in matrix} == {"f1", "f4"}
        assert {r["strategy"] for r in matrix} == {
            "identity_round_robin", "seeded_random", "reputation_round_robin",
        }

    def test_every_run_carries_realized_topology(self):
        matrix = e8_fault_m16_main_matrix()
        validate_topology_evidence(matrix, "m16_corrective")
        for run in matrix:
            assert len(run["group_map"]) == 16
            assert len(run["group_leaders"]) == 4
            assert run["global_primary"] == run["group_leaders"][0]
            assert run["fault_nodes"].split(",")[0] == str(run["global_primary"])

    def test_strategy_group_maps_differ_within_each_pair(self):
        matrix = e8_fault_m16_main_matrix()
        by_config = {}
        for run in matrix:
            key = (run["rank_condition"], run["fault"], run["repeat"])
            by_config.setdefault(key, []).append(run)
        assert len(by_config) == 40
        for runs in by_config.values():
            assert len({json.dumps(r["group_map"], sort_keys=True) for r in runs}) == 3


def test_raw_event_recomputation(tmp_path):
    path = tmp_path / "events.jsonl"
    events = [
        {"type": "DRIVER_RESULT", "data": {"success": True}},
        {"type": "COMMIT", "data": {"sequence": 0, "digest": "a"}},
        {"type": "COMMIT", "data": {"sequence": 0, "digest": "a"}},
    ]
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    checks = recompute_event_checks(path)
    assert checks == {
        "driver_success_count": 1,
        "safety_violation_events": 0,
        "conflicting_commit_count": 0,
        "invalid_new_view_events": 0,
    }
