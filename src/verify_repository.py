#!/usr/bin/env python3
"""M0: Complete environment report including all required artifacts."""
import hashlib, json, os, pathlib, platform, shutil, subprocess, sys, time
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]

def sha256_file(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def required_tests_passed(test_report):
    tests = test_report.get("tests", {})
    return bool(tests) and all(
        entry.get("return_code") == 0 and entry.get("passed") is True
        for entry in tests.values()
    )


def netem_probe_passed(netem_probe):
    probe = netem_probe.get("dual_container_50ms", {})
    return (
        probe.get("exit_code") == 0
        and probe.get("safety_violations") == 0
        and probe.get("rounds_completed") == 2
    )


def probe_round_count(summary):
    return summary.get("round_count", summary.get("client_committed_rounds", 0))


def build_status(artifacts, test_report, netem_probe):
    failed_tests = [
        name for name, entry in test_report.get("tests", {}).items()
        if entry.get("return_code") != 0 or entry.get("passed") is not True
    ]
    failed_checks = []
    if not netem_probe_passed(netem_probe):
        failed_checks.append("netem_probe")
    return {
        "stage": "M0",
        "state": "completed" if not failed_tests and not failed_checks and required_tests_passed(test_report) else "failed",
        "failed_tests": failed_tests,
        "failed_checks": failed_checks,
        "artifacts": list(artifacts),
    }

def run_cmd(cmd, check=True, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)
    return r.stdout.strip()

def generate_m0():
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "results" / f"m0-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # === 1. m0_environment.json ===
    disk = shutil.disk_usage(str(HERE.anchor))
    wsl_info = ""
    try:
        wsl_info = run_cmd(["wsl", "-d", "Ubuntu", "--", "bash", "-lc",
                            "echo 'python:'; python3 --version 2>&1; echo 'go:'; go version 2>&1; "
                            "echo 'mem:'; free -b 2>&1 | head -3; echo 'disk:'; df -B1 / /mnt/c 2>&1"],
                           check=False, timeout=30)
    except Exception:
        wsl_info = "wsl unavailable"

    environment = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": platform.node(),
        "os": platform.platform(),
        "python_version": sys.version,
        "logical_cpu_count": os.cpu_count(),
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "wsl_distribution": "Ubuntu",
        "wsl_report": wsl_info,
    }
    try:
        environment["docker_server"] = run_cmd(["docker", "version", "--format", "{{.Server.Version}}"], check=False)
        environment["docker_compose"] = run_cmd(["docker", "compose", "version"], check=False)
        environment["running_containers"] = run_cmd(
            ["docker", "ps", "--format", "{{.Names}}|{{.Status}}|{{.Image}}"], check=False
        ).splitlines()
    except Exception:
        environment["docker_server"] = "unavailable"

    # Git snapshot
    git_available = False
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10)
        git_available = r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        pass
    environment["git_available"] = git_available
    if git_available:
        try:
            environment["git_commit"] = run_cmd(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=False)
            environment["git_branch"] = run_cmd(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"], check=False)
            environment["git_status"] = run_cmd(["git", "-C", str(ROOT), "status", "--short"], check=False).splitlines()
        except Exception:
            pass

    # Go version
    try:
        environment["go_version"] = run_cmd(["go", "version"], check=False)
    except Exception:
        pass

    (out_dir / "m0_environment.json").write_text(json.dumps(environment, indent=2, ensure_ascii=False))

    # === 2. m0_versions.json ===
    versions = {"python": sys.version, "timestamp": ts}
    try:
        versions["go"] = run_cmd(["go", "version"], check=False)
    except Exception:
        pass
    try:
        r = run_cmd(["pip", "freeze", "--all"], check=False)
        versions["pip_packages"] = sorted(r.splitlines())
    except Exception:
        pass
    try:
        r = run_cmd(["docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"], check=False)
        versions["docker_images"] = sorted(r.splitlines())
    except Exception:
        pass
    (out_dir / "m0_versions.json").write_text(json.dumps(versions, indent=2, ensure_ascii=False))

    # === 3. e1_e7_schema_audit.json ===
    e1_base = ROOT / "results" / "e1-base-512"
    e1_final = ROOT / "results" / "e1-final-2048-topup"
    audit = {"timestamp": ts, "e1_runs": {}}
    for label, e1_dir in [("base", e1_base), ("final", e1_final)]:
        gen_path = e1_dir / "generation.jsonl"
        mod_path = e1_dir / "moderation.jsonl"
        result = {"path": str(e1_dir), "exists": e1_dir.exists()}
        if gen_path.exists():
            gen = [json.loads(l) for l in gen_path.read_text("utf-8").splitlines() if l.strip()]
            result["generation_count"] = len(gen)
            result["gen_sample_ids"] = sorted([r.get("sample_id", "") for r in gen])
            result["gen_fields"] = list(gen[0].keys()) if gen else []
        if mod_path.exists():
            mod = [json.loads(l) for l in mod_path.read_text("utf-8").splitlines() if l.strip()]
            result["moderation_count"] = len(mod)
            result["mod_sample_ids"] = sorted([r.get("sample_id", "") for r in mod])
            result["mod_fields"] = list(mod[0].keys()) if mod else []
            # Validate field types
            safety_vals = set(r.get("safety", "") for r in mod)
            refusal_vals = set(r.get("refusal", "") for r in mod)
            es_vals = set(r.get("expected_input_safe", "") for r in gen if "expected_input_safe" in r)
            result["safety_values"] = sorted(safety_vals)
            result["refusal_values"] = sorted(refusal_vals)
            result["expected_input_safe_values"] = sorted(str(v) for v in es_vals)
        if "gen_sample_ids" in result and "mod_sample_ids" in result:
            result["sample_id_match"] = result["gen_sample_ids"] == result["mod_sample_ids"]
            result["unique_sample_count"] = len(set(result["gen_sample_ids"]))
        audit["e1_runs"][label] = result
    (out_dir / "e1_e7_schema_audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False))

    # === 4. m0_netem_probe.json ===
    # Run dual-container 50ms netem probe using zte-rggpbft:v2 image
    netem_probe = {"timestamp": ts, "probe": "dual-container-50ms-netem"}
    try:
        probe_dir = out_dir / "netem_probe"
        probe_dir.mkdir(exist_ok=True)
        cmd = [
            sys.executable,
            str(ROOT / "src" / "rggpbft_distributed" / "run_v2.py"),
            "--mode", "rgg",
            "--nodes", "16",
            "--groups", "4",
            "--rounds", "2",
            "--delay-ms", "5",
            "--round-timeout", "30",
            "--view-timeout", "2.0",
            "--fault-scenario", "none",
            "--seed", "20260705",
            "--run-dir", str(probe_dir),
            "--image", "zte-rggpbft:v2",
            "--skip-build",
            "--netem-delay", "50",
            "--netem-jitter", "5",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        netem_probe["dual_container_50ms"] = {
            "exit_code": r.returncode,
            "stdout_tail": r.stdout.strip()[-500:] if r.stdout else "",
            "stderr_tail": r.stderr.strip()[-500:] if r.stderr else "",
        }
        summary_path = probe_dir / "summary.json"
        if summary_path.exists():
            s = json.loads(summary_path.read_text())
            netem_probe["dual_container_50ms"]["safety_violations"] = s.get("safety_violation_events", 0)
            netem_probe["dual_container_50ms"]["rounds_completed"] = probe_round_count(s)
    except Exception as e:
        netem_probe["dual_container_50ms"] = {"error": str(e)}
    (out_dir / "m0_netem_probe.json").write_text(json.dumps(netem_probe, indent=2))

    # === 5. m0_test_report.json ===
    test_report = {"timestamp": ts, "tests": {}}

    def record_test(label, cmd, cwd, env=None, timeout=120):
        """Run a test and record full details: command, cwd, interpreter, rc, stdout, stderr."""
        entry = {
            "command": " ".join(str(x) for x in cmd),
            "cwd": str(cwd),
            "interpreter": cmd[0] if cmd else None,
        }
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(cwd), env=env)
            entry["return_code"] = r.returncode
            entry["stdout_tail"] = r.stdout.strip()[-2000:] if r.stdout else ""
            entry["stderr_tail"] = r.stderr.strip()[-2000:] if r.stderr else ""
            entry["passed"] = r.returncode == 0
        except subprocess.TimeoutExpired:
            entry["return_code"] = None
            entry["error"] = "timeout"
        except Exception as e:
            entry["return_code"] = None
            entry["error"] = str(e)
        test_report["tests"][label] = entry

    # Python tests: src/rggpbft_distributed
    record_test("python_rggpbft",
                [sys.executable, "-m", "pytest", "-q", "--tb=short"],
                ROOT / "src" / "rggpbft_distributed")

    # Python workflow and E1 tests.
    record_test("python_workflow_tests",
                [sys.executable, "-m", "pytest", "-q", "--tb=short"],
                HERE / "tests")

    # Full Python collection from the unified source directory.
    record_test("python_full_collection",
                [sys.executable, "-m", "pytest", "-q", "--tb=short"],
                HERE)

    # Go tests: chaincode (where go.mod lives, run via WSL)
    chaincode_dir = ROOT / "src" / "fabric" / "chaincode"
    resolved = chaincode_dir.resolve()
    drive = resolved.drive.rstrip(":").lower()
    wsl_chaincode_dir = f"/mnt/{drive}/" + resolved.as_posix().split(":/", 1)[1]
    record_test("go_chaincode",
                ["wsl", "-d", "Ubuntu", "--", "bash", "-lc",
                 f"cd {wsl_chaincode_dir} && go test -count=1 ./..."],
                HERE)

    (out_dir / "m0_test_report.json").write_text(json.dumps(test_report, indent=2))

    # === 6. m0_e1_checksums.json ===
    e1_checksums = {"timestamp": ts, "e1_runs": {}}
    for label, e1_dir in [("base", e1_base), ("final", e1_final)]:
        chk = {}
        for fname in ["generation.jsonl", "moderation.jsonl", "checksums.sha256", "config.json"]:
            fp = e1_dir / fname
            if fp.exists():
                chk[fname] = sha256_file(fp)
        e1_checksums["e1_runs"][label] = chk
    (out_dir / "m0_e1_checksums.json").write_text(json.dumps(e1_checksums, indent=2))

    # === 7. m0_git_snapshot.json ===
    git_snap = {"timestamp": ts}
    git_available = False
    try:
        r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10)
        git_available = r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        git_available = False

    git_snap["available"] = git_available
    if git_available:
        try:
            git_snap["commit"] = run_cmd(["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=False)
            git_snap["branch"] = run_cmd(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"], check=False)
            git_snap["status"] = run_cmd(["git", "-C", str(ROOT), "status", "--short"], check=False).splitlines()
            git_snap["log_last_10"] = run_cmd(
                ["git", "-C", str(ROOT), "log", "--oneline", "-10", "--format=%h %s"], check=False
            ).splitlines()
        except Exception:
            pass
    else:
        git_snap["reason"] = f"not a git repository: {ROOT}"
    (out_dir / "m0_git_snapshot.json").write_text(json.dumps(git_snap, indent=2))

    # === 8. m0_b2_verify.json ===
    b2_verify = {"timestamp": ts}
    b2_input = ROOT / "results" / "b2-input-20260705T154954Z"
    if b2_input.exists():
        b2_verify["b2_input_exists"] = True
        manifest = b2_input / "full-b64" / "manifest.json"
        if manifest.exists():
            m = json.loads(manifest.read_text())
            b2_verify["manifest_batches"] = len(m.get("batches", []))
            b2_verify["manifest_total_samples"] = m.get("total_samples", 0)
    else:
        b2_verify["b2_input_exists"] = False
    (out_dir / "m0_b2_verify.json").write_text(json.dumps(b2_verify, indent=2))

    # === Write manifest ===
    all_files = sorted([
        "m0_environment.json", "m0_versions.json", "e1_e7_schema_audit.json",
        "m0_netem_probe.json", "m0_test_report.json", "m0_e1_checksums.json",
        "m0_git_snapshot.json", "m0_b2_verify.json",
    ])
    status = build_status(all_files, test_report, netem_probe)
    (out_dir / "status.json").write_text(json.dumps(status, indent=2))
    checksum_files = all_files + ["status.json"]
    manifest_lines = [f"{sha256_file(out_dir / f)}  {f}" for f in checksum_files]
    (out_dir / "checksums.sha256").write_text("\n".join(manifest_lines) + "\n")

    print(f"M0 complete: {out_dir}")
    for f in all_files:
        print(f"  {f}")
    if status["state"] != "completed":
        failed = status["failed_tests"] + status["failed_checks"]
        raise SystemExit(f"M0 failed required checks: {', '.join(failed)}")

if __name__ == "__main__":
    generate_m0()
