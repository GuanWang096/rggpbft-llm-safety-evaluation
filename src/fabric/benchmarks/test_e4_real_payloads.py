import hashlib
import json

import pytest

from e4_full_lifecycle import load_manifest_payloads


def write_manifest(tmp_path, payloads):
    batches = []
    for index, payload in enumerate(payloads):
        filename = f"batch-{index:03d}.json"
        path = tmp_path / filename
        path.write_bytes(payload)
        batches.append(
            {
                "batch_index": index,
                "filename": filename,
                "record_count": index + 1,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest = {"record_count": sum(x["record_count"] for x in batches), "batches": batches}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_load_manifest_payloads_preserves_batch_order_and_counts(tmp_path):
    manifest = write_manifest(tmp_path, [b"first", b"second"])

    payloads = load_manifest_payloads(manifest)

    assert [item["data"] for item in payloads] == [b"first", b"second"]
    assert [item["record_count"] for item in payloads] == [1, 2]
    assert [item["batch_index"] for item in payloads] == [0, 1]


def test_load_manifest_payloads_rejects_hash_mismatch(tmp_path):
    manifest = write_manifest(tmp_path, [b"original"])
    (tmp_path / "batch-000.json").write_bytes(b"changed!")

    with pytest.raises(ValueError, match="SHA-256"):
        load_manifest_payloads(manifest)


def test_load_manifest_payloads_rejects_record_total_mismatch(tmp_path):
    manifest = write_manifest(tmp_path, [b"payload"])
    value = json.loads(manifest.read_text())
    value["record_count"] = 999
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="record count"):
        load_manifest_payloads(manifest)
