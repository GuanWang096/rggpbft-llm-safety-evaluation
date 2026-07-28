import hashlib
import json
import os
import socket
import time

from network_utils import resolve_host


M = int(os.environ.get("M", "16"))
N_ROUNDS = int(os.environ.get("N_ROUNDS", "10"))
NETEM_CLEANUP_BARRIER_PATH = os.environ.get("NETEM_CLEANUP_BARRIER_PATH", "")
PORT = int(os.environ.get("PORT", "9000"))
ROUND_TIMEOUT = float(os.environ.get("ROUND_TIMEOUT", "15"))
COLLECTOR_HOST = os.environ.get("COLLECTOR_HOST", "collector")
COLLECTOR_PORT = int(os.environ.get("COLLECTOR_PORT", "9999"))
REPUTATION_ORDER = [int(value) for value in os.environ.get("REPUTATION_ORDER", ",".join(map(str, range(M)))).split(",")]
PRIMARY = REPUTATION_ORDER[0]
FAULT_SCENARIO = os.environ.get("FAULT_SCENARIO", "none").lower()
DIGESTS_PATH = os.environ.get("DIGESTS_PATH", "")


def load_round_inputs():
    if not DIGESTS_PATH or not os.path.exists(DIGESTS_PATH):
        return [
            {
                "decision_id": f"synthetic-round-{sequence}",
                "digest": hashlib.sha256(
                    f"rggpbft-round-{sequence}".encode("ascii")
                ).hexdigest(),
            }
            for sequence in range(N_ROUNDS)
        ]
    with open(DIGESTS_PATH, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    entries = payload.get("entries", payload)
    if not isinstance(entries, list) or len(entries) != N_ROUNDS:
        raise ValueError(
            f"digest input count {len(entries) if isinstance(entries, list) else 'invalid'} "
            f"does not match N_ROUNDS={N_ROUNDS}"
        )
    normalized = []
    for sequence, entry in enumerate(entries):
        digest = str(entry["digest"]).lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(f"invalid digest at sequence {sequence}")
        normalized.append(
            {
                "decision_id": str(entry["decision_id"]),
                "digest": digest,
            }
        )
    return normalized


ROUND_INPUTS = load_round_inputs()


def required_commit_count(fault_scenario, node_count):
    return int(node_count) if str(fault_scenario).lower() == "none" else 1


def completion_latency_fields(
    start_ns,
    first_commit_ns,
    required_commit_ns,
    ended_ns,
):
    first_latency_ms = (
        (first_commit_ns or ended_ns) - start_ns
    ) / 1_000_000
    required_latency_ms = (
        (required_commit_ns or ended_ns) - start_ns
    ) / 1_000_000
    return {
        "latency_ms": required_latency_ms,
        "first_commit_latency_ms": first_latency_ms,
        "required_commit_latency_ms": required_latency_ms,
    }


def emit(event_type, data, attempts=10):
    event = {"type": event_type, "node": -1, "time_ns": time.time_ns(), "data": data}
    encoded = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
    last_error = None
    for attempt in range(attempts):
        try:
            with socket.create_connection((resolve_host(COLLECTOR_HOST), COLLECTOR_PORT), timeout=3) as connection:
                connection.sendall(encoded)
            return
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(0.1 * (2**attempt), 1.0))
    raise last_error


def wait_for_nodes():
    deadline = time.monotonic() + 30
    pending = set(range(M))
    while pending and time.monotonic() < deadline:
        for node_id in list(pending):
            try:
                with socket.create_connection((resolve_host(f"node{node_id}"), PORT), timeout=0.2) as connection:
                    connection.sendall(b'{"control":"PING"}\n')
                    if json.loads(connection.recv(1024).decode("utf-8")).get("control") == "PONG":
                        pending.remove(node_id)
            except OSError:
                pass
        time.sleep(0.1)
    if pending:
        raise RuntimeError(f"nodes not reachable: {sorted(pending)}")


def announce_request(request):
    encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
    failures = []
    for node_id in range(M):
        try:
            with socket.create_connection(
                (resolve_host(f"node{node_id}"), PORT), timeout=3
            ) as connection:
                connection.sendall(encoded)
        except OSError as exc:
            failures.append((node_id, repr(exc)))
    if failures:
        raise RuntimeError(f"request announcement failed: {failures}")


def query_commit(node_id, sequence, expected_digest):
    request = {
        "control": "STATUS",
        "sequence": int(sequence),
    }
    encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        with socket.create_connection(
            (resolve_host(f"node{node_id}"), PORT), timeout=1
        ) as connection:
            connection.sendall(encoded)
            reply = json.loads(connection.recv(4096).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return reply.get("committed") is True and reply.get("digest") == expected_digest


def run_round(sequence):
    round_input = ROUND_INPUTS[sequence]
    decision_id = round_input["decision_id"]
    digest = round_input["digest"]
    start_ns = time.time_ns()
    request = {
        "type": "REQUEST",
        "sequence": sequence,
        "digest": digest,
        "start_ns": start_ns,
    }
    try:
        announce_request(request)
        deadline = time.monotonic() + ROUND_TIMEOUT
        required = required_commit_count(FAULT_SCENARIO, M)
        committed_nodes = set()
        first_commit_ns = None
        required_commit_ns = None
        while time.monotonic() < deadline and len(committed_nodes) < required:
            for node_id in range(M):
                if node_id in committed_nodes:
                    continue
                if query_commit(node_id, sequence, digest):
                    committed_nodes.add(node_id)
                    if first_commit_ns is None:
                        first_commit_ns = time.time_ns()
                    if (
                        required_commit_ns is None
                        and len(committed_nodes) >= required
                    ):
                        required_commit_ns = time.time_ns()
            if len(committed_nodes) < required:
                time.sleep(0.02)
        success = len(committed_nodes) >= required
        error = "" if success else "round timeout before a matching commit"
    except Exception as exc:
        success = False
        error = repr(exc)
        committed_nodes = set()
        required = required_commit_count(FAULT_SCENARIO, M)
        first_commit_ns = None
        required_commit_ns = None
    ended_ns = time.time_ns()
    latency_fields = completion_latency_fields(
        start_ns,
        first_commit_ns,
        required_commit_ns,
        ended_ns,
    )
    emit(
        "DRIVER_RESULT",
        {
            "sequence": sequence,
            "decision_id": decision_id,
            "digest": digest,
            "success": success,
            "error": error,
            **latency_fields,
            "observed_commit_count": len(committed_nodes),
            "required_commit_count": required,
        },
    )
    return success


def stop_nodes():
    message = (json.dumps({"control": "STOP"}) + "\n").encode("utf-8")
    for node_id in range(M):
        for attempt in range(5):
            try:
                with socket.create_connection(
                    (resolve_host(f"node{node_id}"), PORT), timeout=1
                ) as connection:
                    connection.sendall(message)
                break
            except OSError:
                if attempt < 4:
                    time.sleep(0.1 * (attempt + 1))


def main():
    results = []
    fatal_error = ""
    try:
        wait_for_nodes()
        for sequence in range(N_ROUNDS):
            results.append(run_round(sequence))
            time.sleep(0.05)
    except Exception as exc:
        fatal_error = repr(exc)
    finally:
        time.sleep(1)
        if NETEM_CLEANUP_BARRIER_PATH:
            emit("DRIVER_DONE", {"rounds": N_ROUNDS, "successful": sum(results)})
            deadline = time.monotonic() + 90
            while not os.path.exists(NETEM_CLEANUP_BARRIER_PATH):
                if time.monotonic() >= deadline:
                    fatal_error = fatal_error or "timed out waiting for qdisc cleanup barrier"
                    break
                time.sleep(0.1)
        stop_nodes()
        time.sleep(0.5)
        emit(
            "FINISH",
            {"rounds": N_ROUNDS, "successful": sum(results), "fatal_error": fatal_error},
        )
    print(f"Driver complete: {sum(results)}/{N_ROUNDS} rounds committed", flush=True)
    if fatal_error:
        raise RuntimeError(fatal_error)


if __name__ == "__main__":
    main()
