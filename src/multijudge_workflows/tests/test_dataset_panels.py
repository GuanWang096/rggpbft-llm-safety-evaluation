import hashlib
import json
from pathlib import Path

from PIL import Image

from multijudge.dataset import dialogue_transcript, load_mmds_manifest
from multijudge.panels import build_image_panels
from multijudge.schema import canonical_json, sha256_text


def _write_fixture(tmp_path: Path, image_count: int = 5) -> tuple[Path, Path]:
    dataset_root = tmp_path / "datasets"
    mmds_root = dataset_root / "MMDS"
    image_root = mmds_root / "images"
    image_root.mkdir(parents=True)
    references = []
    for index in range(image_count):
        path = image_root / f"{index}.png"
        Image.new("RGB", (80 + index, 60 + index), (20 * index, 80, 120)).save(
            path
        )
        references.append(f"images/{index}.png")

    conversations = [
        {
            "role": "user",
            "image": references[:2],
            "content": "User content",
        },
        {
            "role": "assistant",
            "image": [],
            "content": "Assistant content",
        },
        {
            "role": "user",
            "image": references[2:],
            "content": "Follow-up",
        },
    ]
    record = {
        "id": 7,
        "set": "train",
        "conversations": conversations,
        "assistant_rating": "Unsafe",
        "assistant_dimension": "Illegal Activities",
        "assistant_rationale": "MUST NOT ENTER MODEL INPUT",
    }
    metadata_path = mmds_root / "mmds.jsonl"
    metadata_path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    digest = sha256_text(canonical_json(conversations))
    manifest = {
        "entries": [
            {
                "sample_id": "mmds:train:1:7",
                "jsonl_line": 1,
                "assistant_label": "unsafe",
                "conversation_sha256": digest,
            }
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return dataset_root, manifest_path


def test_manifest_loader_preserves_image_order(tmp_path: Path) -> None:
    dataset_root, manifest_path = _write_fixture(tmp_path)
    sample = load_mmds_manifest(dataset_root, manifest_path)[0]
    assert sample.sample_id == "mmds:train:1:7"
    assert sample.expected_label == "unsafe"
    assert sample.image_references == tuple(f"images/{i}.png" for i in range(5))


def test_transcript_contains_markers_but_not_ground_truth(tmp_path: Path) -> None:
    dataset_root, manifest_path = _write_fixture(tmp_path)
    sample = load_mmds_manifest(dataset_root, manifest_path)[0]
    transcript = dialogue_transcript(sample)
    assert "[IMAGE_01]" in transcript
    assert "[IMAGE_05]" in transcript
    assert "MUST NOT ENTER MODEL INPUT" not in transcript
    assert "unsafe" not in transcript.lower()


def test_panel_builder_splits_five_images_into_two_panels(tmp_path: Path) -> None:
    dataset_root, manifest_path = _write_fixture(tmp_path)
    sample = load_mmds_manifest(dataset_root, manifest_path)[0]
    panels = build_image_panels(sample, tmp_path / "panels", tile_size=320)
    assert [panel.image_indices for panel in panels] == [(1, 2, 3, 4), (5,)]
    assert all(panel.path.is_file() for panel in panels)
    with Image.open(panels[0].path) as panel:
        assert panel.size == (640, 640)


def test_panel_generation_is_deterministic(tmp_path: Path) -> None:
    dataset_root, manifest_path = _write_fixture(tmp_path)
    sample = load_mmds_manifest(dataset_root, manifest_path)[0]
    first = build_image_panels(sample, tmp_path / "first", tile_size=320)
    second = build_image_panels(sample, tmp_path / "second", tile_size=320)
    assert [panel.sha256 for panel in first] == [panel.sha256 for panel in second]


def test_manifest_loader_rejects_hash_mismatch(tmp_path: Path) -> None:
    dataset_root, manifest_path = _write_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"][0]["conversation_sha256"] = hashlib.sha256(b"wrong").hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    try:
        load_mmds_manifest(dataset_root, manifest_path)
    except ValueError as exc:
        assert "Conversation hash mismatch" in str(exc)
    else:
        raise AssertionError("Hash mismatch was not rejected")
