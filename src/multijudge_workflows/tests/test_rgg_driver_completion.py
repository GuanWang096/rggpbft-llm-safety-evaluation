from __future__ import annotations

import sys
from pathlib import Path


RGG_SOURCE = Path(__file__).resolve().parents[3] / "src" / "rggpbft"
if str(RGG_SOURCE) not in sys.path:
    sys.path.insert(0, str(RGG_SOURCE))

from driver_v2 import completion_latency_fields


def test_driver_primary_latency_is_required_commit_completion() -> None:
    fields = completion_latency_fields(
        start_ns=1_000_000_000,
        first_commit_ns=1_010_000_000,
        required_commit_ns=1_075_000_000,
        ended_ns=1_080_000_000,
    )

    assert fields["first_commit_latency_ms"] == 10.0
    assert fields["required_commit_latency_ms"] == 75.0
    assert fields["latency_ms"] == 75.0


def test_driver_timeout_uses_end_time_for_missing_completion() -> None:
    fields = completion_latency_fields(
        start_ns=2_000_000_000,
        first_commit_ns=2_015_000_000,
        required_commit_ns=None,
        ended_ns=2_500_000_000,
    )

    assert fields["first_commit_latency_ms"] == 15.0
    assert fields["required_commit_latency_ms"] == 500.0
    assert fields["latency_ms"] == 500.0
