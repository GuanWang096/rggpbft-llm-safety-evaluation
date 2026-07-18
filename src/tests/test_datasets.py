from pathlib import Path

import pytest

from e1_pipeline.datasets import load_mm_safetybench_tiny, load_vlguard


DATASET_ROOT = Path(__file__).resolve().parents[2] / "dataset"

pytestmark = pytest.mark.skipif(
    not DATASET_ROOT.exists(),
    reason="public benchmark distributions are downloaded separately",
)


def test_mm_safetybench_tiny_inventory_is_complete():
    samples = load_mm_safetybench_tiny(DATASET_ROOT / "MM-SafetyBench")

    assert len(samples) == 504
    assert len({sample.sample_id for sample in samples}) == 504
    assert {sample.variant for sample in samples} == {"SD", "SD_TYPO", "TYPO"}
    assert all(sample.image_path.is_file() for sample in samples)
    assert all(sample.expected_input_safe is False for sample in samples)


def test_vlguard_expands_every_instruction_response_pair():
    samples = load_vlguard(DATASET_ROOT / "VLGuard")

    assert len(samples) == 1558
    assert len({sample.sample_id for sample in samples}) == 1558
    assert all(sample.image_path.is_file() for sample in samples)
    assert {sample.variant for sample in samples} == {
        "safe_instruction",
        "unsafe_instruction",
        "unsafe_image",
    }
    assert sum(sample.expected_input_safe for sample in samples) == 558
