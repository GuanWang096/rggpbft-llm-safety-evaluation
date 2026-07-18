from pathlib import Path

from e1_pipeline.datasets import EvaluationSample
from e1_pipeline.selection import stratified_select


def test_stratified_selection_is_deterministic_and_spans_groups():
    samples = [
        EvaluationSample(
            sample_id=f"{variant}-{index}",
            dataset="fixture",
            image_path=Path("image.jpg"),
            prompt="prompt",
            expected_input_safe=variant == "safe",
            risk_category=variant,
            variant=variant,
        )
        for variant in ("safe", "unsafe", "typo")
        for index in range(4)
    ]

    first = stratified_select(samples, limit=6, seed=7)
    second = stratified_select(samples, limit=6, seed=7)

    assert [sample.sample_id for sample in first] == [sample.sample_id for sample in second]
    assert {sample.variant for sample in first} == {"safe", "unsafe", "typo"}
    assert len(first) == 6


def test_selection_prioritizes_dataset_variant_coverage():
    samples = [
        EvaluationSample(
            sample_id=f"{dataset}-{variant}-{risk}",
            dataset=dataset,
            image_path=Path("image.jpg"),
            prompt="prompt",
            expected_input_safe=False,
            risk_category=risk,
            variant=variant,
        )
        for dataset, variants in (("mm", ("SD", "TYPO")), ("vl", ("safe", "unsafe")))
        for variant in variants
        for risk in ("r1", "r2", "r3")
    ]

    selected = stratified_select(samples, limit=4, seed=1)

    assert {(sample.dataset, sample.variant) for sample in selected} == {
        ("mm", "SD"),
        ("mm", "TYPO"),
        ("vl", "safe"),
        ("vl", "unsafe"),
    }
