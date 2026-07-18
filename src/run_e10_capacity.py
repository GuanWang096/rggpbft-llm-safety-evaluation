#!/usr/bin/env python3
"""E10: Fabric/IPFS capacity curve experiment."""
import hashlib, json, pathlib, subprocess, sys, threading, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEED_BASE = 20260705

MANIFEST = ROOT / "results" / "b2-input-20260705T154954Z" / "full-b64" / "manifest.json"
BENCHMARK = ROOT / "src" / "fabric" / "benchmarks" / "e4_full_lifecycle.py"


def derive_pair_seed(block, repeat):
    material = "zte-sci-local-v1|%d|%s|M=na|delay=na|fault=na|batch=full-b64|repeat=%d" % (
        SEED_BASE, block, repeat
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    seed = int.from_bytes(bytes.fromhex(digest)[:8], "big") & 0x7FFFFFFFFFFFFFFF
    return {"pair_material": material, "sha256": digest, "seed": seed}


def check_fabric_network():
    """Verify Fabric peers are reachable before launching benchmarks."""
    import subprocess as sp
    ok = True
    for container, name in [("peer0.org1.example.com", "Org1"), ("peer0.org2.example.com", "Org2")]:
        try:
            r = sp.run(["docker", "exec", container,
                        "peer", "node", "status"],
                       capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                print(f"  WARNING: {name} peer not healthy: {r.stderr[:120]}")
                ok = False
        except Exception as e:
            print(f"  WARNING: Cannot reach {name} peer ({container}): {e}")
            ok = False
    # Check IPFS
    try:
        r = sp.run(["curl", "-s", "-X", "POST", "-o", "/dev/null", "-w", "%{http_code}",
                    "http://localhost:5001/api/v0/version"],
                   capture_output=True, text=True, timeout=10)
        if r.stdout.strip() != "200":
            print(f"  WARNING: IPFS not responding (HTTP {r.stdout.strip()})")
            ok = False
    except Exception as e:
        print(f"  WARNING: IPFS check failed: {e}")
        ok = False
    return ok


def build_e10_matrix():
    matrix = []
    for concurrency in (1, 4, 8, 16):
        for repeat in range(1, 6):
            block = "e10-fabric-c%d" % concurrency
            seed_info = derive_pair_seed(block, repeat)
            matrix.append({
                "pair_id": "e10-fabric-c%d-r%d" % (concurrency, repeat),
                "concurrency": concurrency,
                "repeat": repeat,
                "block": block,
                **seed_info,
            })
    # c32: 5 runs (supplemented from original 1)
    for concurrency in (32,):
        for repeat in range(1, 6):
            block = "e10-fabric-c%d" % concurrency
            seed_info = derive_pair_seed(block, repeat)
            matrix.append({
                "pair_id": "e10-fabric-c%d-r%d" % (concurrency, repeat),
                "concurrency": concurrency,
                "repeat": repeat,
                "block": block,
                **seed_info,
            })
    return matrix


def build_e10_resource_matrix(repeats=3):
    matrix = []
    for concurrency in (1, 8, 16, 32):
        for repeat in range(1, repeats + 1):
            block = f"e10-resource-c{concurrency}"
            matrix.append({
                "pair_id": f"e10-resource-c{concurrency}-r{repeat}",
                "concurrency": concurrency,
                "repeat": repeat,
                "block": block,
                "series": "resource-only-v2",
                **derive_pair_seed(block, repeat),
            })
    return matrix


def _host_resource_snapshot():
    try:
        import psutil
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "memory_total_bytes": memory.total,
            "memory_available_bytes": memory.available,
            "swap_total_bytes": swap.total,
            "swap_used_bytes": swap.used,
        }
    except ImportError:
        return {"error": "psutil is not installed"}


def collect_resource_snapshot():
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
        capture_output=True, text=True, timeout=15, check=False,
    )
    containers = []
    for line in result.stdout.splitlines():
        if line.strip():
            containers.append(json.loads(line))
    return {
        "time_ns": time.time_ns(),
        "exit_status": result.returncode,
        "containers": containers,
        "host": _host_resource_snapshot(),
        "stderr": result.stderr.strip(),
    }


class ResourceSampler:
    def __init__(self, output_path, interval_s=1.0):
        self.output_path = pathlib.Path(output_path)
        self.interval_s = interval_s
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name="e10-resource-sampler", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=20)
            if self.thread.is_alive():
                raise RuntimeError("resource sampler did not stop")

    def _run(self):
        with self.output_path.open("w", encoding="utf-8") as output:
            while not self.stop_event.is_set():
                try:
                    record = collect_resource_snapshot()
                except Exception as exc:
                    record = {"time_ns": time.time_ns(), "error": repr(exc), "containers": []}
                output.write(json.dumps(record, sort_keys=True) + "\n")
                output.flush()
                self.stop_event.wait(self.interval_s)


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def build_resource_summary(samples, minimum_samples=3):
    valid = [sample for sample in samples if sample.get("containers")]
    if len(valid) < minimum_samples:
        raise RuntimeError(
            f"resource stop gate requires at least {minimum_samples} timestamped samples; got {len(valid)}"
        )
    cpu_totals = []
    memory_totals = []
    host_used = []
    host_swap = []
    container_records = 0
    for sample in valid:
        cpu_total = 0.0
        memory_total = 0
        for container in sample["containers"]:
            cpu_total += float(container.get("CPUPerc", "0").rstrip("%"))
            memory_total += _parse_mem(container.get("MemUsage", "0/0").split("/")[0])
            container_records += 1
        cpu_totals.append(cpu_total)
        memory_totals.append(memory_total)
        host = sample.get("host", {})
        if "memory_total_bytes" in host and "memory_available_bytes" in host:
            host_used.append(host["memory_total_bytes"] - host["memory_available_bytes"])
        if "swap_used_bytes" in host:
            host_swap.append(host["swap_used_bytes"])
    return {
        "sample_count": len(valid),
        "container_record_count": container_records,
        "sampling_interval_s": 1.0,
        "peak_cpu_percent": round(max(cpu_totals), 2),
        "p95_cpu_percent": round(_percentile(cpu_totals, 0.95), 2),
        "mean_cpu_percent": round(sum(cpu_totals) / len(cpu_totals), 2),
        "peak_memory_bytes": max(memory_totals),
        "p95_memory_bytes": _percentile(memory_totals, 0.95),
        "mean_memory_bytes": int(sum(memory_totals) / len(memory_totals)),
        "host_peak_memory_used_bytes": max(host_used) if host_used else None,
        "host_peak_swap_used_bytes": max(host_swap) if host_swap else None,
        "first_sample_time_ns": valid[0]["time_ns"],
        "last_sample_time_ns": valid[-1]["time_ns"],
    }


def run_single(entry, output_dir):
    pair_id = entry["pair_id"]
    concurrency = entry["concurrency"]
    seed = entry["seed"]

    pair_dir = output_dir / pair_id
    pair_dir.mkdir(parents=True, exist_ok=False)

    run_id = pair_id
    bench_path = str(BENCHMARK.resolve())
    manifest_path = str(MANIFEST.resolve())
    out_path = str(pair_dir.resolve())
    fabric_cmd = [
        sys.executable, bench_path,
        "--batch-manifest", manifest_path,
        "--concurrency", str(concurrency),
        "--warmup", "1",
        "--seed", str(seed),
        "--output", out_path,
        "--run-id", run_id,
    ]

    print("  [%s] c=%d seed=%d" % (pair_id, concurrency, seed))

    # --- Resource monitoring: network baseline BEFORE benchmark ---
    net_baseline = {}
    try:
        r_net = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.NetIO}}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r_net.stdout.strip().split("\n"):
            parts = line.split("|")
            if len(parts) == 2:
                net_baseline[parts[0]] = parts[1]
        (pair_dir / "net_baseline.json").write_text(json.dumps(net_baseline, indent=2))
    except Exception as e:
        (pair_dir / "net_baseline.json").write_text(json.dumps({"error": str(e)}))

    # --- Start timestamped resource sampling ---
    stats_path = pair_dir / "docker-stats.jsonl"
    sampler = ResourceSampler(stats_path)
    sampler.start()

    # --- Run benchmark ---
    t0 = time.time()
    # Wrap benchmark in WSL — Fabric peer binary is Linux-only.
    # Use WSL native python3 to avoid Windows/Linux path translation issues.
    def _wsl(p):
        p = str(p).replace("\\", "/")
        if len(p) >= 2 and p[1] == ":":
            return "/mnt/" + p[0].lower() + p[2:]
        return p
    wsl_cmd = " ".join(["/usr/bin/python3"] + [_wsl(x) for x in fabric_cmd[1:]])
    wsl_cwd = _wsl(str(pathlib.Path.cwd()))
    try:
        r = subprocess.run(
            ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", f"cd {wsl_cwd} && {wsl_cmd}"],
            capture_output=True, text=True, timeout=1800,
        )
    finally:
        sampler.stop()
    elapsed = time.time() - t0
    (pair_dir / "run.log").write_text(
        "COMMAND: " + wsl_cmd + "\n\nSTDOUT:\n" + r.stdout + "\nSTDERR:\n" + r.stderr,
        encoding="utf-8",
    )

    # --- Network delta AFTER benchmark ---
    net_delta = {}
    try:
        r_net = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.NetIO}}"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r_net.stdout.strip().split("\n"):
            parts = line.split("|")
            if len(parts) == 2:
                name, io = parts[0], parts[1]
                baseline_io = net_baseline.get(name, "0B/0B")
                # Parse cumulative bytes from "1.2GiB / 3.4GiB" format
                b_rx, b_tx = _parse_net_pair(baseline_io)
                a_rx, a_tx = _parse_net_pair(io)
                net_delta[name] = {
                    "baseline_rx_bytes": b_rx, "baseline_tx_bytes": b_tx,
                    "after_rx_bytes": a_rx, "after_tx_bytes": a_tx,
                    "delta_rx_bytes": max(0, a_rx - b_rx),
                    "delta_tx_bytes": max(0, a_tx - b_tx),
                }
        (pair_dir / "net_delta.json").write_text(json.dumps(net_delta, indent=2))
    except Exception as e:
        (pair_dir / "net_delta.json").write_text(json.dumps({"error": str(e)}))

    samples = [
        json.loads(line) for line in stats_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    resource_summary = build_resource_summary(samples, minimum_samples=3)
    (pair_dir / "resource_summary.json").write_text(
        json.dumps(resource_summary, indent=2), encoding="utf-8"
    )

    if r.returncode != 0:
        print("    FAIL (exit=%d, %.0fs)" % (r.returncode, elapsed))
        (pair_dir / "FAILED").write_text("exit=%d\n%s" % (r.returncode, r.stderr[-500:]))
        return False

    summary_glob = list(pair_dir.rglob("summary.json"))
    if summary_glob:
        summary = json.loads(summary_glob[0].read_text())
        sr = summary.get("workflow_success_rate", 0)
        failed = summary.get("failed_workflows", 0)
        lat_p95 = summary.get("latency_ms", {}).get("p95", 0)
        print("    OK (%.0fs, success=%.0f%%, failed=%d, p95=%.0fms)" % (
            elapsed, sr * 100, failed, lat_p95
        ))
    else:
        print("    OK (%.0fs)" % elapsed)

    checksum_names = [
        "docker-stats.jsonl", "resource_summary.json", "net_baseline.json",
        "net_delta.json", "run.log",
    ]
    with (pair_dir / "checksums.sha256").open("w", encoding="ascii") as output:
        for name in checksum_names:
            digest = hashlib.sha256((pair_dir / name).read_bytes()).hexdigest()
            output.write(f"{digest}  {name}\n")
    return True


def _parse_mem(s):
    s = s.strip()
    if s.endswith("GiB"):
        return int(float(s[:-3]) * 1024 * 1024 * 1024)
    elif s.endswith("MiB"):
        return int(float(s[:-3]) * 1024 * 1024)
    elif s.endswith("KiB"):
        return int(float(s[:-3]) * 1024)
    elif s.endswith("GB"):
        return int(float(s[:-2]) * 1000 * 1000 * 1000)
    elif s.endswith("MB"):
        return int(float(s[:-2]) * 1000 * 1000)
    elif s.endswith("kB"):
        return int(float(s[:-2]) * 1000)
    elif s.endswith("B"):
        return int(float(s[:-1]))
    return 0


def _parse_net(s):
    """Parse docker NetIO like '1.5kB / 300B' -> total bytes."""
    parts = s.split("/")
    total = 0
    for p in parts:
        total += _parse_net_bytes(p)
    return total


def _parse_net_bytes(s):
    """Parse a single net IO value like '1.5kB' -> bytes."""
    s = s.strip()
    if s.endswith("GiB"):
        return int(float(s[:-3]) * 1024 * 1024 * 1024)
    elif s.endswith("MiB"):
        return int(float(s[:-3]) * 1024 * 1024)
    elif s.endswith("KiB"):
        return int(float(s[:-3]) * 1024)
    elif s.endswith("GB"):
        return int(float(s[:-2]) * 1000 * 1000 * 1000)
    elif s.endswith("MB"):
        return int(float(s[:-2]) * 1000 * 1000)
    elif s.endswith("kB"):
        return int(float(s[:-2]) * 1000)
    elif s.endswith("B"):
        return int(float(s[:-1]))
    return 0


def _parse_net_pair(s):
    """Parse docker NetIO like '1.5kB / 300B' -> (rx_bytes, tx_bytes)."""
    parts = s.split("/")
    rx = _parse_net_bytes(parts[0]) if len(parts) >= 1 else 0
    tx = _parse_net_bytes(parts[1]) if len(parts) >= 2 else 0
    return rx, tx


def main():
    import argparse
    parser = argparse.ArgumentParser(description="E10 capacity curve runner")
    parser.add_argument("--test", action="store_true", help="Run single c1 test")
    parser.add_argument("--concurrency", type=int, help="Run specific concurrency level")
    parser.add_argument("--all", action="store_true", help="Run all E10 matrix")
    parser.add_argument("--resource-only", action="store_true",
                        help="Run c=1/8/16/32 resource-evidence subset")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    if args.test:
        entry = {
            "pair_id": "e10-test-c1-r1",
            "concurrency": 1,
            "seed": derive_pair_seed("e10-test-c1", 1)["seed"],
        }
        output_dir = ROOT / "results" / "e10-test"
        output_dir.mkdir(parents=True, exist_ok=True)
        print("Test run: c=1")
        run_single(entry, output_dir)
        return

    matrix = (build_e10_resource_matrix(args.repeats)
              if args.resource_only else build_e10_matrix())

    if args.concurrency:
        matrix = [e for e in matrix if e["concurrency"] == args.concurrency]
    elif args.all or args.resource_only:
        pass  # Full matrix: c1x5 + c4x5 + c8x5 + c16x5 + c32x1 = 21 runs
    else:
        parser.print_help()
        return

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    output_dir = ROOT / "results" / ("e10-capacity-" + ts)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "experiment": "E10",
        "description": "Fabric/IPFS capacity curve",
        "seed_base": SEED_BASE,
        "manifest": str(MANIFEST),
        "matrix": matrix,
        "series": "resource-only-v2" if args.resource_only else "capacity-v1",
    }
    (output_dir / "config.json").write_text(json.dumps(config, indent=2))
    print("E10 capacity curve: %d runs -> %s" % (len(matrix), output_dir))
    print("=" * 60)

    print("Fabric network health check...")
    if not check_fabric_network():
        print("STOP-GATE: Fabric network not healthy. Start the network first, then re-run.")
        sys.exit(1)
    print("  All services OK")

    results = []
    for entry in matrix:
        try:
            ok = run_single(entry, output_dir)
            results.append({"pair_id": entry["pair_id"], "ok": ok})
            if not ok:
                break
        except Exception as exc:
            results.append({"pair_id": entry["pair_id"], "ok": False, "error": repr(exc)})
            break

    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count
    print("\n" + "=" * 60)
    print("E10 complete: %d OK, %d FAIL, output: %s" % (ok_count, fail_count, output_dir))

    state = "completed" if fail_count == 0 and len(results) == len(matrix) else "failed"
    (output_dir / "status.json").write_text(json.dumps({
        "state": state, "planned": len(matrix), "ok": ok_count, "fail": fail_count,
        "results": results,
    }, indent=2))
    if state != "completed":
        sys.exit(1)


if __name__ == "__main__":
    main()
