import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from run_e10_capacity import build_resource_summary, build_e10_resource_matrix


def sample(cpu_a, cpu_b, mem_a, mem_b, host_available, swap_used=0):
    return {
        "time_ns": 1,
        "containers": [
            {"CPUPerc": f"{cpu_a}%", "MemUsage": f"{mem_a}MiB / 1GiB"},
            {"CPUPerc": f"{cpu_b}%", "MemUsage": f"{mem_b}MiB / 1GiB"},
        ],
        "host": {
            "memory_total_bytes": 1000,
            "memory_available_bytes": host_available,
            "swap_used_bytes": swap_used,
        },
    }


def test_resource_summary_aggregates_per_timestamp_not_per_container():
    samples = [
        sample(10, 20, 100, 200, 700),
        sample(30, 40, 150, 250, 600, 10),
        sample(20, 30, 120, 220, 650, 5),
    ]
    summary = build_resource_summary(samples, minimum_samples=3)
    assert summary["sample_count"] == 3
    assert summary["container_record_count"] == 6
    assert summary["peak_cpu_percent"] == 70
    assert summary["peak_memory_bytes"] == 400 * 1024 * 1024
    assert summary["host_peak_memory_used_bytes"] == 400
    assert summary["host_peak_swap_used_bytes"] == 10


def test_resource_summary_rejects_too_few_samples():
    try:
        build_resource_summary([sample(1, 1, 1, 1, 900)], minimum_samples=3)
    except RuntimeError as exc:
        assert "at least 3" in str(exc)
    else:
        raise AssertionError("zero/short resource evidence must fail the stop gate")


def test_resource_matrix_is_twelve_runs():
    matrix = build_e10_resource_matrix(repeats=3)
    assert len(matrix) == 12
    assert {entry["concurrency"] for entry in matrix} == {1, 8, 16, 32}
    assert {entry["repeat"] for entry in matrix} == {1, 2, 3}
    assert all(entry["series"] == "resource-only-v2" for entry in matrix)
