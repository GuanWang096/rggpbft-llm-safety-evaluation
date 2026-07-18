import json
import os
import socket
import statistics
import time


PORT = int(os.environ.get("COLLECTOR_PORT", "9999"))
M = int(os.environ.get("M", "16"))
N_ROUNDS = int(os.environ.get("N_ROUNDS", "10"))
EVENTS_PATH = os.environ.get("EVENTS_PATH", "/results/events.jsonl")
SUMMARY_PATH = os.environ.get("SUMMARY_PATH", "/results/summary.json")
GRACE_SECONDS = float(os.environ.get("GRACE_SECONDS", "45"))


def collection_complete(events, node_count):
    finish_received = any(event["type"] == "FINISH" for event in events)
    stopped_nodes = {
        event.get("node") for event in events if event["type"] == "STOPPED"
    }
    return finish_received and len(stopped_nodes) >= int(node_count)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int(fraction * (len(ordered) - 1))] if ordered else None


def summarize(events):
    raw_commits = [event for event in events if event["type"] == "COMMIT"]
    unique_commits = {}
    for event in raw_commits:
        key = (event.get("node", -1), event["data"]["sequence"])
        unique_commits.setdefault(key, event)
    commits = list(unique_commits.values())
    driver_results = [event for event in events if event["type"] == "DRIVER_RESULT"]
    successful = [event for event in driver_results if event["data"]["success"]]
    stopped = [event for event in events if event["type"] == "STOPPED"]
    client_latencies = [event["data"]["latency_ms"] for event in successful]
    node_latencies = [event["data"]["latency_ms"] for event in commits]
    committed_digests = {}
    conflicting_commits = []
    for event in raw_commits:
        sequence = event["data"]["sequence"]
        digest = event["data"]["digest"]
        previous = committed_digests.setdefault(sequence, digest)
        if previous != digest:
            conflicting_commits.append({"sequence": sequence, "first": previous, "second": digest})
    first_view_change = {}
    first_commit = {}
    for event in events:
        sequence = event.get("data", {}).get("sequence")
        event_time = event.get("time_ns")
        if sequence is None or event_time is None:
            continue
        if event["type"] == "VIEW_CHANGE_SENT":
            first_view_change[sequence] = min(
                first_view_change.get(sequence, event_time), event_time
            )
        elif event["type"] == "COMMIT":
            first_commit[sequence] = min(
                first_commit.get(sequence, event_time), event_time
            )
    recovery_latencies = [
        (first_commit[sequence] - started) / 1_000_000
        for sequence, started in first_view_change.items()
        if sequence in first_commit and first_commit[sequence] >= started
    ]
    accepted_views = [
        event["data"]["view"]
        for event in events
        if event["type"] == "NEW_VIEW_ACCEPTED"
    ]
    return {
        "node_count": M,
        "round_count": N_ROUNDS,
        "driver_success_count": len(successful),
        "driver_failure_count": len(driver_results) - len(successful),
        "driver_success_rate": len(successful) / N_ROUNDS,
        "client_latency_ms": {
            "mean": statistics.fmean(client_latencies) if client_latencies else None,
            "p50": percentile(client_latencies, 0.50),
            "p95": percentile(client_latencies, 0.95),
            "p99": percentile(client_latencies, 0.99),
        },
        "node_commit_count": len(commits),
        "duplicate_commit_events": len(raw_commits) - len(commits),
        "reported_protocol_messages_sent": sum(
            event["data"].get("messages_sent", 0) for event in commits
        ),
        "reported_protocol_bytes_sent": sum(
            event["data"].get("bytes_sent", 0) for event in commits
        ),
        "final_protocol_messages_sent": sum(
            event["data"].get("messages_sent", 0) for event in stopped
        ),
        "final_protocol_bytes_sent": sum(
            event["data"].get("bytes_sent", 0) for event in stopped
        ),
        "node_commit_completeness": len(commits) / (M * N_ROUNDS),
        "node_latency_ms": {
            "mean": statistics.fmean(node_latencies) if node_latencies else None,
            "p50": percentile(node_latencies, 0.50),
            "p95": percentile(node_latencies, 0.95),
            "p99": percentile(node_latencies, 0.99),
        },
        "invalid_signature_events": sum(event["type"] == "INVALID_SIGNATURE" for event in events),
        "fault_injected_events": sum(event["type"] == "FAULT_INJECTED" for event in events),
        "invalid_view_change_events": sum(event["type"] == "INVALID_VIEW_CHANGE" for event in events),
        "equivocation_sent_events": sum(event["type"] == "EQUIVOCATION_SENT" for event in events),
        "equivocation_observed_events": sum(event["type"] == "EQUIVOCATION_OBSERVED" for event in events),
        "view_change_sent_events": sum(event["type"] == "VIEW_CHANGE_SENT" for event in events),
        "new_view_sent_events": sum(event["type"] == "NEW_VIEW_SENT" for event in events),
        "new_view_accepted_events": len(accepted_views),
        "invalid_new_view_events": sum(event["type"] == "INVALID_NEW_VIEW" for event in events),
        "stale_new_view_events": sum(
            event["type"] == "STALE_NEW_VIEW_IGNORED" for event in events
        ),
        "max_accepted_view": max(accepted_views, default=0),
        "recovered_sequence_count": len(recovery_latencies),
        "recovery_latency_ms": {
            "mean": statistics.fmean(recovery_latencies) if recovery_latencies else None,
            "p50": percentile(recovery_latencies, 0.50),
            "p95": percentile(recovery_latencies, 0.95),
            "p99": percentile(recovery_latencies, 0.99),
        },
        "node_error_events": sum(event["type"] == "NODE_ERROR" for event in events),
        "safety_violation_events": sum(event["type"] == "SAFETY_VIOLATION" for event in events),
        "conflicting_commit_count": len(conflicting_commits),
        "conflicting_commits": conflicting_commits,
    }


def main():
    os.makedirs(os.path.dirname(EVENTS_PATH), exist_ok=True)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PORT))
    server.listen(512)
    server.settimeout(0.5)
    events = []
    finish_deadline = None
    with open(EVENTS_PATH, "w", encoding="utf-8") as output:
        while finish_deadline is None or time.monotonic() < finish_deadline:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                data = b""
                while True:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in chunk:
                        break
            try:
                event = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            events.append(event)
            output.write(json.dumps(event, sort_keys=True) + "\n")
            output.flush()
            if event["type"] == "FINISH":
                finish_deadline = time.monotonic() + GRACE_SECONDS
            if collection_complete(events, M):
                break
    server.close()
    summary = summarize(events)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as output:
        json.dump(summary, output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
