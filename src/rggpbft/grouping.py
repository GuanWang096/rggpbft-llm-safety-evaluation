"""Deterministic sequential round-robin reputation grouping.

Centralizes group-map derivation so every module uses the same rules.
"""

import hashlib
import struct


def generate_reputation_order(m, seed_base=20260705):
    """Return a deterministic non-identity permutation of 0..m-1.

    score = big_endian_uint64(SHA256("reputation-v1|<M>|<seed_base>|<node_id>")[0:8])
    Nodes are sorted by (-score, node_id).
    """
    scores = {}
    for node_id in range(m):
        material = f"reputation-v1|{m}|{seed_base}|{node_id}".encode()
        raw = hashlib.sha256(material).digest()[:8]
        scores[node_id] = struct.unpack(">Q", raw)[0]
    return sorted(range(m), key=lambda n: (-scores[n], n))


def validate_reputation_order(reputation_order, m):
    """Raise ValueError if *reputation_order* is not a permutation of 0..m-1."""
    if len(reputation_order) != m:
        raise ValueError(f"reputation_order length {len(reputation_order)} != M={m}")
    if set(reputation_order) != set(range(m)):
        missing = set(range(m)) - set(reputation_order)
        extra = set(reputation_order) - set(range(m))
        parts = []
        if missing:
            parts.append(f"missing {sorted(missing)}")
        if extra:
            parts.append(f"extra {sorted(extra)}")
        raise ValueError("reputation_order is not a permutation: " + ", ".join(parts))
    if len(set(reputation_order)) != m:
        raise ValueError("reputation_order contains duplicates")


def build_group_map(reputation_order, k_g):
    """Return (group_map, group_leaders, l_gl).

    *group_map*:  dict[node_id -> group_id]
    *group_leaders*: dict[group_id -> leader_node_id]
    *l_gl*:          tuple of group leaders in reputation order
    """
    m = len(reputation_order)
    if m % k_g != 0:
        raise ValueError(f"M={m} is not divisible by K_g={k_g}")
    validate_reputation_order(reputation_order, m)

    group_map = {}
    groups = {g: [] for g in range(k_g)}
    for pos, node_id in enumerate(reputation_order):
        g = pos % k_g
        group_map[node_id] = g
        groups[g].append(node_id)

    group_leaders = {}
    for g in range(k_g):
        group_leaders[g] = groups[g][0]

    l_gl = tuple(
        reputation_order[pos]
        for pos, node_id in enumerate(reputation_order)
        if node_id in set(group_leaders.values())
    )

    leader_set_expected = set(group_leaders.values())
    assert len(l_gl) == k_g, f"L_GL length {len(l_gl)} != K_g={k_g}"
    assert set(l_gl) == leader_set_expected, "L_GL members != group leaders"
    assert all(
        pos == reputation_order.index(leader)
        for pos, leader in enumerate(l_gl)
    ), "L_GL order does not match reputation order"

    return group_map, group_leaders, l_gl
