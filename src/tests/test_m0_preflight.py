import hashlib

import pytest

from verify_environment import verify_checksum_manifest


def test_verify_checksum_manifest_accepts_valid_files(tmp_path):
    payload = tmp_path / "payload.txt"
    payload.write_text("payload", encoding="utf-8")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = tmp_path / "checksums.sha256"
    manifest.write_text(f"{digest}  payload.txt\n", encoding="ascii")

    result = verify_checksum_manifest(tmp_path, manifest)

    assert result == {"payload.txt": "ok"}


def test_verify_checksum_manifest_rejects_a_mismatch(tmp_path):
    payload = tmp_path / "payload.txt"
    payload.write_text("changed", encoding="utf-8")
    manifest = tmp_path / "checksums.sha256"
    manifest.write_text(f"{'0' * 64}  payload.txt\n", encoding="ascii")

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_checksum_manifest(tmp_path, manifest)
