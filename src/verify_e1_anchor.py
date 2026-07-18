#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import tarfile


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def verify_bundle(bundle_path):
    bundle_path = pathlib.Path(bundle_path)
    with tarfile.open(bundle_path, "r:gz") as archive:
        regular = {
            member.name: member
            for member in archive.getmembers()
            if member.isfile()
        }
        manifest_member = regular.get("evidence_manifest.json")
        if manifest_member is None:
            raise ValueError("evidence_manifest.json is missing")
        manifest = json.load(archive.extractfile(manifest_member))
        for name, expected in manifest.get("files", {}).items():
            member = regular.get(name)
            if member is None:
                raise ValueError(f"evidence file is missing: {name}")
            data = archive.extractfile(member).read()
            if len(data) != int(expected["bytes"]):
                raise ValueError(f"size mismatch: {name}")
            if sha256_bytes(data) != expected["sha256"]:
                raise ValueError(f"hash mismatch: {name}")
    return manifest


def verify_anchor(task, lifecycle, bundle_path):
    actual_sha = hashlib.sha256(pathlib.Path(bundle_path).read_bytes()).hexdigest()
    if task.get("cid") != lifecycle.get("ipfs_cid"):
        raise ValueError("CID mismatch between Fabric task and lifecycle result")
    if task.get("sha256") != lifecycle.get("bundle_sha256"):
        raise ValueError("SHA-256 mismatch between Fabric task and lifecycle result")
    if lifecycle.get("bundle_sha256") != actual_sha:
        raise ValueError("SHA-256 mismatch between lifecycle result and bundle")
    return {"cid": task["cid"], "sha256": actual_sha}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=pathlib.Path, required=True)
    parser.add_argument("--task", type=pathlib.Path, required=True)
    parser.add_argument("--lifecycle", type=pathlib.Path, required=True)
    args = parser.parse_args()
    verify_bundle(args.bundle)
    result = verify_anchor(
        json.loads(args.task.read_text(encoding="utf-8")),
        json.loads(args.lifecycle.read_text(encoding="utf-8")),
        args.bundle,
    )
    print(json.dumps({"state": "verified", **result}, sort_keys=True))


if __name__ == "__main__":
    main()
