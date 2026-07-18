#!/usr/bin/env python3
"""Aggregate and package E11 temporal-reputation evidence."""
import argparse
import csv
import hashlib
import json
import pathlib
import platform
import random
import statistics
import sys
from collections import defaultdict

from run_e11_temporal_reputation import (
    derive_simulation_seed,
    generate_behavior_deterministic,
    generate_behavior_probabilistic,
    run_one,
)


def percentile(values, probability):
    if not values:
        return -1
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * probability))
    return ordered[index]


def mean_ci95(values, bootstrap_seed=20260705, samples=2000):
    """Return a deterministic percentile-bootstrap interval for the mean."""
    if not values:
        return [-1, -1]
    if len(values) == 1:
        return [values[0], values[0]]
    rng = random.Random(bootstrap_seed)
    means = []
    for _ in range(samples):
        means.append(statistics.fmean(rng.choice(values) for _ in values))
    return [percentile(means, 0.025), percentile(means, 0.975)]


def aggregate_group(group):
    if not group:
        raise ValueError("cannot aggregate an empty group")
    n = len(group)
    attacker_count = n * 2
    cold_count = sum(r.get("cold_start_excluded_count", 0) for r in group)
    exposed_count = attacker_count - cold_count
    missed_count = sum(r.get("post_exposure_missed_detection", 0) for r in group)
    first_delays = [
        delay
        for run in group
        for delay in run.get("post_exposure_first_exclusion_delays", [])
        if delay >= 0
    ]
    h3_delays = [
        delay for run in group for delay in run.get("stable_h3_delays", []) if delay >= 0
    ]
    h5_delays = [
        delay for run in group for delay in run.get("stable_h5_delays", []) if delay >= 0
    ]

    def mean(key):
        return statistics.fmean(run.get(key, 0) for run in group)

    return {
        "scenario": group[0]["scenario"],
        "strategy": group[0]["strategy"],
        "count": n,
        "attacker_population": attacker_count,
        "cold_start_excluded_count": cold_count,
        "cold_start_exclusion_rate": cold_count / attacker_count,
        "post_exposure_population": exposed_count,
        "post_exposure_missed_detection_count": missed_count,
        "post_exposure_miss_rate": missed_count / exposed_count if exposed_count else 0.0,
        "mean_post_exposure_first_exclusion_delay": (
            statistics.fmean(first_delays) if first_delays else -1
        ),
        "p50_post_exposure_first_exclusion_delay": percentile(first_delays, 0.50),
        "p95_post_exposure_first_exclusion_delay": percentile(first_delays, 0.95),
        "mean_stable_h3_delay": statistics.fmean(h3_delays) if h3_delays else -1,
        "p50_stable_h3_delay": percentile(h3_delays, 0.50),
        "stable_h3_miss_rate": (
            (exposed_count - len(h3_delays)) / exposed_count if exposed_count else 0.0
        ),
        "mean_stable_h5_delay": statistics.fmean(h5_delays) if h5_delays else -1,
        "p50_stable_h5_delay": percentile(h5_delays, 0.50),
        "stable_h5_miss_rate": (
            (exposed_count - len(h5_delays)) / exposed_count if exposed_count else 0.0
        ),
        "mean_final_byzantine_leaders": mean("final_byzantine_leaders"),
        "mean_byzantine_leader_rounds": mean("byzantine_leader_rounds"),
        "mean_byzantine_leader_slots": mean("byzantine_leader_slots"),
        "mean_post_attack_miss_rate": mean("post_attack_miss_rate"),
        "mean_re_entry_count": mean("re_entry_count"),
        "total_re_entries": sum(r.get("re_entry_count", 0) for r in group),
        "mean_honest_demotions": mean("honest_demotions"),
        "mean_honest_demotion_rate": mean("honest_demotion_rate"),
        "honest_p": group[0].get("honest_p"),
        "byz_exploit_p": group[0].get("byz_exploit_p"),
        "obs_error_p": group[0].get("obs_error_p"),
    }


def paired_comparisons(runs, baseline="cumulative"):
    """Compare each strategy with the baseline using identical scenario seeds."""
    by_scenario_seed = defaultdict(dict)
    for run in runs:
        by_scenario_seed[(run["scenario"], run["seed_index"])][run["strategy"]] = run

    comparisons = {}
    scenarios = sorted({run["scenario"] for run in runs})
    strategies = sorted({run["strategy"] for run in runs if run["strategy"] != baseline})
    for scenario in scenarios:
        for strategy in strategies:
            pairs = []
            for (candidate_scenario, _), records in by_scenario_seed.items():
                if candidate_scenario == scenario and baseline in records and strategy in records:
                    pairs.append((records[baseline], records[strategy]))
            if not pairs:
                continue
            miss_diffs = [
                candidate["post_attack_miss_rate"] - base["post_attack_miss_rate"]
                for base, candidate in pairs
            ]
            exposure_diffs = [
                candidate["byzantine_leader_rounds"] - base["byzantine_leader_rounds"]
                for base, candidate in pairs
            ]
            key = f"{scenario}|{strategy}"
            comparisons[key] = {
                "scenario": scenario,
                "baseline": baseline,
                "strategy": strategy,
                "paired_seed_count": len(pairs),
                "mean_post_attack_miss_rate_difference": statistics.fmean(miss_diffs),
                "post_attack_miss_rate_difference_ci95": mean_ci95(miss_diffs),
                "mean_byzantine_leader_round_difference": statistics.fmean(exposure_diffs),
                "byzantine_leader_round_difference_ci95": mean_ci95(exposure_diffs),
            }
    return comparisons


def representative_traces(runs, output_path):
    """Recompute a small, deterministic set of per-round traces from recorded seeds."""
    wanted = {
        "T1",
        "P_T1_h095_b050_e02",
        "P_T3_h098_b080_e05",
    }
    selected = {}
    for run in runs:
        key = (run["scenario"], run["strategy"])
        if run["scenario"] in wanted and run["seed_index"] == 0 and key not in selected:
            selected[key] = run

    with output_path.open("w", encoding="utf-8") as handle:
        for (scenario, strategy), source in sorted(selected.items()):
            if scenario.startswith("P_"):
                behavior = generate_behavior_probabilistic(
                    source["attack_start_round"], source["total_rounds"],
                    source["honest_p"], source["byz_exploit_p"], source["obs_error_p"],
                    random.Random(source["seed"]),
                )
            else:
                behavior = generate_behavior_deterministic(scenario, source["total_rounds"])
            metrics = run_one(
                behavior, strategy, source["nodes"], source["groups"],
                source["total_rounds"], source["attack_start_round"], {0, 1},
                collect_trace=True,
            )
            for row in metrics["trace"]:
                handle.write(json.dumps({
                    "scenario": scenario,
                    "strategy": strategy,
                    "seed_index": source["seed_index"],
                    "seed": source["seed"],
                    **row,
                }, separators=(",", ":")) + "\n")
    return len(selected)


def write_checksums(result_dir, names):
    lines = []
    for name in names:
        path = result_dir / name
        if not path.exists():
            raise RuntimeError(f"required E11 artifact is missing: {name}")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}")
    (result_dir / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="ascii")


def aggregate(result_dir):
    result_dir = pathlib.Path(result_dir)
    runs_path = result_dir / "results.jsonl"
    summary_path = result_dir / "summary.json"
    if not runs_path.exists() or not summary_path.exists():
        raise SystemExit(f"E11 raw results are incomplete in {result_dir}")
    runs = [json.loads(line) for line in runs_path.read_text("utf-8").splitlines() if line.strip()]
    summary = json.loads(summary_path.read_text("utf-8"))
    if len(runs) != summary.get("total_runs"):
        raise RuntimeError(f"run count mismatch: raw={len(runs)} summary={summary.get('total_runs')}")

    grouped = defaultdict(list)
    for run in runs:
        grouped[(run["scenario"], run["strategy"])].append(run)
    aggregated = {f"{s}|{p}": aggregate_group(group) for (s, p), group in sorted(grouped.items())}
    deterministic = {k: v for k, v in aggregated.items() if not v["scenario"].startswith("P_")}
    probabilistic = {k: v for k, v in aggregated.items() if v["scenario"].startswith("P_")}
    paired = paired_comparisons([r for r in runs if r["scenario"].startswith("P_")])

    aggregate_doc = {
        "schema_version": "e11-aggregate-v3",
        "result_dir": str(result_dir.resolve()),
        "total_runs": len(runs),
        "deterministic_runs": sum(1 for r in runs if not r["scenario"].startswith("P_")),
        "probabilistic_runs": sum(1 for r in runs if r["scenario"].startswith("P_")),
        "deterministic": deterministic,
        "probabilistic": probabilistic,
    }
    (result_dir / "aggregate.json").write_text(json.dumps(aggregate_doc, indent=2), encoding="utf-8")
    (result_dir / "paired_comparisons.json").write_text(json.dumps(paired, indent=2), encoding="utf-8")

    csv_fields = [
        "scenario", "strategy", "count", "honest_p", "byz_exploit_p", "obs_error_p",
        "cold_start_exclusion_rate", "post_exposure_miss_rate",
        "mean_post_exposure_first_exclusion_delay", "p50_post_exposure_first_exclusion_delay",
        "p95_post_exposure_first_exclusion_delay", "mean_stable_h3_delay",
        "stable_h3_miss_rate", "mean_stable_h5_delay", "stable_h5_miss_rate",
        "mean_post_attack_miss_rate", "mean_re_entry_count", "mean_honest_demotion_rate",
        "mean_byzantine_leader_rounds",
    ]
    with (result_dir / "aggregate_probabilistic.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(probabilistic.values())

    trace_count = representative_traces(runs, result_dir / "representative_traces.jsonl")
    config = {
        "schema_version": "e11-package-v3",
        "source_summary": summary,
        "baseline_strategy": "cumulative",
        "bootstrap_samples": 2000,
        "representative_trace_config_count": trace_count,
    }
    (result_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    sources = [pathlib.Path(__file__), pathlib.Path(__file__).with_name("run_e11_temporal_reputation.py")]
    environment = {
        "python": sys.version,
        "platform": platform.platform(),
        "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
    }
    (result_dir / "environment.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")
    log = (
        f"Validated {len(runs)} raw runs against summary.json.\n"
        f"Aggregated {len(deterministic)} deterministic and {len(probabilistic)} probabilistic configurations.\n"
        f"Generated {len(paired)} paired comparisons and {trace_count} representative trace configurations.\n"
    )
    (result_dir / "aggregate.log").write_text(log, encoding="utf-8")
    names = [
        "results.jsonl", "summary.json", "aggregate.json", "paired_comparisons.json",
        "aggregate_probabilistic.csv", "representative_traces.jsonl", "config.json",
        "environment.json", "aggregate.log",
    ]
    write_checksums(result_dir, names)
    print(log, end="")
    return aggregate_doc


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=pathlib.Path)
    aggregate(parser.parse_args().result_dir)
