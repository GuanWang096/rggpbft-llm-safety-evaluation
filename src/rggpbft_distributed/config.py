"""Configuration for RGG-PBFT distributed consensus experiment."""
import json

# Node configuration
M = 16           # total consensus nodes
K_g = 4          # groups (None = standard PBFT, no grouping)
PORT_BASE = 9000

# Network simulation
DELAY_MS = 5     # simulated per-hop delay (ms)
MSG_TIMEOUT = 30 # seconds

# Experiment
N_ROUNDS = 5     # consensus rounds per trial
SEED = 42

# PBFT threshold
F = (M - 1) // 3  # max Byzantine nodes tolerated

def get_host_port(node_id):
    return ("localhost", PORT_BASE + node_id)

def get_group(node_id, kg):
    """Round-robin group assignment."""
    if kg is None:
        return 0
    return node_id % kg

def get_leader_for_group(kg):
    """Leader = highest-reputation node in group; simplified to fixed assignment."""
    # In a real system, reputation would determine this.
    # For deterministic testing, node 0 is global primary, each group leader = lowest ID in group.
    leaders = {}
    if kg is None:
        leaders[0] = 0  # only one group
    else:
        for g in range(kg):
            candidates = [i for i in range(M) if i % kg == g]
            leaders[g] = min(candidates)
    return leaders

def save_config(kg, path="experiment_config.json"):
    cfg = {"M": M, "K_g": kg, "F": F, "delay_ms": DELAY_MS,
           "n_rounds": N_ROUNDS, "port_base": PORT_BASE}
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg
