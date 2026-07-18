import json
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent.parent / "src" / "rggpbft_distributed"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from run_grouping_ablation import build_strategy_mapping


class B6AblationTests(unittest.TestCase):
    def setUp(self):
        config_path = pathlib.Path(__file__).resolve().parent.parent / "configs" / "b6_grouping_ablation.json"
        self.config = json.loads(config_path.read_text())
        self.runs = self.config["runs"]

    def test_has_120_mappings(self):
        self.assertEqual(len(self.runs), 120)

    def test_all_have_groupingv2_run_id(self):
        for r in self.runs:
            self.assertIn("groupingv2", r["run_id"], f"{r['run_id']} missing groupingv2")

    def test_three_strategies_per_pair(self):
        by_pair = {}
        for r in self.runs:
            by_pair.setdefault(r["pair_id"], set()).add(r["grouping_strategy"])
        for pair_id, strategies in by_pair.items():
            self.assertEqual(strategies, {"fixed_modulo", "seeded_random", "reputation_round_robin"},
                             f"{pair_id}: {strategies}")

    def test_shared_seed_per_pair(self):
        by_pair = {}
        for r in self.runs:
            by_pair.setdefault(r["pair_id"], set()).add(r["seed"])
        for pair_id, seeds in by_pair.items():
            self.assertEqual(len(seeds), 1, f"{pair_id} has multiple seeds: {seeds}")

    def test_scales_and_conditions(self):
        scales = set(r["nodes"] for r in self.runs)
        rank_conds = set(r["rank_condition"] for r in self.runs)
        self.assertEqual(scales, {16, 24})
        self.assertEqual(rank_conds, {"separable", "build-then-exploit"})

    def test_all_unique_run_ids(self):
        ids = [r["run_id"] for r in self.runs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_seeded_random_leaders_follow_shuffled_positions(self):
        nodes = 16
        groups = 4
        seed = 20260705
        group_map, leaders = build_strategy_mapping(
            strategy="seeded_random",
            nodes=nodes,
            k_g=groups,
            reputation_order=list(range(nodes)),
            rng=random.Random(seed),
        )

        shuffled = list(range(nodes))
        random.Random(seed).shuffle(shuffled)
        self.assertEqual(leaders, {group: shuffled[group] for group in range(groups)})
        self.assertEqual(group_map, {node: pos % groups for pos, node in enumerate(shuffled)})
