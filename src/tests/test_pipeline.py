import json

from e1_pipeline.artifacts import JsonlCheckpoint
from e1_pipeline.datasets import EvaluationSample
from e1_pipeline.guard import GuardDecision
from e1_pipeline.models import GenerationResult, ModerationResult
from e1_pipeline.pipeline import run_generation, run_moderation


class FakeVLM:
    model_id = "fake-vlm"

    def __init__(self):
        self.calls = []

    def generate(self, sample):
        self.calls.append(sample.sample_id)
        return GenerationResult("answer", 12.5, 10, 3, 1024)


class FakeGuard:
    model_id = "fake-guard"

    def __init__(self):
        self.calls = []

    def moderate(self, prompt, response):
        self.calls.append((prompt, response))
        return ModerationResult(
            GuardDecision("Safe", ("None",), "No"),
            "Safety: Safe\nCategories: None\nRefusal: No",
            4.0,
            20,
            8,
            512,
        )


def _samples(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"image-bytes")
    return [
        EvaluationSample(
            sample_id=f"s{index}",
            dataset="fixture",
            image_path=image,
            prompt=f"prompt {index}",
            expected_input_safe=index == 0,
            risk_category="fixture",
            variant="fixture",
        )
        for index in range(2)
    ]


def test_generation_is_checkpointed_and_resumable(tmp_path):
    samples = _samples(tmp_path)
    checkpoint = JsonlCheckpoint(tmp_path / "generation.jsonl")
    checkpoint.append({"sample_id": "s0", "response": "existing"})
    runner = FakeVLM()

    counts = run_generation(samples, runner, checkpoint)

    assert counts == {"selected": 2, "skipped": 1, "written": 1}
    assert runner.calls == ["s1"]
    record = json.loads(checkpoint.path.read_text(encoding="utf-8").splitlines()[1])
    assert record["model_id"] == "fake-vlm"
    assert record["image_sha256"]
    assert record["latency_ms"] == 12.5


def test_moderation_reads_generation_and_preserves_ground_truth(tmp_path):
    samples = _samples(tmp_path)
    generation = tmp_path / "generation.jsonl"
    generation.write_text(
        "\n".join(
            json.dumps(
                {
                    "sample_id": sample.sample_id,
                    "prompt": sample.prompt,
                    "response": "answer",
                    "expected_input_safe": sample.expected_input_safe,
                    "dataset": sample.dataset,
                    "variant": sample.variant,
                    "risk_category": sample.risk_category,
                }
            )
            for sample in samples
        )
        + "\n",
        encoding="utf-8",
    )
    checkpoint = JsonlCheckpoint(tmp_path / "moderation.jsonl")
    guard = FakeGuard()

    counts = run_moderation(generation, guard, checkpoint)

    assert counts == {"selected": 2, "skipped": 0, "written": 2}
    rows = [json.loads(line) for line in checkpoint.path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["expected_input_safe"] is True
    assert rows[0]["safety"] == "Safe"
    assert rows[0]["guard_model_id"] == "fake-guard"
    assert len(guard.calls) == 2

