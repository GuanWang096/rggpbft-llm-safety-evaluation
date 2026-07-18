"""
Collector: receives timing events from all consensus nodes,
aggregates results, and outputs summary statistics.
"""
import socket
import json
import time
import sys
import os

COLLECTOR_PORT = 9999
N_ROUNDS = int(os.environ.get("N_ROUNDS", "5"))
M = int(os.environ.get("M", "16"))
events = []

def run_collector(timeout=20):
    host, port = "0.0.0.0", COLLECTOR_PORT
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(200)
    server.settimeout(timeout)

    print(f"Collector listening on port {COLLECTOR_PORT}, timeout={timeout}s", flush=True)
    start = time.time()
    expected = M * N_ROUNDS  # each node commits each round
    while time.time() - start < timeout:
        try:
            conn, addr = server.accept()
            data = conn.recv(65536).decode()
            evt = json.loads(data)
            events.append(evt)
            conn.close()
            # Early exit: enough DONE events
            done_count = sum(1 for e in events if e["type"] == "DONE")
            if done_count >= expected:
                print(f"  All {expected} commits received, exiting early.", flush=True)
                break
        except socket.timeout:
            break
        except:
            break

    # Analyze
    committed = [e for e in events if e["type"] == "DONE"]
    if not committed:
        print("No committed events collected.")
        return

    pbft_lat = [e["data"]["latency_ms"] for e in committed if e["data"].get("mode") == "PBFT"]
    rgg_lat  = [e["data"]["latency_ms"] for e in committed if e["data"].get("mode") == "RGG-PBFT"]

    import statistics
    print(f"\n{'='*55}")
    print(f"Distributed Consensus Experiment Results")
    print(f"{'='*55}")
    print(f"Total events: {len(events)}")
    print(f"Committed rounds: {len(committed)}")
    print(f"Expected: {N_ROUNDS} rounds x {M} nodes = {N_ROUNDS * M} commits")
    print(f"Completeness: {len(committed)}/{N_ROUNDS * M} ({100*len(committed)/(N_ROUNDS*M):.1f}%)")

    def safe_stats(vals, label):
        if not vals:
            print(f"\n{label}: no data")
            return
        mean_v = statistics.mean(vals)
        if len(vals) >= 2:
            std_v = statistics.stdev(vals)
            print(f"\n{label}: {len(vals)} commits, {mean_v:.1f} ± {std_v:.1f} ms")
        else:
            print(f"\n{label}: {len(vals)} commits, {mean_v:.1f} ms")

    safe_stats(pbft_lat, "PBFT")
    safe_stats(rgg_lat, "RGG-PBFT")

    if pbft_lat and rgg_lat:
        reduction = (1 - statistics.mean(rgg_lat) / statistics.mean(pbft_lat)) * 100
        print(f"\nRGG-PBFT latency reduction vs PBFT: {reduction:.1f}%")

if __name__ == "__main__":
    run_collector()
