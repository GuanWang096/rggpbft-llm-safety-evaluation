#!/usr/bin/env python3
"""RGG-PBFT distributed benchmark runner with phased startup for netem.

Phase 1: compose up collector + nodes (no driver)
Phase 2: ready barrier — all containers healthy
Phase 3: apply & verify netem qdisc (if configured) with check=True
Phase 4: RTT gate verification
Phase 5: start driver
Phase 6: wait for collector
Finally: cleanup qdisc, verify no netem, compose down
"""
import argparse
import hashlib
import json
import pathlib
import platform
import re
import subprocess
import time


def read_events(events_path):
    path = pathlib.Path(events_path)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def ready_node_ids(events_path):
    return {
        int(event["node"])
        for event in read_events(events_path)
        if event.get("type") == "READY" and isinstance(event.get("node"), int)
    }


def first_non_ready_event(events_path):
    return next((event for event in read_events(events_path) if event.get("type") != "READY"), None)


def event_unix_seconds(event):
    return event.get("time_ns", 0) / 1_000_000_000 if event else 0.0


def qdisc_cleanup_complete(entries, node_count):
    nodes = {entry.get("node") for entry in entries if "netem" not in entry.get("qdisc", "")}
    return nodes == set(range(node_count))


def wait_for_event_type(events_path, event_type, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = read_events(events_path)
        matching = [event for event in events if event.get("type") == event_type]
        if matching:
            return matching[-1]
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {event_type} event")


def compose_definition(args, run_dir, include_driver=False):
    hard_timeout = 50 + args.rounds * (args.round_timeout + 1)
    has_netem = any(
        getattr(args, name, 0) > 0
        for name in ("netem_delay", "netem_jitter", "netem_loss")
    )
    common = {
        "M": str(args.nodes),
        "K_G": str(args.groups) if args.mode == "rgg" else "None",
        "PORT": "9000",
        "DELAY_MS": str(args.delay_ms),
        "N_ROUNDS": str(args.rounds),
        "ROUND_TIMEOUT": str(args.round_timeout),
        "COLLECTOR_HOST": "collector",
        "COLLECTOR_PORT": "9999",
        "FAULT_MODE": args.fault_mode,
        "FAULT_SCENARIO": args.fault_scenario,
        "FAULT_NODES": args.fault_nodes,
        "FAULT_DELAY_MS": str(args.fault_delay_ms),
        "HARD_TIMEOUT_SECONDS": str(hard_timeout),
        "VIEW_TIMEOUT_SECONDS": str(args.view_timeout),
        "REPUTATION_ORDER": args.reputation_order or ",".join(map(str, range(args.nodes))),
        "RUN_SEED": str(args.seed),
        "DIGESTS_PATH": "/results/decision_digests.json",
        "NETEM_CLEANUP_BARRIER_PATH": "/results/qdisc_cleanup_complete" if has_netem else "",
    }
    services = {
        "collector": {
            "image": args.image,
            "command": ["python", "collector_v2.py"],
            "environment": {
                **common,
                "EVENTS_PATH": "/results/events.jsonl",
                "SUMMARY_PATH": "/results/summary.json",
            },
            "volumes": [f"{run_dir}:/results"],
        }
    }
    node_names = []
    for node_id in range(args.nodes):
        name = f"node{node_id}"
        node_names.append(name)
        services[name] = {
            "image": args.image,
            "command": ["python", "node_v2.py"],
            "environment": {**common, "NODE_ID": str(node_id)},
            "depends_on": ["collector"],
            "cap_add": ["NET_ADMIN"],
        }
    if include_driver:
        services["driver"] = {
            "image": args.image,
            "command": ["python", "driver_v2.py"],
            "environment": common,
            "depends_on": node_names,
        }
    return {"services": services, "networks": {"default": {"driver": "bridge"}}}


def run_command(command, log_path, cwd=None, timeout=None):
    with log_path.open("a", encoding="utf-8") as log:
        try:
            result = subprocess.run(
                command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"command timed out after {timeout}s: {' '.join(command)}") from exc
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")


def docker_cli_path(path):
    path = pathlib.Path(path).resolve()
    if pathlib.Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists() and str(path).startswith("/mnt/"):
        return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
    return str(path)


def get_container_iface(container_name):
    """Discover non-loopback interface dynamically. Uses check=True."""
    r = subprocess.run(
        ["docker", "exec", container_name, "sh", "-c",
         "ip -o -4 addr show scope global 2>/dev/null | head -1 | awk '{print $2}'"],
        capture_output=True, text=True, check=True, timeout=10,
    )
    iface = r.stdout.strip()
    if not iface:
        # Fallback: try link show
        r2 = subprocess.run(
            ["docker", "exec", container_name, "sh", "-c",
             "ip -o link show 2>/dev/null | grep -v lo | head -1 | awk -F': ' '{print $2}' | awk '{print $1}'"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        iface = r2.stdout.strip()
    if not iface or " " in iface:
        raise RuntimeError(f"No non-loopback interface found in {container_name}: got '{iface}'")
    return iface


def apply_qdisc(container_name, iface, delay_ms, jitter_ms, loss_pct):
    """Apply tc netem qdisc with check=True. Returns the applied qdisc output."""
    # Clear existing
    subprocess.run(
        ["docker", "exec", container_name, "sh", "-c", f"tc qdisc del dev {iface} root 2>/dev/null; true"],
        capture_output=True, check=False, timeout=10,
    )
    # Build netem command
    parts = [f"tc qdisc add dev {iface} root netem"]
    if jitter_ms > 0:
        parts.append(f"delay {delay_ms}ms {jitter_ms}ms")
    else:
        parts.append(f"delay {delay_ms}ms")
    if loss_pct > 0:
        parts.append(f"loss {loss_pct}%")
    cmd = " ".join(parts)
    subprocess.run(
        ["docker", "exec", container_name, "sh", "-c", cmd],
        capture_output=True, check=True, timeout=10,
    )
    # Read back
    r = subprocess.run(
        ["docker", "exec", container_name, "tc", "qdisc", "show", "dev", iface],
        capture_output=True, text=True, check=True, timeout=10,
    )
    return r.stdout.strip()


def verify_qdisc_params(qdisc_output, expected_delay_ms, expected_jitter_ms, expected_loss_pct):
    """Parse tc qdisc show output and verify parameters. Returns (ok, details)."""
    m_netem = re.search(r'qdisc netem \S+.*', qdisc_output)
    if not m_netem:
        return False, f"no netem qdisc found in: {qdisc_output[:120]}"
    netem_str = m_netem.group(0)

    ok = True
    details = {}

    # Parse delay
    m_delay = re.search(r'delay\s+([\d.]+)ms(?:\s+([\d.]+)ms)?', netem_str)
    if m_delay:
        actual_delay = float(m_delay.group(1))
        actual_jitter = float(m_delay.group(2)) if m_delay.group(2) else 0
        # Allow 5% tolerance for kernel rounding
        if abs(actual_delay - expected_delay_ms) > max(0.5, expected_delay_ms * 0.05):
            ok = False
            details["delay"] = f"expected={expected_delay_ms}ms got={actual_delay}ms"
        if expected_jitter_ms > 0 and abs(actual_jitter - expected_jitter_ms) > max(0.5, expected_jitter_ms * 0.1):
            ok = False
            details["jitter"] = f"expected={expected_jitter_ms}ms got={actual_jitter}ms"
    elif expected_delay_ms > 0:
        ok = False
        details["delay"] = f"expected={expected_delay_ms}ms but not found in qdisc"

    # Parse loss
    m_loss = re.search(r'loss\s+([\d.]+)%', netem_str)
    if expected_loss_pct > 0:
        if m_loss:
            actual_loss = float(m_loss.group(1))
            if abs(actual_loss - expected_loss_pct) > 0.1:
                ok = False
                details["loss"] = f"expected={expected_loss_pct}% got={actual_loss}%"
        else:
            ok = False
            details["loss"] = f"expected={expected_loss_pct}% but not found in qdisc"

    details["qdisc"] = netem_str[:200]
    return ok, details


def measure_rtt(container_src, container_dst, count=5):
    """Measure RTT between two containers. Returns (ok, rtt_stats, raw_output)."""
    r = subprocess.run(
        ["docker", "exec", container_src, "ping", "-c", str(count), "-q", container_dst],
        capture_output=True, text=True, check=False, timeout=15,
    )
    raw = r.stdout.strip()
    if r.returncode != 0:
        return False, {}, raw
    # Parse avg rtt
    m = re.search(r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)', raw)
    if m:
        return True, {"min": float(m.group(1)), "avg": float(m.group(2)),
                       "max": float(m.group(3)), "mdev": float(m.group(4))}, raw
    return False, {}, raw


def clear_qdisc(container_name, iface):
    """Remove tc qdisc from interface. check=True."""
    subprocess.run(
        ["docker", "exec", container_name, "sh", "-c", f"tc qdisc del dev {iface} root 2>/dev/null; true"],
        capture_output=True, check=False, timeout=10,
    )
    # Verify
    r = subprocess.run(
        ["docker", "exec", container_name, "tc", "qdisc", "show", "dev", iface],
        capture_output=True, text=True, check=True, timeout=10,
    )
    qdisc_after = r.stdout.strip()
    if "netem" in qdisc_after:
        raise RuntimeError(f"Netem qdisc still present on {container_name} {iface} after cleanup: {qdisc_after}")
    return qdisc_after


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("pbft", "rgg"), required=True)
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--groups", type=int, default=4)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--delay-ms", type=float, default=5)
    parser.add_argument("--round-timeout", type=float, default=15)
    parser.add_argument("--view-timeout", type=float, default=1.0)
    parser.add_argument("--fault-mode", choices=("none", "crash", "delay", "equivocation"), default="none")
    parser.add_argument("--fault-scenario",
                        choices=("none", "f1", "f2", "f2l", "f3", "f4", "f5"), default="none")
    parser.add_argument("--fault-nodes", default="")
    parser.add_argument("--fault-delay-ms", type=float, default=100)
    parser.add_argument("--reputation-order", default="")
    parser.add_argument("--seed", type=int, default=20260705)
    parser.add_argument("--run-dir", type=pathlib.Path, required=True)
    parser.add_argument("--image", default="zte-rggpbft:v2")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--netem-delay", type=float, default=0)
    parser.add_argument("--netem-jitter", type=float, default=0)
    parser.add_argument("--netem-loss", type=float, default=0)
    args = parser.parse_args()
    if args.mode == "pbft" and args.fault_scenario in {"f2l", "f5"}:
        raise SystemExit(f"{args.fault_scenario} is only defined for RGG-PBFT")
    if args.mode == "rgg" and (
        args.groups < 4 or args.nodes % args.groups or args.nodes // args.groups < 4
    ):
        raise SystemExit(
            "RGG-PBFT requires at least four equal groups with at least four members each"
        )

    source_dir = pathlib.Path(__file__).resolve().parent
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "docker.log"
    config = vars(args).copy()
    config["run_dir"] = str(run_dir)
    config["started_at_unix"] = int(time.time())
    (run_dir / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")

    has_netem = args.netem_delay > 0 or args.netem_jitter > 0 or args.netem_loss > 0
    netem_evidence = {"has_netem": has_netem, "qdisc_before": [], "qdisc_applied": [],
                      "rtt_probe": {}, "timing": {}}
    cleanup_failures = []
    cleanup_barrier = run_dir / "qdisc_cleanup_complete"
    cleanup_barrier.unlink(missing_ok=True)

    # Phase 1: compose up collector + nodes (NO driver)
    compose_data = compose_definition(args, docker_cli_path(run_dir), include_driver=False)
    compose_path = run_dir / "compose.json"
    compose_path.write_text(json.dumps(compose_data, indent=2) + "\n")
    compose_cli_path = docker_cli_path(compose_path)
    project = "rgg" + hashlib.sha256(str(run_dir).encode()).hexdigest()[:10]

    try:
        if not args.skip_build:
            run_command(
                ["docker", "build", "-f", "Dockerfile.v2", "-t", args.image, "."],
                log_path, cwd=source_dir,
            )
        image_id = subprocess.check_output(
            ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True,
        ).strip()
        environment = {
            "host_platform": platform.platform(),
            "python": platform.python_version(),
            "container_image": args.image,
            "container_image_id": image_id,
            "source_sha256": {
                name: hashlib.sha256((source_dir / name).read_bytes()).hexdigest()
                for name in (
                    "run_v2.py",
                    "driver_v2.py",
                    "node_v2.py",
                    "fault_policy.py",
                    "protocol.py",
                    "grouping.py",
                )
            },
        }
        (run_dir / "environment.json").write_text(json.dumps(environment, indent=2, sort_keys=True) + "\n")

        # === Phase 1: Start collector + nodes (no driver) ===
        t0 = time.time()
        run_command(
            ["docker", "compose", "-p", project, "-f", compose_cli_path, "up", "-d", "--force-recreate"],
            log_path,
        )
        netem_evidence["timing"]["compose_up_at_unix"] = time.time()

        # === Phase 2: Ready barrier — wait for all containers running ===
        deadline = time.time() + 30
        last_count = 0
        events_path = run_dir / "events.jsonl"
        while time.time() < deadline:
            ready = ready_node_ids(events_path)
            if len(ready) == args.nodes:
                break
            if len(ready) != last_count:
                with log_path.open("a") as lf:
                    lf.write(f"READY_BARRIER: {len(ready)}/{args.nodes} node services ready\n")
                last_count = len(ready)
            time.sleep(0.2)
        ready = ready_node_ids(events_path)
        if len(ready) != args.nodes:
            raise RuntimeError(f"Ready barrier failed: {len(ready)}/{args.nodes} node READY events")
        netem_evidence["timing"]["ready_barrier_at_unix"] = time.time()
        netem_evidence["ready_node_ids"] = sorted(ready)

        # === Phase 3: Apply netem with verification ===
        if has_netem:
            netem_evidence["expected"] = {
                "delay_ms": args.netem_delay, "jitter_ms": args.netem_jitter,
                "loss_pct": args.netem_loss,
            }
            # Baseline RTT (before netem)
            baseline_rtt = measure_rtt(f"{project}-node0-1", f"{project}-node1-1")
            netem_evidence["rtt_probe"]["baseline"] = {
                "ok": baseline_rtt[0], "stats": baseline_rtt[1], "output": baseline_rtt[2],
            }

            for node_id in range(args.nodes):
                container = f"{project}-node{node_id}-1"
                # qdisc before
                r_before = subprocess.run(
                    ["docker", "exec", container, "tc", "qdisc", "show"],
                    capture_output=True, text=True, check=True, timeout=10,
                )
                netem_evidence["qdisc_before"].append({"node": node_id, "qdisc": r_before.stdout.strip()})

                # Discover interface
                iface = get_container_iface(container)
                # Apply and verify qdisc
                qdisc_output = apply_qdisc(container, iface, args.netem_delay, args.netem_jitter, args.netem_loss)
                param_ok, param_details = verify_qdisc_params(
                    qdisc_output, args.netem_delay, args.netem_jitter, args.netem_loss,
                )
                entry = {"node": node_id, "iface": iface, "qdisc": qdisc_output,
                         "params_verified": param_ok, "param_details": param_details}
                netem_evidence["qdisc_applied"].append(entry)
                if not param_ok:
                    raise RuntimeError(
                        f"qdisc parameter mismatch on node{node_id}: {param_details}"
                    )
                with log_path.open("a") as lf:
                    lf.write(f"NETEM_APPLIED node{node_id} iface={iface}: {qdisc_output}\n")

            netem_evidence["timing"]["netem_applied_at_unix"] = time.time()

            # === Phase 4: RTT gate ===
            rtt_after = measure_rtt(f"{project}-node0-1", f"{project}-node1-1")
            netem_evidence["rtt_probe"]["after_netem"] = {
                "ok": rtt_after[0], "stats": rtt_after[1], "output": rtt_after[2],
            }
            if not rtt_after[0]:
                raise RuntimeError(f"RTT gate failed: ping returned non-zero")
            # RTT gate: expected increase = 2 * one-way delay * container_hop_factor
            expected_increase = 2 * args.netem_delay
            actual_avg = rtt_after[1].get("avg", 0)
            baseline_avg = baseline_rtt[1].get("avg", 0) if baseline_rtt[0] else 1.0
            increase = actual_avg - baseline_avg
            tolerance = max(5.0, expected_increase * 0.3)
            if increase < expected_increase - tolerance:
                raise RuntimeError(
                    f"RTT gate failed: expected increase ~{expected_increase}ms, "
                    f"got {increase:.1f}ms (baseline={baseline_avg:.1f}ms, actual={actual_avg:.1f}ms)"
                )
            with log_path.open("a") as lf:
                lf.write(f"NETEM_RTT_GATE OK: baseline={baseline_avg:.1f}ms "
                         f"actual={actual_avg:.1f}ms increase={increase:.1f}ms\n")
            netem_evidence["rtt_probe"]["gate"] = {
                "passed": True, "expected_increase_ms": expected_increase,
                "actual_increase_ms": round(increase, 1),
                "baseline_avg_ms": round(baseline_avg, 1),
                "after_avg_ms": round(actual_avg, 1),
            }

        # === Phase 5: Start driver ===
        netem_evidence["timing"]["driver_start_at_unix"] = time.time()
        driver_cmd = ["python", "driver_v2.py"]
        driver_env = {k: str(v) for k, v in compose_data["services"]["collector"]["environment"].items()}
        # Network is auto-created by compose as <project>_default
        subprocess.run(
            ["docker", "run", "-d", "--name", f"{project}-driver-1",
             "--network", f"{project}_default",
             "-v", f"{docker_cli_path(run_dir)}:/results",
             ] + [f"-e{k}={v}" for k, v in driver_env.items()] +
            [args.image] + driver_cmd,
            capture_output=True, check=True, timeout=30,
        )
        with log_path.open("a") as lf:
            lf.write(f"DRIVER_STARTED: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")

        # === Phase 6: clear netem while nodes are still alive ===
        if has_netem:
            wait_for_event_type(
                events_path, "DRIVER_DONE",
                timeout=60 + args.rounds * (args.round_timeout + 1),
            )
            for node_id in range(args.nodes):
                container = f"{project}-node{node_id}-1"
                iface = get_container_iface(container)
                qdisc_after = clear_qdisc(container, iface)
                netem_evidence.setdefault("qdisc_after_cleanup", []).append(
                    {"node": node_id, "iface": iface, "qdisc": qdisc_after}
                )
            if not qdisc_cleanup_complete(netem_evidence["qdisc_after_cleanup"], args.nodes):
                raise RuntimeError("qdisc cleanup stop gate failed before node shutdown")
            netem_evidence["timing"]["qdisc_cleared_at_unix"] = time.time()
            cleanup_barrier.write_text("ok\n", encoding="ascii")

        # === Phase 7: Wait for collector ===
        run_command(
            ["docker", "wait", f"{project}-collector-1"],
            log_path,
            timeout=60 + args.rounds * (args.round_timeout + 1),
        )
        netem_evidence["timing"]["collector_done_at_unix"] = time.time()

        first_event = first_non_ready_event(events_path)
        if first_event:
            netem_evidence["timing"]["first_protocol_event"] = first_event
            netem_evidence["timing"]["first_protocol_event_at_unix"] = event_unix_seconds(first_event)

    finally:
        # === Cleanup: remove qdisc ===
        if has_netem and not qdisc_cleanup_complete(
            netem_evidence.get("qdisc_after_cleanup", []), args.nodes
        ):
            netem_evidence["qdisc_after_cleanup"] = []
            for node_id in range(args.nodes):
                container = f"{project}-node{node_id}-1"
                try:
                    iface = get_container_iface(container)
                    qdisc_after = clear_qdisc(container, iface)
                    netem_evidence["qdisc_after_cleanup"].append(
                        {"node": node_id, "iface": iface, "qdisc": qdisc_after}
                    )
                except Exception as e:
                    cleanup_failures.append({"node": node_id, "error": str(e)})
            netem_evidence["timing"]["qdisc_cleared_at_unix"] = time.time()
            if cleanup_failures:
                with log_path.open("a") as lf:
                    for cf in cleanup_failures:
                        lf.write(f"QDSK_CLEANUP_FAIL node{cf['node']}: {cf['error']}\n")
            elif qdisc_cleanup_complete(netem_evidence["qdisc_after_cleanup"], args.nodes):
                cleanup_barrier.write_text("ok\n", encoding="ascii")

        subprocess.run(
            ["docker", "compose", "-p", project, "-f", compose_cli_path, "down", "-v", "--remove-orphans"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        # Also remove driver container
        subprocess.run(
            ["docker", "rm", "-f", f"{project}-driver-1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    # Verify timing: all qdisc verified before driver started, driver before first event
    if has_netem:
        t_driver = netem_evidence["timing"].get("driver_start_at_unix", 0)
        t_netem = netem_evidence["timing"].get("netem_applied_at_unix", 0)
        first_event_s = netem_evidence["timing"].get("first_protocol_event_at_unix", 0)
        netem_evidence["timing_order"] = {
            "netem_before_driver": t_netem < t_driver,
            "driver_before_first_event": bool(first_event_s) and t_driver < first_event_s,
        }
        if not all(netem_evidence["timing_order"].values()):
            raise RuntimeError(f"Timing stop gate failed: {netem_evidence['timing_order']}")
        if cleanup_failures or not qdisc_cleanup_complete(
            netem_evidence.get("qdisc_after_cleanup", []), args.nodes
        ):
            raise RuntimeError(f"qdisc cleanup stop gate failed: {cleanup_failures}")
    (run_dir / "netem_evidence.json").write_text(json.dumps(netem_evidence, indent=2))

    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise SystemExit("collector did not produce summary.json")
    summary = json.loads(summary_path.read_text())
    if summary["safety_violation_events"] or summary["conflicting_commit_count"]:
        raise SystemExit("consensus safety violation detected")

    files = ["config.json", "environment.json", "compose.json", "matrix_entry.json",
             "events.jsonl", "summary.json", "docker.log", "netem_evidence.json"]
    with (run_dir / "checksums.sha256").open("w", encoding="ascii") as output:
        for name in files:
            fp = run_dir / name
            if fp.exists():
                digest = hashlib.sha256(fp.read_bytes()).hexdigest()
                output.write(f"{digest}  {name}\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
