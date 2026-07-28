import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from PIL import Image

from multijudge.internvl import (
    build_internvl_question,
    configure_language_attention,
    dynamic_tiles,
    tiles_per_image,
)
from multijudge.minicpm import build_minicpm_content, slices_per_image
from multijudge.policy import CanonicalPolicy
from multijudge.runtime import fingerprint_model
from multijudge.safework import (
    build_safework_turn_messages,
    next_visual_token_budget,
    parse_safework_decision,
    set_processor_visual_token_budget,
    visual_tokens_per_image,
)
from multijudge.schema import CanonicalSample, DialogueTurn


REPEAT_SCRIPT = (
    Path(__file__).resolve().parents[1] / "compare_repeatability.py"
)
REPEAT_SPEC = spec_from_file_location("compare_repeatability", REPEAT_SCRIPT)
assert REPEAT_SPEC is not None and REPEAT_SPEC.loader is not None
REPEAT_MODULE = module_from_spec(REPEAT_SPEC)
REPEAT_SPEC.loader.exec_module(REPEAT_MODULE)


def _sample(tmp_path: Path) -> CanonicalSample:
    tmp_path.mkdir(parents=True, exist_ok=True)
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    Image.new("RGB", (640, 320), "#78909c").save(first)
    Image.new("RGB", (320, 640), "#c98f7a").save(second)
    return CanonicalSample(
        sample_id="mmds:train:1:1",
        dataset="MMDS",
        split="train",
        raw_id="1",
        jsonl_line=1,
        expected_label="unsafe",
        expected_dimension="Illegal Activities",
        turns=(
            DialogueTurn(
                role="user",
                content="Question",
                image_references=("images/a.png", "images/b.png"),
                image_paths=(first, second),
            ),
            DialogueTurn(
                role="assistant",
                content="Response",
                image_references=(),
                image_paths=(),
            ),
        ),
        conversation_sha256="digest",
    )


def _policy() -> CanonicalPolicy:
    return CanonicalPolicy(
        policy_id="test",
        version="1",
        task="binary",
        safe_definition="SAFE DEF",
        unsafe_definition="UNSAFE DEF",
        categories=("Illegal Activities",),
        output_schema={"type": "object"},
        sha256="policy",
    )


def test_safework_messages_use_native_absolute_images(tmp_path: Path) -> None:
    turn_messages = build_safework_turn_messages(_sample(tmp_path), _policy())
    assert len(turn_messages) == 1
    turn_index, messages = turn_messages[0]
    assert turn_index == 2
    images = [
        item
        for item in messages[0]["content"]
        if item["type"] == "image"
    ]
    assert len(images) == 2
    assert all(Path(item["image"]).is_absolute() for item in images)
    assert all(not item["image"].startswith("file://") for item in images)
    prompt = messages[0]["content"][-1]["text"]
    assert "SAFE DEF" in prompt
    assert "UNSAFE DEF" in prompt
    assert "Illegal Activities" in prompt
    assert "<text_query> [TURN_01][USER]: Question" in prompt
    assert "<response> [TURN_02][ASSISTANT]: Response" in prompt
    assert prompt.endswith("Illogical completion")


def test_safework_native_labels_have_frozen_binary_mapping() -> None:
    unsafe, native = parse_safework_decision("Unsafe")
    refusal, refusal_native = parse_safework_decision("Safe with refusal.")
    numbered, numbered_native = parse_safework_decision("(4)")
    invalid, invalid_native = parse_safework_decision("probably unsafe")
    assert (unsafe.label, native) == ("unsafe", "Unsafe")
    assert (refusal.label, refusal_native) == ("safe", "Safe with refusal")
    assert (numbered.label, numbered_native) == ("unsafe", "Unsafe")
    assert invalid.status == "error"
    assert invalid_native is None


def test_safework_uses_one_request_per_assistant_turn(tmp_path: Path) -> None:
    base = _sample(tmp_path)
    sample = CanonicalSample(
        sample_id=base.sample_id,
        dataset=base.dataset,
        split=base.split,
        raw_id=base.raw_id,
        jsonl_line=base.jsonl_line,
        expected_label=base.expected_label,
        expected_dimension=base.expected_dimension,
        turns=base.turns
        + (
            DialogueTurn(
                role="user",
                content="Follow-up question",
                image_references=(),
                image_paths=(),
            ),
            DialogueTurn(
                role="assistant",
                content="Follow-up response",
                image_references=(),
                image_paths=(),
            ),
        ),
        conversation_sha256=base.conversation_sha256,
    )
    requests = build_safework_turn_messages(sample, _policy())
    assert [turn_index for turn_index, _ in requests] == [2, 4]
    second_content = requests[1][1][0]["content"]
    second_prompt = second_content[-1]["text"]
    assert sum(item["type"] == "image" for item in second_content) == 2
    assert "Follow-up question" in second_prompt
    assert "Follow-up response" in second_prompt
    assert "\nResponse\n" not in second_prompt


def test_safework_visual_budget_respects_input_limit() -> None:
    assert visual_tokens_per_image(text_tokens=1000, image_count=3) == 1280
    constrained = visual_tokens_per_image(
        text_tokens=5000,
        image_count=7,
    )
    assert constrained == int((12800 - 256 - 5000) * 0.75) // 7
    assert 5000 + 7 * constrained + 256 <= 12800


def test_safework_processor_budget_and_retry_are_bounded() -> None:
    class ImageProcessor:
        min_pixels = 0
        max_pixels = 0

    class Processor:
        image_processor = ImageProcessor()

    processor = Processor()
    set_processor_visual_token_budget(processor, 640)
    assert processor.image_processor.min_pixels == 64 * 28 * 28
    assert processor.image_processor.max_pixels == 640 * 28 * 28
    reduced = next_visual_token_budget(
        current_budget=640,
        observed_tokens=15930,
    )
    assert 64 <= reduced < 640


def test_internvl_dynamic_tiles_are_bounded_and_deterministic() -> None:
    image = Image.new("RGB", (896, 448), "#78909c")
    first = dynamic_tiles(image)
    second = dynamic_tiles(image)
    assert len(first) == len(second) == 3
    assert all(tile.size == (448, 448) for tile in first)
    assert [tile.tobytes() for tile in first] == [
        tile.tobytes() for tile in second
    ]
    assert len(dynamic_tiles(image, max_tiles=2)) <= 2


def test_internvl_tile_budget_is_shared_across_images() -> None:
    assert tiles_per_image(1) == 4
    assert tiles_per_image(4) == 4
    assert tiles_per_image(8) == 2
    assert tiles_per_image(16) == 1


def test_internvl_attention_configuration_reaches_language_model() -> None:
    class Config:
        _attn_implementation = "eager"

    class OuterConfig:
        llm_config = Config()

    class LanguageModel:
        config = Config()

    class Model:
        config = OuterConfig()
        language_model = LanguageModel()

    model = Model()
    configure_language_attention(model, "sdpa")
    assert model.config.llm_config._attn_implementation == "sdpa"
    assert model.language_model.config._attn_implementation == "sdpa"


def test_internvl_question_has_one_marker_per_image(tmp_path: Path) -> None:
    question = build_internvl_question(_sample(tmp_path), "POLICY")
    assert question.count("<image>") == 2
    assert "IMAGE_01: <image>" in question
    assert "IMAGE_02: <image>" in question
    assert "[TURN_02][ASSISTANT]" in question


def test_minicpm_content_preserves_image_order_and_stable_hash(
    tmp_path: Path,
) -> None:
    first_content, first_hash = build_minicpm_content(
        _sample(tmp_path / "first"),
        "POLICY",
    )
    second_content, second_hash = build_minicpm_content(
        _sample(tmp_path / "second"),
        "POLICY",
    )
    assert sum(isinstance(item, Image.Image) for item in first_content) == 2
    assert first_hash == second_hash
    assert len(first_content) == len(second_content)


def test_minicpm_slice_budget_is_shared_across_images() -> None:
    assert slices_per_image(1) == 4
    assert slices_per_image(4) == 4
    assert slices_per_image(5) == 3
    assert slices_per_image(8) == 2
    assert slices_per_image(16) == 1


def test_model_fingerprint_changes_with_file_content(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    config = model / "config.json"
    config.write_text('{"a":1}', encoding="utf-8")
    first = fingerprint_model(model)
    config.write_text('{"a":2}', encoding="utf-8")
    second = fingerprint_model(model)
    assert first["manifest_sha256"] != second["manifest_sha256"]


def _repeat_record(raw_output: str) -> dict:
    return {
        "sample_id": "sample",
        "subdecisions": [
            {
                "raw_output": raw_output,
                "prompt_sha256": "prompt",
            }
        ],
        "decision": {
            "status": "ok",
            "label": "safe",
            "categories": [],
            "error": None,
        },
    }


def test_repeatability_comparator_detects_raw_output_change(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(
        json.dumps(_repeat_record('{"label":"safe"}')) + "\n",
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(_repeat_record('{"label": "safe"}')) + "\n",
        encoding="utf-8",
    )
    result = REPEAT_MODULE.compare(first, second)
    assert result["passed"] is False
    assert result["mismatch_count"] == 1
