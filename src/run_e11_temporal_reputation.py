#!/usr/bin/env python3
"""E11: build-then-exploit temporal sensitivity — CPU-only simulation.

Two result classes:
  Deterministic (T0-T4): sanity check — all strategies respond immediately to
    unambiguous malicious behaviour.  Detection delay = 0 is the correct result.
  Probabilistic (P_*): realistic incomplete observation — compares cumulative
    Beta, sliding window, exponential decay and dual-score under noise,
    intermittent attacks and build-then-exploit.
"""
import argparse
import hashlib
import json
import math
import pathlib
import random
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEED_BASE = 20260705

# ---------------------------------------------------------------------------
# Beta reputation models
# ---------------------------------------------------------------------------

class CumulativeBeta:
    def __init__(self, alpha=1, beta=1):
        self.alpha0, self.beta0 = alpha, beta
        self.alpha, self.beta = alpha, beta

    def update(self, success):
        if success:
            self.alpha += 1
        else:
            self.beta += 1

    @property
    def reputation(self):
        return self.alpha / (self.alpha + self.beta)

    def reset(self):
        self.alpha, self.beta = self.alpha0, self.beta0


class SlidingWindowBeta:
    def __init__(self, window=10, alpha=1, beta=1):
        self.window = window
        self.alpha0, self.beta0 = alpha, beta
        self.history = []
        self.alpha, self.beta = alpha, beta

    def update(self, success):
        self.history.append(1 if success else 0)
        if len(self.history) > self.window:
            old = self.history.pop(0)
            if old:
                self.alpha -= 1
            else:
                self.beta -= 1
        if success:
            self.alpha += 1
        else:
            self.beta += 1

    @property
    def reputation(self):
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    def reset(self):
        self.alpha, self.beta = self.alpha0, self.beta0
        self.history = []


class ExponentialDecayBeta:
    def __init__(self, decay=0.95, alpha=1, beta=1):
        self.decay = decay
        self.alpha0, self.beta0 = alpha, beta
        self.alpha, self.beta = alpha, beta

    def update(self, success):
        self.alpha = self.decay * self.alpha + self.alpha0 * (1 - self.decay)
        self.beta = self.decay * self.beta + self.beta0 * (1 - self.decay)
        if success:
            self.alpha += 1
        else:
            self.beta += 1

    @property
    def reputation(self):
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    def reset(self):
        self.alpha, self.beta = self.alpha0, self.beta0


class DualScore:
    def __init__(self, alpha=1, beta=1):
        self.service = CumulativeBeta(alpha, beta)
        self.consensus = CumulativeBeta(alpha, beta)

    def update(self, success, honest_consensus=True):
        self.service.update(success)
        self.consensus.update(honest_consensus)

    @property
    def reputation(self):
        return 0.5 * self.service.reputation + 0.5 * self.consensus.reputation

    def reset(self):
        self.service.reset()
        self.consensus.reset()


def make_model(strategy, **kw):
    if strategy == "cumulative":
        return CumulativeBeta(**kw)
    elif strategy.startswith("sliding-w"):
        w = int(strategy.split("-")[-1])
        return SlidingWindowBeta(window=w, **kw)
    elif strategy.startswith("decay-"):
        lam = float(strategy.split("-")[-1]) / 100
        return ExponentialDecayBeta(decay=lam, **kw)
    elif strategy == "dual":
        return DualScore(**kw)
    raise ValueError(f"Unknown strategy: {strategy}")


# ---------------------------------------------------------------------------
# Behaviour generation
# ---------------------------------------------------------------------------

def generate_behavior_deterministic(scenario, total_rounds):
    """Deterministic behaviour sequences (sanity-check baseline)."""
    if scenario == "T0":
        sequences = {}
        for n in range(16):
            sequences[n] = [(False, False) if n < 2 else (True, True)
                            for _ in range(total_rounds)]
        return sequences

    elif scenario in ("T1", "T2", "T3"):
        build_rounds = {"T1": 5, "T2": 20, "T3": 50}[scenario]
        sequences = {}
        for n in range(16):
            seq = []
            for r in range(total_rounds):
                if n < 2:
                    seq.append((True, True) if r < build_rounds else (False, False))
                else:
                    seq.append((True, True))
            sequences[n] = seq
        return sequences

    elif scenario == "T4":
        sequences = {}
        for n in range(16):
            seq = []
            for r in range(total_rounds):
                if n < 2:
                    attack = (r % 5 == 0)
                    seq.append((not attack, not attack))
                else:
                    seq.append((True, True))
            sequences[n] = seq
        return sequences

    raise ValueError(f"Unknown scenario: {scenario}")


def generate_behavior_probabilistic(build_rounds, total_rounds, honest_p,
                                    byz_exploit_p, obs_error_p, rng):
    """Probabilistic behaviour with observation noise.

    honest_p:      P(success) for honest nodes each round
    byz_exploit_p: P(success) for Byzantine nodes during exploit phase
    obs_error_p:   probability the observation is flipped before model update
    rng:           random.Random instance seeded deterministically
    """
    sequences = {}
    for n in range(16):
        seq = []
        for r in range(total_rounds):
            if n < 2:  # Byzantine nodes
                if r < build_rounds:
                    # Build phase: behave like honest
                    actual_success = rng.random() < honest_p
                else:
                    # Exploit phase: malicious with probability (1 - byz_exploit_p)
                    actual_success = rng.random() < byz_exploit_p
            else:  # Honest nodes
                actual_success = rng.random() < honest_p

            # Observation error: flip the observed outcome
            observed_success = actual_success
            if rng.random() < obs_error_p:
                observed_success = not actual_success

            seq.append((observed_success, observed_success))
        sequences[n] = seq
    return sequences


# ---------------------------------------------------------------------------
# Group mapping
# ---------------------------------------------------------------------------

def compute_group_assignment(reputations, k_g=4):
    """Map nodes to groups based on reputation rank, round-robin."""
    ranked = sorted(reputations.items(), key=lambda x: (-x[1], x[0]))
    group_map = {}
    leaders = {}
    for pos, (node_id, _) in enumerate(ranked):
        g = pos % k_g
        group_map[node_id] = g
        if g not in leaders:
            leaders[g] = node_id
    l_gl = [leaders[g] for g in range(k_g)]
    return group_map, leaders, l_gl


# ---------------------------------------------------------------------------
# Seed derivation
# ---------------------------------------------------------------------------

def derive_simulation_seed(scenario, seed_index):
    """Deterministic SHA-256 seed derivation. No Python hash() dependency."""
    material = f"zte-sci-e11-v2|{SEED_BASE}|{scenario}|seed={seed_index}"
    digest = hashlib.sha256(material.encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFF


def find_attack_start(scenario, build_rounds_map=None):
    """Return the round index where Byzantine nodes start attacking."""
    if scenario == "T0":
        return 0
    elif scenario == "T1":
        return 5
    elif scenario == "T2":
        return 20
    elif scenario == "T3":
        return 50
    elif scenario == "T4":
        return 0
    # Probabilistic scenarios encode build length in name: P_T1_...
    if scenario.startswith("P_"):
        parts = scenario.split("_")
        for p in parts:
            if p.startswith("B") and p[1:].isdigit():
                return int(p[1:])
        # Fallback: parse from T1/T2/T3 in name
        if "T1" in scenario:
            return 5
        elif "T2" in scenario:
            return 20
        elif "T3" in scenario:
            return 50
    return 0


# ---------------------------------------------------------------------------
# Single-run simulation kernel
# ---------------------------------------------------------------------------

def run_one(behavior, strategy, m, k_g, total_rounds, attack_start,
            byzantine_nodes, collect_trace=False):
    """Run one simulation and return metrics dict."""
    models = {n: make_model(strategy) for n in range(m)}

    # Leader tracking
    byz_leader_slots = 0
    byz_leader_rounds = 0
    byz_was_ever_leader = {n: False for n in byzantine_nodes}
    prev_leaders = set()

    # Detection: first-exclusion delay per node (-1 = never excluded)
    first_exclusion_delay = {n: -1 for n in byzantine_nodes}
    # Cold-start: attacker excluded at attack_start, never becomes leader afterward.
    # Determined at the end of simulation: if a Byzantine node was never a leader
    # AND the attack started at round 0, it's cold-start excluded (not missed detection).
    # Stable detection: H consecutive rounds not in leader set
    consecutive_absent = {n: 0 for n in byzantine_nodes}
    stable_h3_delay = {n: -1 for n in byzantine_nodes}
    stable_h5_delay = {n: -1 for n in byzantine_nodes}

    # Miss tracking (post-attack window)
    post_attack_rounds = 0
    post_attack_byz_leader_rounds = 0

    # Re-entry tracking
    excluded_once = {n: False for n in byzantine_nodes}
    re_entry_count = {n: 0 for n in byzantine_nodes}
    was_leader_prev = {n: False for n in byzantine_nodes}

    # Honest demotion tracking
    honest_demotions = 0
    total_honest_leader_changes = 0
    trace = []

    for round_idx in range(total_rounds):
        # Update models
        for n in range(m):
            service_success, consensus_honest = behavior[n][round_idx]
            if strategy == "dual":
                models[n].update(service_success, consensus_honest)
            else:
                models[n].update(service_success)

        # Compute reputation and grouping
        reps = {n: models[n].reputation for n in range(m)}
        _, _, l_gl = compute_group_assignment(reps, k_g)
        curr_leaders = set(l_gl)

        if collect_trace:
            trace.append({
                "round": round_idx,
                "leaders": l_gl,
                "byzantine_leaders": [n for n in l_gl if n in byzantine_nodes],
                "byzantine_reputations": {
                    str(n): round(reps[n], 6) for n in sorted(byzantine_nodes)
                },
                "mean_honest_reputation": round(
                    sum(reps[n] for n in range(m) if n not in byzantine_nodes)
                    / (m - len(byzantine_nodes)),
                    6,
                ),
            })

        # Byzantine leader exposure
        byz_leaders_this_round = [n for n in l_gl if n in byzantine_nodes]
        byz_leader_slots += len(byz_leaders_this_round)
        if byz_leaders_this_round:
            byz_leader_rounds += 1
            for n in byz_leaders_this_round:
                byz_was_ever_leader[n] = True

        # Per-node leader status
        is_leader_now = {n: n in curr_leaders for n in byzantine_nodes}

        # Post-attack metrics
        if round_idx >= attack_start:
            post_attack_rounds += 1
            if byz_leaders_this_round:
                post_attack_byz_leader_rounds += 1

        # First-exclusion delay + stable detection
        for n in byzantine_nodes:
            if round_idx >= attack_start and byz_was_ever_leader[n]:
                if is_leader_now[n]:
                    consecutive_absent[n] = 0
                else:
                    consecutive_absent[n] += 1
                    # First exclusion
                    if first_exclusion_delay[n] == -1:
                        first_exclusion_delay[n] = round_idx - attack_start
                    # Stable H=3
                    if stable_h3_delay[n] == -1 and consecutive_absent[n] >= 3:
                        stable_h3_delay[n] = round_idx - attack_start - 2
                    # Stable H=5
                    if stable_h5_delay[n] == -1 and consecutive_absent[n] >= 5:
                        stable_h5_delay[n] = round_idx - attack_start - 4

            # Re-entry: was excluded, now back in leader set
            if excluded_once[n] and is_leader_now[n] and not was_leader_prev[n]:
                re_entry_count[n] += 1
            if first_exclusion_delay[n] >= 0:
                excluded_once[n] = True

            was_leader_prev[n] = is_leader_now[n]

        # Honest demotion
        if round_idx > 0:
            newly_demoted = prev_leaders - curr_leaders
            newly_promoted = curr_leaders - prev_leaders
            total_honest_leader_changes += len(newly_demoted) + len(newly_promoted)
            honest_demotions += sum(1 for n in newly_demoted if n not in byzantine_nodes)

        prev_leaders = curr_leaders

    # Final stats
    final_reps = {n: models[n].reputation for n in range(m)}
    _, _, final_l_gl = compute_group_assignment(final_reps, k_g)
    final_byz_leaders = sum(1 for n in final_l_gl if n in byzantine_nodes)

    # Aggregate detection metrics
    # Cold-start: attacker never became leader post-attack (excluded before any exposure)
    cold_start_excluded = {}
    for n in byzantine_nodes:
        cold_start_excluded[n] = not byz_was_ever_leader[n]
    cold_start_count = sum(1 for v in cold_start_excluded.values() if v)
    # Post-exposure detection: only for nodes that were ever leaders after attack
    fe_delays = [d for n, d in first_exclusion_delay.items()
                 if d >= 0 and not cold_start_excluded[n]]
    sh3_delays = [d for n, d in stable_h3_delay.items()
                  if d >= 0 and not cold_start_excluded[n]]
    sh5_delays = [d for n, d in stable_h5_delay.items()
                  if d >= 0 and not cold_start_excluded[n]]
    # True missed: never excluded AND was a leader post-attack (not cold-start)
    missed_fe = sum(1 for n, d in first_exclusion_delay.items()
                    if d == -1 and byz_was_ever_leader[n])
    total_re_entries = sum(re_entry_count.values())

    miss_rate = (post_attack_byz_leader_rounds / post_attack_rounds
                 if post_attack_rounds > 0 else 0.0)
    honest_demotion_rate = (honest_demotions / total_honest_leader_changes
                            if total_honest_leader_changes > 0 else 0.0)

    metrics = {
        "final_byzantine_leaders": final_byz_leaders,
        "byzantine_leader_rounds": byz_leader_rounds,
        "byzantine_leader_slots": byz_leader_slots,
        "cold_start_excluded_count": cold_start_count,
        "cold_start_excluded": [n for n, v in cold_start_excluded.items() if v],
        "post_exposure_first_exclusion_delays": fe_delays,
        "mean_post_exposure_first_exclusion_delay": (sum(fe_delays) / len(fe_delays)
                                                     if fe_delays else -1),
        "post_exposure_missed_detection": missed_fe,
        "stable_h3_delays": sh3_delays,
        "mean_stable_h3_delay": (sum(sh3_delays) / len(sh3_delays)
                                 if sh3_delays else -1),
        "stable_h5_delays": sh5_delays,
        "mean_stable_h5_delay": (sum(sh5_delays) / len(sh5_delays)
                                 if sh5_delays else -1),
        "post_attack_miss_rate": miss_rate,
        "re_entry_count": total_re_entries,
        "honest_demotions": honest_demotions,
        "honest_demotion_rate": honest_demotion_rate,
        "final_reputations": {str(k): round(v, 6) for k, v in final_reps.items()},
    }
    if collect_trace:
        metrics["trace"] = trace
    return metrics


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------

DETERMINISTIC_SCENARIOS = ["T0", "T1", "T2", "T3", "T4"]

PROBABILISTIC_BUILD_LENGTHS = [5, 20, 50]       # T1, T2, T3
PROBABILISTIC_HONEST_P = [0.95, 0.98]
PROBABILISTIC_BYZ_EXPLOIT_P = [0.2, 0.5, 0.8]
PROBABILISTIC_OBS_ERROR_P = [0.0, 0.02, 0.05]
STABLE_H_WINDOWS = [3, 5]

DEFAULT_STRATEGIES = [
    "cumulative", "sliding-w-5", "sliding-w-10", "sliding-w-20",
    "decay-90", "decay-95", "decay-99",
]
# "dual" is experimental — its two channels receive identical observations,
# making it equivalent to cumulative Beta. Use --strategies dual to enable.
EXPERIMENTAL_STRATEGIES = ["dual"]


def build_probabilistic_scenario_name(build_len, honest_p, byz_p, err_p):
    """e.g. P_T1_h098_b050_e00"""
    t_label = {5: "T1", 20: "T2", 50: "T3"}[build_len]
    return "P_%s_h%03d_b%03d_e%02d" % (
        t_label, int(honest_p * 100), int(byz_p * 100), int(err_p * 100),
    )


def simulate(m=16, k_g=4, strategies=None, scenarios=None,
             total_rounds=100, seeds=100, output_dir=None,
             probabilistic=True):
    if strategies is None:
        strategies = DEFAULT_STRATEGIES
    if scenarios is None:
        scenarios = DETERMINISTIC_SCENARIOS[:]

    byzantine_nodes = {0, 1}
    results = []

    # Expand scenarios: if "P_ALL" in scenarios, generate full probabilistic matrix
    run_scenarios = []
    for s in scenarios:
        if s == "P_ALL":
            for build_len in PROBABILISTIC_BUILD_LENGTHS:
                for hp in PROBABILISTIC_HONEST_P:
                    for bp in PROBABILISTIC_BYZ_EXPLOIT_P:
                        for ep in PROBABILISTIC_OBS_ERROR_P:
                            run_scenarios.append(
                                build_probabilistic_scenario_name(build_len, hp, bp, ep)
                            )
        else:
            run_scenarios.append(s)

    for scenario in run_scenarios:
        is_prob = scenario.startswith("P_")

        if is_prob:
            # Parse scenario name: P_T1_h098_b050_e00
            parts = scenario.split("_")
            t_label = parts[1]  # T1, T2, T3
            build_len = {"T1": 5, "T2": 20, "T3": 50}[t_label]
            hp_str = [p for p in parts if p.startswith("h")][0]
            bp_str = [p for p in parts if p.startswith("b")][0]
            ep_str = [p for p in parts if p.startswith("e")][0]
            honest_p = int(hp_str[1:]) / 100.0
            byz_p = int(bp_str[1:]) / 100.0
            err_p = int(ep_str[1:]) / 100.0
            attack_start = build_len
        else:
            attack_start = find_attack_start(scenario)

        for seed_i in range(seeds):
            seed = derive_simulation_seed(scenario, seed_i)
            rng = random.Random(seed)

            if is_prob:
                behavior = generate_behavior_probabilistic(
                    build_len, total_rounds, honest_p, byz_p, err_p, rng,
                )
            else:
                behavior = generate_behavior_deterministic(scenario, total_rounds)

            for strategy in strategies:
                metrics = run_one(
                    behavior, strategy, m, k_g, total_rounds, attack_start,
                    byzantine_nodes,
                )
                results.append({
                    "scenario": scenario,
                    "strategy": strategy,
                    "seed_index": seed_i,
                    "seed": seed,
                    "nodes": m,
                    "groups": k_g,
                    "total_rounds": total_rounds,
                    "attack_start_round": attack_start,
                    **({"honest_p": honest_p, "byz_exploit_p": byz_p,
                        "obs_error_p": err_p} if is_prob else {}),
                    **metrics,
                })

    if output_dir:
        output_dir = pathlib.Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        with (output_dir / "results.jsonl").open("w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")

        summary = {
            "total_runs": len(results),
            "deterministic_scenarios": [s for s in run_scenarios if not s.startswith("P_")],
            "probabilistic_scenarios": [s for s in run_scenarios if s.startswith("P_")],
            "strategies": strategies,
            "nodes": m,
            "groups": k_g,
            "total_rounds": total_rounds,
            "seeds": seeds,
            "stable_h_windows": STABLE_H_WINDOWS,
        }
        (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--nodes", type=int, default=16)
    parser.add_argument("--groups", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("--scenarios", nargs="*", default=None)
    parser.add_argument("--strategies", nargs="*", default=None)
    parser.add_argument("--deterministic-only", action="store_true",
                        help="Run only deterministic sanity-check scenarios")
    parser.add_argument("--probabilistic-only", action="store_true",
                        help="Run only probabilistic scenarios")
    args = parser.parse_args()

    scenarios = args.scenarios
    if scenarios is None:
        if args.deterministic_only:
            scenarios = DETERMINISTIC_SCENARIOS[:]
        elif args.probabilistic_only:
            scenarios = ["P_ALL"]
        else:
            scenarios = DETERMINISTIC_SCENARIOS[:] + ["P_ALL"]

    results = simulate(
        m=args.nodes, k_g=args.groups,
        total_rounds=args.rounds, seeds=args.seeds,
        scenarios=scenarios, strategies=args.strategies,
        output_dir=args.output_dir,
    )
    n_det = sum(1 for r in results if not r["scenario"].startswith("P_"))
    n_prob = sum(1 for r in results if r["scenario"].startswith("P_"))
    print("E11 complete: %d runs (%d deterministic + %d probabilistic) -> %s"
          % (len(results), n_det, n_prob, args.output_dir))


if __name__ == "__main__":
    main()
