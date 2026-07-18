#!/usr/bin/env python3
import argparse
import hashlib
import io
import json
import pathlib
import tarfile
import tempfile

from verify_e1_anchor import verify_anchor, verify_bundle


def read_bundle(path):
    members = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.isfile():
                members[member.name] = archive.extractfile(member).read()
    return members


def write_bundle(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for name in sorted(members):
            data = members[name]
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))


def expect_rejected(name, function):
    try:
        function()
    except ValueError as exc:
        return {"case": name, "detected": True, "reason": str(exc)}
    return {"case": name, "detected": False, "reason": "mutation was accepted"}


def run(bundle, task, lifecycle, output):
    verify_bundle(bundle)
    verify_anchor(task, lifecycle, bundle)
    original = read_bundle(bundle)
    results = []
    with tempfile.TemporaryDirectory() as temporary:
        temporary = pathlib.Path(temporary)

        modified = dict(original)
        generation = bytearray(modified["generation.jsonl"])
        generation[0] ^= 1
        modified["generation.jsonl"] = bytes(generation)
        path = temporary / "modified.tar.gz"
        write_bundle(path, modified)
        results.append(expect_rejected("modified_generation_record", lambda: verify_bundle(path)))

        deleted = dict(original)
        del deleted["moderation.jsonl"]
        path = temporary / "deleted.tar.gz"
        write_bundle(path, deleted)
        results.append(expect_rejected("deleted_moderation_records", lambda: verify_bundle(path)))

        reordered = dict(original)
        lines = reordered["generation.jsonl"].splitlines(keepends=True)
        lines[0], lines[1] = lines[1], lines[0]
        reordered["generation.jsonl"] = b"".join(lines)
        path = temporary / "reordered.tar.gz"
        write_bundle(path, reordered)
        results.append(expect_rejected("reordered_generation_records", lambda: verify_bundle(path)))

    results.append(
        expect_rejected(
            "replaced_cid",
            lambda: verify_anchor({**task, "cid": "QmReplacement"}, lifecycle, bundle),
        )
    )
    results.append(
        expect_rejected(
            "replaced_bundle_sha256",
            lambda: verify_anchor(
                task, {**lifecycle, "bundle_sha256": "0" * 64}, bundle
            ),
        )
    )
    report = {
        "schema": "zte-sci-e1-anchor-negative-v1",
        "bundle_sha256": hashlib.sha256(pathlib.Path(bundle).read_bytes()).hexdigest(),
        "cases": results,
        "passed": all(item["detected"] for item in results),
    }
    pathlib.Path(output).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not report["passed"]:
        raise SystemExit("one or more negative tests were not detected")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    run(
        args.run_dir / "e1_evidence_bundle.tar.gz",
        json.loads((args.run_dir / "task_org1.json").read_text(encoding="utf-8")),
        json.loads((args.run_dir / "lifecycle_result.json").read_text(encoding="utf-8")),
        args.output,
    )


if __name__ == "__main__":
    main()
