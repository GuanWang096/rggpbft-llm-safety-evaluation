from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def mean_sd(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.fmean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def parse_size_mib(value: str) -> float:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([A-Za-z]+)\s*", value)
    if match is None:
        raise ValueError(f"Unsupported Docker size: {value}")
    number, unit = match.groups()
    scale = {
        "B": 1 / (1024 * 1024),
        "kB": 1000 / (1024 * 1024),
        "KB": 1000 / (1024 * 1024),
        "KiB": 1 / 1024,
        "MB": 1_000_000 / (1024 * 1024),
        "MiB": 1.0,
        "GB": 1_000_000_000 / (1024 * 1024),
        "GiB": 1024.0,
    }
    return float(number) * scale[unit]


def resource_peaks(path: Path) -> dict[str, float]:
    samples = read_json(path)
    by_second: dict[int, dict[str, float]] = defaultdict(
        lambda: {"cpu": 0.0, "memory_mib": 0.0}
    )
    for row in samples:
        if "-tce_2.1-" in row["Name"]:
            continue
        second = round(float(row["sampled_at_unix"]))
        by_second[second]["cpu"] += float(row["CPUPerc"].rstrip("%"))
        used = row["MemUsage"].split("/", 1)[0].strip()
        by_second[second]["memory_mib"] += parse_size_mib(used)
    if not by_second:
        return {"peak_cpu_percent_sum": 0.0, "peak_memory_mib_sum": 0.0}
    return {
        "peak_cpu_percent_sum": max(row["cpu"] for row in by_second.values()),
        "peak_memory_mib_sum": max(
            row["memory_mib"] for row in by_second.values()
        ),
    }


def run_metrics(session_dir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    run_dir = session_dir / "runs" / entry["run_id"]
    stage_a_status = entry["fabric_stage_a"]["status"]
    stage_c_status = entry["fabric_stage_c"]["status"]
    rgg_status = read_json(run_dir / "rgg_status.json")
    stage_a_seconds = float(stage_a_status["elapsed_seconds_this_invocation"])
    stage_c_seconds = float(stage_c_status["elapsed_seconds_this_invocation"])
    rgg_seconds = float(rgg_status["elapsed_seconds"])
    records = int(entry["sample_count"])
    sequential_stage_seconds = (
        stage_a_seconds + rgg_seconds + stage_c_seconds
    )
    stage_a_rows = read_jsonl(run_dir / "stage_a.jsonl")
    stage_c_by_decision = {
        row["decision_id"]: row
        for row in read_jsonl(run_dir / "stage_c.jsonl")
    }
    rgg_by_decision = {
        event["data"]["decision_id"]: float(event["data"]["latency_ms"])
        for event in read_jsonl(run_dir / "rgg/events.jsonl")
        if event["type"] == "DRIVER_RESULT"
        and event["data"]["success"] is True
    }
    if (
        len(stage_a_rows) != records
        or len(stage_c_by_decision) != records
        or len(rgg_by_decision) != records
    ):
        raise RuntimeError(f"{entry['run_id']} decision-level join failed")
    composed = [
        float(row["stage_a_total_ms"])
        + float(stage_c_by_decision[row["decision_id"]]["stage_c_total_ms"])
        + rgg_by_decision[row["decision_id"]]
        for row in stage_a_rows
    ]
    return {
        "run_id": entry["run_id"],
        "judge_count": int(entry["judge_count"]),
        "concurrency": int(entry["concurrency"]),
        "repeat": int(entry["repeat"]),
        "sample_count": records,
        "stage_a_throughput": float(
            stage_a_status["workflow_throughput_per_second"]
        ),
        "stage_c_throughput": float(
            stage_c_status["workflow_throughput_per_second"]
        ),
        "sequential_stage_throughput": records / sequential_stage_seconds,
        "stage_a_p95_ms": float(
            entry["fabric_stage_a"]["workflow_total"]["p95_ms"]
        ),
        "stage_c_p95_ms": float(
            entry["fabric_stage_c"]["workflow_total"]["p95_ms"]
        ),
        "composed_workflow_p95_ms": percentile(composed, 0.95),
        "settlement_queue_p95_ms": float(
            entry["fabric_stage_c"]["settlement_queue_wait"]["p95_ms"]
        ),
        "settlement_mvcc_retries": int(
            entry["fabric_stage_c"]["settlement_mvcc_retries"]["total"]
        ),
        "rgg_client_p95_ms": float(entry["rgg"]["client_latency_ms"]["p95"]),
        "rgg_driver_failures": int(entry["rgg"]["driver_failure_count"]),
        "rgg_commit_conflicts": int(entry["rgg"]["conflicting_commit_count"]),
        "rgg_safety_violations": int(entry["rgg"]["safety_violation_events"]),
        "evidence_bundle_mean_bytes": float(
            entry["evidence_bundle_bytes"]["mean_ms"]
        ),
        "signed_log_throughput": float(
            entry["signed_log_baseline"]["throughput_per_second"]
        ),
        "stage_a_seconds": stage_a_seconds,
        "rgg_seconds": rgg_seconds,
        "stage_c_seconds": stage_c_seconds,
        "stage_a_resources": resource_peaks(
            run_dir / "stage_a_resources.json"
        ),
        "stage_c_resources": resource_peaks(
            run_dir / "stage_c_resources.json"
        ),
    }


def aggregate_configs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["judge_count"], row["concurrency"])].append(row)
    metrics = (
        "stage_a_throughput",
        "stage_c_throughput",
        "sequential_stage_throughput",
        "stage_a_p95_ms",
        "stage_c_p95_ms",
        "composed_workflow_p95_ms",
        "settlement_queue_p95_ms",
        "rgg_client_p95_ms",
        "evidence_bundle_mean_bytes",
        "signed_log_throughput",
    )
    output = []
    for (judge_count, concurrency), group in sorted(groups.items()):
        item: dict[str, Any] = {
            "judge_count": judge_count,
            "concurrency": concurrency,
            "repeat_count": len(group),
        }
        for metric in metrics:
            item[metric] = mean_sd(
                [float(row[metric]) for row in group]
            )
        for stage in ("stage_a", "stage_c"):
            item[f"{stage}_peak_cpu_percent_sum"] = mean_sd(
                [
                    row[f"{stage}_resources"]["peak_cpu_percent_sum"]
                    for row in group
                ]
            )
            item[f"{stage}_peak_memory_mib_sum"] = mean_sd(
                [
                    row[f"{stage}_resources"]["peak_memory_mib_sum"]
                    for row in group
                ]
            )
        item["settlement_mvcc_retries"] = sum(
            row["settlement_mvcc_retries"] for row in group
        )
        output.append(item)

    baselines = {
        row["judge_count"]: row
        for row in output
        if row["concurrency"] == 1
    }
    for row in output:
        baseline = baselines[row["judge_count"]]
        row["stage_a_speedup_vs_c1"] = (
            row["stage_a_throughput"]["mean"]
            / baseline["stage_a_throughput"]["mean"]
        )
        row["stage_c_speedup_vs_c1"] = (
            row["stage_c_throughput"]["mean"]
            / baseline["stage_c_throughput"]["mean"]
        )
    return output


def write_csv(path: Path, configs: list[dict[str, Any]]) -> None:
    fields = [
        "judge_count",
        "concurrency",
        "repeat_count",
        "stage_a_throughput_mean",
        "stage_a_throughput_sd",
        "stage_a_speedup_vs_c1",
        "stage_c_throughput_mean",
        "stage_c_throughput_sd",
        "stage_c_speedup_vs_c1",
        "sequential_stage_throughput_mean",
        "composed_workflow_p95_ms_mean",
        "settlement_queue_p95_ms_mean",
        "rgg_client_p95_ms_mean",
        "stage_a_peak_memory_mib_sum_mean",
        "stage_c_peak_memory_mib_sum_mean",
        "settlement_mvcc_retries",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in configs:
            writer.writerow(
                {
                    "judge_count": row["judge_count"],
                    "concurrency": row["concurrency"],
                    "repeat_count": row["repeat_count"],
                    "stage_a_throughput_mean": row[
                        "stage_a_throughput"
                    ]["mean"],
                    "stage_a_throughput_sd": row[
                        "stage_a_throughput"
                    ]["sd"],
                    "stage_a_speedup_vs_c1": row[
                        "stage_a_speedup_vs_c1"
                    ],
                    "stage_c_throughput_mean": row[
                        "stage_c_throughput"
                    ]["mean"],
                    "stage_c_throughput_sd": row[
                        "stage_c_throughput"
                    ]["sd"],
                    "stage_c_speedup_vs_c1": row[
                        "stage_c_speedup_vs_c1"
                    ],
                    "sequential_stage_throughput_mean": row[
                        "sequential_stage_throughput"
                    ]["mean"],
                    "composed_workflow_p95_ms_mean": row[
                        "composed_workflow_p95_ms"
                    ]["mean"],
                    "settlement_queue_p95_ms_mean": row[
                        "settlement_queue_p95_ms"
                    ]["mean"],
                    "rgg_client_p95_ms_mean": row[
                        "rgg_client_p95_ms"
                    ]["mean"],
                    "stage_a_peak_memory_mib_sum_mean": row[
                        "stage_a_peak_memory_mib_sum"
                    ]["mean"],
                    "stage_c_peak_memory_mib_sum_mean": row[
                        "stage_c_peak_memory_mib_sum"
                    ]["mean"],
                    "settlement_mvcc_retries": row[
                        "settlement_mvcc_retries"
                    ],
                }
            )


def fmt(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}"


def write_report(
    path: Path,
    session_id: str,
    runtime_hours: float,
    configs: list[dict[str, Any]],
    integrity: dict[str, Any],
) -> None:
    lines = [
        "# MJ5 正式实验统计报告",
        "",
        f"- 会话：`{session_id}`",
        f"- 总运行时间：{runtime_hours:.3f} h",
        "- 正式矩阵：J=3/4 × c=1/8/16 × 3 次同主机顺序重复，共 18 组",
        "- 每组：96 个按标签和风险维度分层选取的真实测试样本",
        f"- Stage A / Stage C / 证书记录：{integrity['row_count']} / "
        f"{integrity['row_count']} / {integrity['certificate_count']}",
        f"- RGG-PBFT 失败 / 冲突提交 / 安全违规："
        f"{integrity['rgg_driver_failures']} / "
        f"{integrity['rgg_commit_conflicts']} / "
        f"{integrity['rgg_safety_violations']}",
        f"- Fabric 最终 MVCC 重试：{integrity['mvcc_retries']}",
        "",
        "## 配置级结果",
        "",
        "| J | c | Stage A throughput (wf/s) | Stage C throughput (wf/s) "
        "| Stage A speedup | Stage C speedup | composed p95 (s) "
        "| settlement queue p95 (s) | RGG p95 (ms) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in configs:
        lines.append(
            f"| {row['judge_count']} | {row['concurrency']} | "
            f"{fmt(row['stage_a_throughput']['mean'])} ± "
            f"{fmt(row['stage_a_throughput']['sd'])} | "
            f"{fmt(row['stage_c_throughput']['mean'])} ± "
            f"{fmt(row['stage_c_throughput']['sd'])} | "
            f"{fmt(row['stage_a_speedup_vs_c1'], 2)}× | "
            f"{fmt(row['stage_c_speedup_vs_c1'], 2)}× | "
            f"{fmt(row['composed_workflow_p95_ms']['mean'] / 1000, 2)} | "
            f"{fmt(row['settlement_queue_p95_ms']['mean'] / 1000, 2)} | "
            f"{fmt(row['rgg_client_p95_ms']['mean'], 2)} |"
        )
    lines.extend(
        [
            "",
            "## 主要发现",
            "",
            "1. Fabric/IPFS 前半流程随并发度明显扩展；结算阶段受共享裁判信誉键的单写依赖约束，在 c=8 后进入平台区。",
            "2. c=16 的端到端尾延迟主要来自结算队列，而不是 RGG-PBFT。该队列时间已计入结果，没有通过删除冲突样本或隐藏重试来改善指标。",
            "3. 所有 1728 个真实决策摘要均获得 16 节点 RGG-PBFT 提交和可验证 leader certificate；未出现冲突提交或安全违规。",
            "4. J=3 与 J=4 的系统吞吐接近，说明本轮基础设施成本主要由链上交易数和共享状态更新决定，而不是证据包中多一个裁判输出。",
            "5. 签名哈希链基线仅代表单管理员日志的计算成本，信任模型不等价，不能解释为 Fabric 的可替代吞吐基线。",
            "",
            "## 解释边界",
            "",
            "- 本报告是单机 Docker Desktop/WSL2 的系统评测，不代表多主机部署性能。",
            "- 每个配置仅 3 次重复；均值与标准差用于描述本机重复性，不作广泛部署总体的推断统计。",
            "- `composed p95` 是三个阶段的按样本组合延迟，不是跨阶段并行流水线的墙钟响应时间。",
            "- 96 条系统样本用于基础设施容量回放；模型准确性结论仍来自完整的 330 条测试集。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    session_dir = args.session_dir.resolve()
    aggregate = read_json(session_dir / "aggregate.json")
    runtime = read_json(session_dir / "runtime_status.json")
    if not aggregate["all_entries_complete"] or aggregate["entry_count"] != 18:
        raise RuntimeError("Formal aggregate is incomplete")
    rows = [run_metrics(session_dir, entry) for entry in aggregate["entries"]]
    row_count = sum(row["sample_count"] for row in rows)
    certificate_count = sum(
        len(read_json(session_dir / "runs" / row["run_id"] / "protocol_certificates.json"))
        for row in rows
    )
    integrity = {
        "row_count": row_count,
        "certificate_count": certificate_count,
        "rgg_driver_failures": sum(row["rgg_driver_failures"] for row in rows),
        "rgg_commit_conflicts": sum(row["rgg_commit_conflicts"] for row in rows),
        "rgg_safety_violations": sum(
            row["rgg_safety_violations"] for row in rows
        ),
        "mvcc_retries": sum(row["settlement_mvcc_retries"] for row in rows),
    }
    if (
        row_count != 1728
        or certificate_count != 1728
        or any(integrity[key] != 0 for key in integrity if key not in {"row_count", "certificate_count"})
    ):
        raise RuntimeError(f"Formal integrity gate failed: {integrity}")
    configs = aggregate_configs(rows)
    output = {
        "schema": "mj5-formal-analysis-v1",
        "session_id": aggregate["session_id"],
        "runtime_hours": float(runtime["elapsed_seconds"]) / 3600,
        "integrity": integrity,
        "runs": rows,
        "configurations": configs,
    }
    write_json(session_dir / "formal_analysis.json", output)
    write_csv(session_dir / "formal_summary.csv", configs)
    write_report(
        session_dir / "FORMAL_RESULTS_REPORT.md",
        aggregate["session_id"],
        output["runtime_hours"],
        configs,
        integrity,
    )
    print(
        json.dumps(
            {
                "session_id": aggregate["session_id"],
                "configurations": len(configs),
                "integrity": integrity,
            },
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_dir", type=Path)
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
