import json
import pytest

from e1_pipeline.artifacts import (
    JsonlCheckpoint,
    canonical_json_bytes,
    model_artifact_hashes,
    sha256_hex,
    write_checksum_manifest,
)


def test_checkpoint_resumes_without_duplicate_sample_ids(tmp_path):
    path = tmp_path / "generation.jsonl"
    checkpoint = JsonlCheckpoint(path)
    checkpoint.append({"sample_id": "s1", "response": "first"})

    resumed = JsonlCheckpoint(path)
    assert resumed.completed_ids == {"s1"}
    assert resumed.append({"sample_id": "s1", "response": "duplicate"}) is False
    assert resumed.append({"sample_id": "s2", "response": "second"}) is True

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["sample_id"] for record in records] == ["s1", "s2"]


def test_canonical_evidence_hash_is_key_order_independent():
    left = {"sample_id": "s1", "decision": {"safety": "Safe", "refusal": "No"}}
    right = {"decision": {"refusal": "No", "safety": "Safe"}, "sample_id": "s1"}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_hex(canonical_json_bytes(left)) == sha256_hex(canonical_json_bytes(right))


def test_model_artifact_hashes_supports_sharded_weights(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model-00001-of-00002.safetensors").write_bytes(b"first")
    (tmp_path / "model-00002-of-00002.safetensors").write_bytes(b"second")
    (tmp_path / "README.md").write_text("not part of runtime", encoding="utf-8")

    hashes = model_artifact_hashes(tmp_path)

    assert set(hashes) == {
        "config.json",
        "model-00001-of-00002.safetensors",
        "model-00002-of-00002.safetensors",
    }


def test_model_artifact_hashes_requires_weights(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="safetensors"):
        model_artifact_hashes(tmp_path)


def test_checkpoint_repairs_only_an_incomplete_final_line(tmp_path):
    path = tmp_path / "generation.jsonl"
    valid = canonical_json_bytes({"sample_id": "s1", "response": "ok"}) + b"\n"
    path.write_bytes(valid + b'{"sample_id":"s2"')

    checkpoint = JsonlCheckpoint(path)

    assert checkpoint.completed_ids == {"s1"}
    assert path.read_bytes() == valid


def test_checkpoint_rejects_a_corrupt_terminated_line(tmp_path):
    path = tmp_path / "generation.jsonl"
    path.write_bytes(b'{"sample_id":"s1"}\nnot-json\n')

    with pytest.raises(ValueError, match=":2"):
        JsonlCheckpoint(path)


def test_checksum_manifest_covers_existing_outputs_only(tmp_path):
    (tmp_path / "generation.jsonl").write_bytes(b"generation\n")
    (tmp_path / "summary.json").write_bytes(b"summary\n")

    write_checksum_manifest(
        tmp_path,
        ["summary.json", "missing.json", "generation.jsonl"],
    )

    lines = (tmp_path / "checksums.sha256").read_text(encoding="ascii").splitlines()
    assert [line.split("  ", 1)[1] for line in lines] == [
        "generation.jsonl",
        "summary.json",
    ]
