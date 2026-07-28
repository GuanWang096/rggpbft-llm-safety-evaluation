from pathlib import Path

from multijudge.qwen3_vl import (
    build_native_messages,
    build_panel_messages,
    semantic_message_sha256,
)
from multijudge.panels import ImagePanel
from multijudge.schema import CanonicalSample, DialogueTurn


def _sample(tmp_path: Path) -> CanonicalSample:
    tmp_path.mkdir(parents=True, exist_ok=True)
    first = tmp_path / "a.png"
    second = tmp_path / "b.png"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
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


def test_native_messages_interleave_two_images(tmp_path: Path) -> None:
    messages = build_native_messages(_sample(tmp_path), "POLICY")
    content = messages[0]["content"]
    image_items = [item for item in content if item["type"] == "image"]
    text = "\n".join(item["text"] for item in content if item["type"] == "text")
    assert len(image_items) == 2
    assert all(item["max_pixels"] > item["min_pixels"] for item in image_items)
    assert all(Path(item["image"]).is_absolute() for item in image_items)
    assert all(not item["image"].startswith("file://") for item in image_items)
    assert "[IMAGE_01]" in text
    assert "[IMAGE_02]" in text
    assert "unsafe" not in text.lower()


def test_panel_message_names_visible_indices(tmp_path: Path) -> None:
    panel_path = tmp_path / "panel.png"
    panel_path.write_bytes(b"panel")
    panel = ImagePanel(
        panel_index=1,
        image_indices=(1, 2),
        path=panel_path,
        sha256="hash",
    )
    messages = build_panel_messages(_sample(tmp_path), panel, "POLICY")
    content = messages[0]["content"]
    assert content[0]["type"] == "image"
    assert Path(content[0]["image"]).is_absolute()
    assert not content[0]["image"].startswith("file://")
    assert "IMAGE_01, IMAGE_02" in content[1]["text"]


def test_message_hash_does_not_depend_on_absolute_image_path(tmp_path: Path) -> None:
    first_sample = _sample(tmp_path / "first")
    second_sample = _sample(tmp_path / "second")
    first = build_native_messages(first_sample, "POLICY")
    second = build_native_messages(second_sample, "POLICY")
    assert first != second
    assert semantic_message_sha256(first) == semantic_message_sha256(second)
