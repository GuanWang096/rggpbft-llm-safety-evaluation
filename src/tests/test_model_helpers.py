from e1_pipeline.datasets import EvaluationSample
from e1_pipeline.models import build_vlm_messages


def test_vlm_message_contains_one_image_and_the_dataset_prompt(tmp_path):
    image = tmp_path / "input.jpg"
    image.write_bytes(b"x")
    sample = EvaluationSample(
        sample_id="s1",
        dataset="fixture",
        image_path=image,
        prompt="Inspect the image.",
        expected_input_safe=True,
        risk_category="fixture",
        variant="fixture",
    )

    messages = build_vlm_messages(sample)

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": str(image.resolve())},
                {"type": "text", "text": "Inspect the image."},
            ],
        }
    ]

