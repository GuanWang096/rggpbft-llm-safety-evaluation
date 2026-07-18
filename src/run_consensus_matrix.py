#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys
import time


def command_for(run, run_dir, run_script, *, skip_build):
    command = [
        sys.executable,
        str(run_script),
        "--mode",
        run["protocol"],
        "--nodes",
        str(run["nodes"]),
        "--groups",
        str(run["groups"]),
        "--rounds",
        str(run["rounds"]),
        "--delay-ms",
        str(run["delay_ms"]),
        "--round-timeout",
        str(run["round_timeout"]),
        "--view-timeout",
        str(run["view_timeout"]),
        "--fault-scenario",
        run["fault"],
    ]
    if run.get("fault_nodes") and run["fault_nodes"] != "":
        command.extend(["--fault-nodes", run["fault_nodes"]])
    command.extend([
        "--seed",
        str(run["seed"]),
        "--run-dir",
        str(run_dir.resolve()),
    ])
    if run.get("reputation_order"):
        command.extend(["--reputation-order", run["reputation_order"]])
    if run.get("netem_delay_ms", 0) > 0 or run.get("netem_jitter_ms", 0) > 0 or run.get("netem_loss_pct", 0) > 0:
        command.extend([
            "--netem-delay", str(run.get("netem_delay_ms", 0)),
            "--netem-jitter", str(run.get("netem_jitter_ms", 0)),
            "--netem-loss", str(run.get("netem_loss_pct", 0)),
        ])
    if skip_build:
        command.append("--skip-build")
    return command


def write_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def append_checksum(run_dir, name):
    digest = hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
    with (run_dir / "checksums.sha256").open("a", encoding="ascii") as output:
        output.write(f"{digest}  {name}\n")


def next_attempt_directory(base):
    base = pathlib.Path(base)
    if not base.exists():
        return base
    attempt = 1
    while pathlib.Path(f"{base}_retry{attempt}").exists():
        attempt += 1
    return pathlib.Path(f"{base}_retry{attempt}")


def monitor_stats(process, output_path):
    with output_path.open("w", encoding="utf-8") as output:
        while process.poll() is None:
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            record = {
                "time_ns": time.time_ns(),
                "exit_status": result.returncode,
                "containers": [
                    json.loads(line)
                    for line in result.stdout.splitlines()
                    if line.strip()
                ],
                "stderr": result.stderr.strip(),
            }
            output.write(json.dumps(record, sort_keys=True) + "\n")
            output.flush()
            time.sleep(2)


def validate_summary(run, summary):
    if summary.get("safety_violation_events", 0):
        raise RuntimeError("safety violation event detected")
    if summary.get("conflicting_commit_count", 0):
        raise RuntimeError("conflicting commit detected")
    if summary.get("invalid_new_view_events", 0):
        raise RuntimeError("invalid NEW_VIEW was accepted")
    if summary.get("invalid_view_change_events", 0):
        raise RuntimeError("invalid VIEW_CHANGE entered the protocol")
    if summary.get("driver_success_count") != run["rounds"]:
        raise RuntimeError(
            f"only {summary.get('driver_success_count')} of {run['rounds']} rounds committed"
        )
    if run["fault"] not in ("none", "f4") and summary.get("new_view_accepted_events", 0) < 1:
        raise RuntimeError("fault run completed without an accepted NEW_VIEW")
    if run["fault"] != "none" and summary.get("fault_injected_events", 0) < 1:
        raise RuntimeError("fault run completed without the configured injection barrier")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument(
        "--run-script",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parent
        / "rggpbft_distributed"
        / "run_v2.py",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-build", action="store_true", help="Skip Docker image build")
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    args.output_dir = args.output_dir.resolve()
    status_path = args.output_dir / "status.json"
    if args.resume:
        if not status_path.exists():
            raise SystemExit("--resume requires an existing status.json")
        stored_matrix = json.loads(
            (args.output_dir / "matrix.json").read_text(encoding="utf-8")
        )
        if stored_matrix != matrix:
            raise SystemExit("resume matrix differs from the stored matrix")
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("failed_run"):
            status.setdefault("archived_failures", []).append(status["failed_run"])
        status["state"] = "running"
        status["failed_run"] = None
    else:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        status = {"state": "running", "completed_runs": [], "failed_run": None}
        write_json(args.output_dir / "matrix.json", matrix)
    write_json(status_path, status)

    built = False
    completed = set(status["completed_runs"])
    for run in matrix["runs"]:
        if run["run_id"] in completed:
            continue
        base_run_dir = args.output_dir / run["run_id"]
        run_dir = next_attempt_directory(base_run_dir) if args.resume else base_run_dir
        run_dir.mkdir(parents=True, exist_ok=False)
        write_json(run_dir / "matrix_entry.json", run)
        log_path = args.output_dir / f"{run_dir.name}.runner.log"
        command = command_for(
            run, run_dir, args.run_script.resolve(), skip_build=(built or args.skip_build)
        )
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=args.run_script.resolve().parent,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            monitor_stats(
                process,
                args.output_dir / f"{run_dir.name}.docker-stats.jsonl",
            )
            exit_status = process.wait()
        built = built or exit_status == 0
        try:
            if exit_status:
                raise RuntimeError(f"run_v2 exited with status {exit_status}")
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            validate_summary(run, summary)
            status["completed_runs"].append(run["run_id"])
            completed.add(run["run_id"])
            write_json(status_path, status)
        except Exception as exc:
            status["state"] = "failed"
            status["failed_run"] = {
                "run_id": run["run_id"],
                "attempt_dir": run_dir.name,
                "error": repr(exc),
            }
            write_json(status_path, status)
            raise
    status["state"] = "completed"
    write_json(status_path, status)


if __name__ == "__main__":
    main()
