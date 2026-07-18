import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from analyze_e1_results import (
    DEFAULT_BASE,
    DEFAULT_FINAL,
    _validate_run,
    canonical_json_bytes,
    sha256_file,
)


RESULTS_ROOT = Path(__file__).resolve().parent / "results"


def verify_checksum_manifest(root, manifest):
    root = Path(root)
    result = {}
    for line_number, line in enumerate(
        Path(manifest).read_text(encoding="ascii").splitlines(), start=1
    ):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ValueError(f"invalid checksum line {line_number}: {line}")
        expected, filename = parts
        filename = filename.strip()
        path = root / filename
        if not path.is_file():
            raise ValueError(f"checksum file missing: {filename}")
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"checksum mismatch: {filename}")
        result[filename] = "ok"
    return result


def run_command(command):
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def inspect_e1(root):
    generation, moderation, config = _validate_run(Path(root))
    checks = verify_checksum_manifest(root, Path(root) / "checksums.sha256")
    return {
        "path": str(Path(root).resolve()),
        "generation_count": len(generation),
        "moderation_count": len(moderation),
        "sample_count": len(config["sample_ids"]),
        "max_new_tokens": int(config["max_new_tokens"]),
        "generation_sha256": sha256_file(Path(root) / "generation.jsonl"),
        "verified_manifest_entries": len(checks),
    }


def collect_environment():
    disk = shutil.disk_usage(Path(__file__).resolve().anchor)
    docker_server = run_command(
        ["docker", "version", "--format", "{{.Server.Version}}"]
    )
    docker_compose = run_command(["docker", "compose", "version"])
    docker_containers = run_command(
        ["docker", "ps", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"]
    )
    wsl = run_command(
        [
            "wsl",
            "-d",
            "Ubuntu",
            "--",
            "bash",
            "-lc",
            "python3 --version; go version; free -b; df -B1 / /mnt/c",
        ]
    )
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "windows": platform.platform(),
        "python": sys.version,
        "logical_cpu_count": os.cpu_count(),
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "docker_server": docker_server,
        "docker_compose": docker_compose,
        "running_containers": [
            line for line in docker_containers.splitlines() if line.strip()
        ],
        "wsl_distribution": "Ubuntu",
        "wsl_report": wsl,
    }


def write_results(output_dir, environment, inputs):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    artifacts = {
        "environment.json": environment,
        "input_manifest.json": inputs,
        "status.json": {"stage": "M0", "state": "completed"},
    }
    for filename, value in artifacts.items():
        (output_dir / filename).write_bytes(canonical_json_bytes(value) + b"\n")
    manifest = "".join(
        f"{sha256_file(output_dir / filename)}  {filename}\n"
        for filename in sorted(artifacts)
    )
    (output_dir / "checksums.sha256").write_text(manifest, encoding="ascii")


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = RESULTS_ROOT / f"m0-{stamp}"
    environment = collect_environment()
    inputs = {
        "comparison_e1": inspect_e1(DEFAULT_BASE),
        "authoritative_e1": inspect_e1(DEFAULT_FINAL),
    }
    for label, item in inputs.items():
        if item["generation_count"] != 2062 or item["moderation_count"] != 2062:
            raise ValueError(f"{label} does not contain 2062 complete records")
    write_results(output_dir, environment, inputs)
    print(output_dir)


if __name__ == "__main__":
    main()
