#!/usr/bin/env python3
import argparse
import json
import pathlib
import shutil
from datetime import datetime, timezone

import matplotlib.pyplot as plt


COLORS = {"pbft": "#6F95B5", "rgg": "#C78C68"}
GRID = "#DDE2E4"
LABELS = {"pbft": "PBFT", "rgg": "RGG-PBFT"}


def metric(summary, protocol, nodes, name):
    key = f"{protocol}:m{nodes}:d5:none"
    return summary["groups"][key]["metrics"][name]


def interval(metric_record):
    mean = metric_record["mean"]
    ci = metric_record["bootstrap_95"]
    return mean, mean - ci["low"], ci["high"] - mean


def generate(summary_path, output_dir):
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    nodes = (16, 20, 24)
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "font.size": 9,
            "axes.labelsize": 9,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "axes.linewidth": 0.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.65))
    specs = (
        ("client_latency_p50_ms", "Client median latency (ms)"),
        ("final_protocol_messages_sent", "Protocol messages per 20 rounds"),
    )
    for panel_index, (ax, (metric_name, ylabel)) in enumerate(zip(axes, specs)):
        for protocol in ("pbft", "rgg"):
            records = [metric(summary, protocol, node_count, metric_name) for node_count in nodes]
            parsed = [interval(record) for record in records]
            means = [item[0] for item in parsed]
            errors = ([item[1] for item in parsed], [item[2] for item in parsed])
            ax.errorbar(
                nodes,
                means,
                yerr=errors,
                color=COLORS[protocol],
                marker="o" if protocol == "pbft" else "s",
                markersize=4.5,
                linewidth=1.5,
                capsize=2.5,
                label=LABELS[protocol],
            )
        ax.set_xlabel("Consensus nodes, M")
        ax.set_ylabel(ylabel)
        ax.set_xticks(nodes)
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.text(
            -0.10, 1.04, chr(ord("a") + panel_index),
            transform=ax.transAxes, fontsize=10, fontweight="bold",
            va="top", ha="left",
        )
    axes[0].legend(frameon=False, loc="upper left")
    fig.tight_layout(w_pad=2.0)

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for suffix, dpi in (("pdf", 300), ("png", 300)):
        versioned = output_dir / f"fig_e2_measured_consensus_{stamp}.{suffix}"
        fixed = output_dir / f"fig_e2_measured_consensus.{suffix}"
        fig.savefig(versioned, dpi=dpi, bbox_inches="tight")
        shutil.copy2(versioned, fixed)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    generate(args.summary.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
