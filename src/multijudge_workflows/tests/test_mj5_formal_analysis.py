from __future__ import annotations

import json
import sys
from pathlib import Path


EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
if str(EXPERIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_DIR))

from analyze_mj5_formal import mean_sd, parse_size_mib, resource_peaks


def test_parse_docker_memory_units() -> None:
    assert parse_size_mib("4.5MiB") == 4.5
    assert parse_size_mib("1GiB") == 1024.0


def test_mean_sd_uses_sample_standard_deviation() -> None:
    summary = mean_sd([1.0, 2.0, 3.0])
    assert summary["mean"] == 2.0
    assert summary["sd"] == 1.0


def test_resource_peaks_excludes_deprecated_chaincode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "resources.json"
    path.write_text(
        json.dumps(
            [
                {
                    "Name": "peer0.org1.example.com",
                    "sampled_at_unix": 1.1,
                    "CPUPerc": "10.0%",
                    "MemUsage": "100MiB / 2GiB",
                },
                {
                    "Name": "dev-peer0.org1.example.com-tce_2.1-old",
                    "sampled_at_unix": 1.1,
                    "CPUPerc": "90.0%",
                    "MemUsage": "900MiB / 2GiB",
                },
            ]
        ),
        encoding="utf-8",
    )
    peaks = resource_peaks(path)
    assert peaks["peak_cpu_percent_sum"] == 10.0
    assert peaks["peak_memory_mib_sum"] == 100.0
