import argparse
import hashlib
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from analyze_e1_results import canonical_json_bytes, sha256_file


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_MANIFEST = (
    ROOT
    / "results"
    / "b2-input-20260705T154954Z"
    / "full-b64"
    / "manifest.json"
)
DEFAULT_INPUT_ROOT = DEFAULT_MANIFEST.parents[1]
DEFAULT_RESULTS = ROOT / "results"


def derive_pair_seed(*, block, batch, repeat, nodes="na", delay="na", fault="na"):
    material = (
        f"zte-sci-local-v1|20260705|{block}|M={nodes}|delay={delay}|"
        f"fault={fault}|batch={batch}|repeat={repeat}"
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    seed = int.from_bytes(bytes.fromhex(digest)[:8], "big") & 0x7FFFFFFFFFFFFFFF
    return {"pair_material": material, "sha256": digest, "seed": seed}


def build_matrix():
    matrix = []
    for repeat in range(1, 6):
        for concurrency in (1, 4, 8):
            derived = derive_pair_seed(block="B2", batch=64, repeat=repeat)
            matrix.append(
                {
                    "pair_id": f"b2-c{concurrency}-r{repeat}",
                    "concurrency": concurrency,
                    "repeat": repeat,
                    **derived,
                }
            )
    return matrix


def build_ablation_matrix(input_root):
    input_root = Path(input_root)
    matrix = []
    for repeat in range(1, 6):
        for batch_size in (1, 16, 64, 256):
            derived = derive_pair_seed(
                block="B2", batch=batch_size, repeat=repeat
            )
            matrix.append(
                {
                    "pair_id": f"b2-ablation-b{batch_size}-c4-r{repeat}",
                    "concurrency": 4,
                    "repeat": repeat,
                    "batch_size": batch_size,
                    "evidence_record_count": 256,
                    "manifest": str(
                        input_root
                        / f"stratified-256-b{batch_size}"
                        / "manifest.json"
                    ),
                    **derived,
                }
            )
    return matrix


def to_wsl_path(path):
    path = Path(path).resolve()
    drive = path.drive.rstrip(":").lower()
    relative = path.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive}/{relative}"


def _docker_stats(stop_event, output_path):
    with output_path.open("w", encoding="utf-8") as handle:
        while not stop_event.is_set():
            completed = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            timestamp = datetime.now(timezone.utc).isoformat()
            for line in completed.stdout.splitlines():
                if line.strip():
                    handle.write(
                        json.dumps(
                            {"timestamp_utc": timestamp, "stats": json.loads(line)},
                            sort_keys=True,
                        )
                        + "\n"
                    )
            handle.flush()
            stop_event.wait(2)


def run_monitored(command, log_path, stats_path):
    stop_event = threading.Event()
    monitor = threading.Thread(
        target=_docker_stats, args=(stop_event, stats_path), daemon=True
    )
    monitor.start()
    try:
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
    finally:
        stop_event.set()
        monitor.join(timeout=10)
    if completed.returncode:
        raise RuntimeError(
            f"command failed with exit {completed.returncode}; see {log_path}"
        )


def _write_status(path, value):
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _validate_run_summary(path, expected_evidence_count):
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary["failed_workflows"] != 0 or summary["workflow_success_rate"] != 1:
        raise RuntimeError(f"workflow failures in {path}")
    if summary.get("evidence_record_count") != expected_evidence_count:
        raise RuntimeError(f"unexpected evidence record count in {path}")
    return summary


def completed_canonical_pair_ids(output_dir, completed_attempt_ids):
    canonical = set()
    for attempt_id in completed_attempt_ids:
        summary_path = Path(output_dir) / attempt_id / "summary.json"
        if not summary_path.exists():
            raise RuntimeError(f"completed attempt is missing summary: {attempt_id}")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        canonical.add(summary["pair"]["pair_id"])
    return canonical


def next_attempt_id(output_dir, pair_id):
    output_dir = Path(output_dir)
    if not (output_dir / pair_id).exists():
        return pair_id, 0
    retry = 1
    while (output_dir / f"{pair_id}_retry{retry}").exists():
        retry += 1
    return f"{pair_id}_retry{retry}", retry


def run_matrix(
    manifest,
    output_dir,
    matrix=None,
    *,
    resume=False,
    resume_reason="",
):
    manifest = Path(manifest).resolve()
    output_dir = Path(output_dir).resolve()
    matrix = build_matrix() if matrix is None else list(matrix)
    manifests = sorted(
        {str(Path(item.get("manifest", manifest)).resolve()) for item in matrix}
    )
    config = {
        "stage": "B2-replay",
        "manifests": {
            path: sha256_file(Path(path)) for path in manifests
        },
        "matrix": matrix,
        "systems": ["fabric-ipfs", "signed-log"],
    }
    if resume:
        if not output_dir.is_dir():
            raise RuntimeError("resume output directory does not exist")
        recorded_config = json.loads(
            (output_dir / "config.json").read_text(encoding="utf-8")
        )
        if recorded_config != config:
            raise RuntimeError("resume matrix or manifest differs from recorded config")
        status = json.loads((output_dir / "status.json").read_text(encoding="utf-8"))
        failed_attempt = status.get("failed_pair")
        status.setdefault("infrastructure_failures", [])
        if failed_attempt:
            status["infrastructure_failures"].append(
                {
                    "attempt_id": failed_attempt,
                    "reason": resume_reason or "external infrastructure interruption",
                }
            )
        status["state"] = "running"
        status["failed_pair"] = None
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
        (output_dir / "config.json").write_bytes(
            canonical_json_bytes(config) + b"\n"
        )
        status = {"state": "running", "completed_pairs": [], "failed_pair": None}
    _write_status(output_dir / "status.json", status)
    completed_canonical = completed_canonical_pair_ids(
        output_dir, status["completed_pairs"]
    )

    benchmark = ROOT / "src" / "fabric" / "benchmarks" / "e4_full_lifecycle.py"
    baseline_root = ROOT / "src" / "fabric" / "baseline"
    baseline_binary = "/tmp/zte-e6-real-replay"
    build = [
        "wsl", "-d", "Ubuntu", "--", "bash", "-lc",
        "cd " + to_wsl_path(baseline_root) +
        " && GOTOOLCHAIN=local GOFLAGS=-buildvcs=false go build -o " +
        baseline_binary + " ./cmd/workload",
    ]
    subprocess.run(build, check=True)

    for item in matrix:
        if item["pair_id"] in completed_canonical:
            continue
        item_manifest = Path(item.get("manifest", manifest)).resolve()
        attempt_id, retry = next_attempt_id(output_dir, item["pair_id"])
        pair_dir = output_dir / attempt_id
        pair_dir.mkdir()
        fabric_root = pair_dir / "fabric"
        signed_root = pair_dir / "signed"
        fabric_root.mkdir()
        signed_root.mkdir()
        batch_suffix = (
            f"-b{item['batch_size']}" if "batch_size" in item else ""
        )
        run_id = (
            f"b2-fabric{batch_suffix}-c{item['concurrency']}-r{item['repeat']}"
        )
        if retry:
            run_id += f"-retry{retry}"
        fabric_command = [
            "wsl", "-d", "Ubuntu", "--", "python3",
            to_wsl_path(benchmark),
            "--batch-manifest", to_wsl_path(item_manifest),
            "--concurrency", str(item["concurrency"]),
            "--warmup", "1",
            "--seed", str(item["seed"]),
            "--output", to_wsl_path(fabric_root),
            "--run-id", run_id,
        ]
        signed_command = [
            "wsl", "-d", "Ubuntu", "--", baseline_binary,
            "-mode", "lifecycle",
            "-c", str(item["concurrency"]),
            "-warmup", "1",
            "-seed", str(item["seed"]),
            "-manifest", to_wsl_path(item_manifest),
            "-out", to_wsl_path(signed_root),
        ]
        commands = (
            [("fabric", fabric_command), ("signed", signed_command)]
            if item["repeat"] % 2
            else [("signed", signed_command), ("fabric", fabric_command)]
        )
        try:
            for name, command in commands:
                run_monitored(
                    command,
                    pair_dir / f"{name}.log",
                    pair_dir / f"{name}_docker_stats.jsonl",
                )
            expected_evidence_count = item.get("evidence_record_count", 2062)
            fabric_summary = _validate_run_summary(
                fabric_root / run_id / "summary.json", expected_evidence_count
            )
            signed_dirs = list(signed_root.glob("e6-lifecycle-*-real"))
            if len(signed_dirs) != 1:
                raise RuntimeError(f"expected one signed run in {signed_root}")
            signed_summary = _validate_run_summary(
                signed_dirs[0] / "summary.json", expected_evidence_count
            )
            pair_record = dict(item)
            pair_record.update({"attempt_id": attempt_id, "retry": retry})
            pair_summary = {
                "pair": pair_record,
                "fabric": fabric_summary,
                "signed": signed_summary,
            }
            (pair_dir / "summary.json").write_bytes(
                canonical_json_bytes(pair_summary) + b"\n"
            )
            status["completed_pairs"].append(attempt_id)
            completed_canonical.add(item["pair_id"])
            _write_status(output_dir / "status.json", status)
            print(
                f"completed {attempt_id} "
                f"({len(completed_canonical)}/{len(matrix)})",
                flush=True,
            )
        except Exception:
            status["state"] = "failed"
            status["failed_pair"] = attempt_id
            _write_status(output_dir / "status.json", status)
            raise

    status["state"] = "completed"
    _write_status(output_dir / "status.json", status)
    files = sorted(path for path in output_dir.rglob("*") if path.is_file())
    with (output_dir / "checksums.sha256").open("w", encoding="ascii") as handle:
        for path in files:
            handle.write(
                f"{sha256_file(path)}  {path.relative_to(output_dir).as_posix()}\n"
            )


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--mode", choices=("main", "ablation"), default="main")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--resume-reason", default="")
    return parser


def main():
    args = build_parser().parse_args()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = DEFAULT_RESULTS / f"b2-replay-{stamp}"
    matrix = (
        build_matrix()
        if args.mode == "main"
        else build_ablation_matrix(args.input_root)
    )
    run_matrix(
        args.manifest,
        output_dir,
        matrix,
        resume=args.resume,
        resume_reason=args.resume_reason,
    )
    print(output_dir)


if __name__ == "__main__":
    main()
