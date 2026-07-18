#!/usr/bin/env python3
"""Aggregate B6 grouping ablation results."""
import argparse
import hashlib
import json
import pathlib
import statistics


def aggregate(results_dir):
    rd = pathlib.Path(results_dir)
    lines = [json.loads(line) for line in (rd / "results.jsonl").read_text().strip().splitlines()]

    by_scale = {}
    for r in lines:
        scale = r["nodes"]
        by_scale.setdefault(scale, []).append(r)

    sections = []
    for scale in sorted(by_scale):
        scale_runs = by_scale[scale]
        sections.append(f"## M={scale}")
        for strategy in ["fixed_modulo", "seeded_random", "reputation_round_robin"]:
            strat_runs = [r for r in scale_runs if r["strategy"] == strategy]
            if not strat_runs:
                continue
            for rank_cond in sorted(set(r["rank_condition"] for r in strat_runs)):
                rc_runs = [r for r in strat_runs if r["rank_condition"] == rank_cond]
                conc_vals = [r["max_group_concentration"] for r in rc_runs]
                over_vals = [r["groups_exceeding_local_threshold"] for r in rc_runs]
                byz_lead_vals = [r["byzantine_leader_count"] for r in rc_runs]
                sections.append(
                    f"### {strategy} / {rank_cond} (n={len(rc_runs)})\n"
                    f"- max_group_concentration: {statistics.mean(conc_vals):.2f} mean, "
                    f"{sorted(conc_vals)}\n"
                    f"- groups_exceeding_local_threshold: {statistics.mean(over_vals):.2f} mean, "
                    f"{sorted(over_vals)}\n"
                    f"- byzantine_leader_count: {statistics.mean(byz_lead_vals):.2f} mean, "
                    f"{sorted(byz_lead_vals)}\n"
                )

    report = "\n".join([
        "# B6 Grouping Ablation Aggregate",
        f"\nTotal runs: {len(lines)}",
        "\n".join(sections),
    ])

    out_path = rd / "aggregate.md"
    out_path.write_text(report + "\n")
    checksum_names = (
        "config.json",
        "environment.json",
        "results.jsonl",
        "summary.json",
        "aggregate.md",
    )
    with (rd / "checksums.sha256").open("w", encoding="ascii") as output:
        for name in checksum_names:
            digest = hashlib.sha256((rd / name).read_bytes()).hexdigest()
            output.write(f"{digest}  {name}\n")
    print(report)
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    aggregate(args.results_dir)
