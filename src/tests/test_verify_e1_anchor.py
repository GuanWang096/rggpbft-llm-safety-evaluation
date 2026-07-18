import hashlib
import json
import pathlib
import sys
import tarfile
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from verify_e1_anchor import verify_anchor, verify_bundle


class VerifyE1AnchorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.payload = self.root / "generation.jsonl"
        self.payload.write_text('{"sample_id":"a"}\n', encoding="utf-8")
        manifest = {
            "files": {
                self.payload.name: {
                    "bytes": self.payload.stat().st_size,
                    "sha256": hashlib.sha256(self.payload.read_bytes()).hexdigest(),
                }
            }
        }
        self.manifest = self.root / "evidence_manifest.json"
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        self.bundle = self.root / "bundle.tar.gz"
        with tarfile.open(self.bundle, "w:gz") as archive:
            archive.add(self.payload, arcname=self.payload.name)
            archive.add(self.manifest, arcname=self.manifest.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_bundle_verification_detects_modified_record(self):
        verify_bundle(self.bundle)
        with tarfile.open(self.bundle, "w:gz") as archive:
            self.payload.write_text('{"sample_id":"b"}\n', encoding="utf-8")
            archive.add(self.payload, arcname=self.payload.name)
            archive.add(self.manifest, arcname=self.manifest.name)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_bundle(self.bundle)

    def test_anchor_verification_detects_replaced_cid_or_hash(self):
        bundle_sha = hashlib.sha256(self.bundle.read_bytes()).hexdigest()
        task = {"cid": "cid-a", "sha256": bundle_sha}
        lifecycle = {"ipfs_cid": "cid-a", "bundle_sha256": bundle_sha}
        verify_anchor(task, lifecycle, self.bundle)
        with self.assertRaisesRegex(ValueError, "CID"):
            verify_anchor({**task, "cid": "cid-b"}, lifecycle, self.bundle)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            verify_anchor(task, {**lifecycle, "bundle_sha256": "0" * 64}, self.bundle)


if __name__ == "__main__":
    unittest.main()
