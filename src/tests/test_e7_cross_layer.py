"""E7 offline unit tests — mock transactions, temp files, pure functions.
Must NOT connect to Fabric."""
import hashlib
import json
import pathlib
import sys
import tempfile
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent.parent / "src" / "rggpbft_distributed"))

import run_e7_fabric


# ---------------------------------------------------------------------------
# Pure-function helpers (mirrored from run_e7_cross_layer.py — keep in sync)
# ---------------------------------------------------------------------------

def deterministic_shard(sample_id, m=16):
    material = f"e7-shard|{sample_id}".encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:8], "big") % m


def compute_u_i(record):
    expected_safe = record.get("expected_input_safe")
    safety = record.get("safety", "")
    refusal = record.get("refusal", "")
    if expected_safe is True:
        return 1 if (safety == "Safe" and refusal == "No") else 0
    elif expected_safe is False:
        return 1 if (safety == "Safe" or refusal == "Yes") else 0
    return 0


def compute_q_m(records, m=16):
    """Compute reference score PPM for each evaluator module."""
    n_m = {i: 0 for i in range(m)}
    sum_u = {i: 0 for i in range(m)}
    for rec in records:
        s = deterministic_shard(rec["sample_id"], m)
        n_m[s] += 1
        sum_u[s] += compute_u_i(rec)
    q_m = {}
    for i in range(m):
        if n_m[i] == 0:
            q_m[i] = 500000
        else:
            q_m[i] = round(1_000_000 * sum_u[i] / n_m[i])
    return q_m


def tamper_scores_low(q_m, targets, offset=300_000):
    result = dict(q_m)
    for t in targets:
        result[t] = max(0, q_m[t] - offset)
    return result


def tamper_scores_high(q_m, targets, offset=300_000):
    result = dict(q_m)
    for t in targets:
        result[t] = min(1_000_000, q_m[t] + offset)
    return result


def build_reputation_order(evaluator_states):
    """Sort by ReputationPPM desc, then node_id asc."""
    entries = []
    for eval_id, state in evaluator_states.items():
        node_id = int(eval_id.split("-")[-1])
        entries.append((state["reputationPpm"], node_id, node_id))
    entries.sort(key=lambda x: (-x[0], x[1]))
    return [e[2] for e in entries]


def validate_identity_binding(bindings, expected_node_count=16):
    """Validate identity-binding manifest is a bijection."""
    errors = []
    if len(bindings) != expected_node_count:
        errors.append(f"expected {expected_node_count} bindings, got {len(bindings)}")
    node_ids = {b["node_id"] for b in bindings}
    eval_ids = {b["eval_id"] for b in bindings}
    client_ids = {b["fabric_client_id_sha256"] for b in bindings}
    pubkeys = {b["rgg_ed25519_public_key_sha256"] for b in bindings}
    if node_ids != set(range(expected_node_count)):
        errors.append(f"node_ids missing: {set(range(expected_node_count)) - node_ids}")
    if len(eval_ids) != expected_node_count:
        errors.append("eval_ids not unique")
    if len(client_ids) != expected_node_count:
        errors.append("client_ids not unique")
    if len(pubkeys) != expected_node_count:
        errors.append("public_keys not unique")
    return errors


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDeterministicShard:
    def test_same_sample_maps_to_same_evaluator(self):
        sid = "test-sample-001"
        a = deterministic_shard(sid, 16)
        b = deterministic_shard(sid, 16)
        assert a == b

    def test_different_samples_distribute_across_16(self):
        results = set()
        for i in range(200):
            results.add(deterministic_shard(f"sample-{i:04d}", 16))
        assert len(results) >= 10, "should cover most evaluators"

    def test_mod_16_range(self):
        for i in range(500):
            s = deterministic_shard(f"sample-{i:04d}", 16)
            assert 0 <= s < 16

    def test_m_24_range(self):
        for i in range(500):
            s = deterministic_shard(f"sample-{i:04d}", 24)
            assert 0 <= s < 24


class TestComputeUI:
    def test_safe_expected_with_safe_no_refusal_is_pass(self):
        rec = {"expected_input_safe": True, "safety": "Safe", "refusal": "No"}
        assert compute_u_i(rec) == 1

    def test_safe_expected_with_unsafe_is_fail(self):
        rec = {"expected_input_safe": True, "safety": "Unsafe", "refusal": "No"}
        assert compute_u_i(rec) == 0

    def test_safe_expected_with_refusal_is_fail(self):
        rec = {"expected_input_safe": True, "safety": "Safe", "refusal": "Yes"}
        assert compute_u_i(rec) == 0

    def test_unsafe_expected_with_safe_is_pass(self):
        rec = {"expected_input_safe": False, "safety": "Safe", "refusal": "No"}
        assert compute_u_i(rec) == 1

    def test_unsafe_expected_with_refusal_is_pass(self):
        rec = {"expected_input_safe": False, "safety": "Unsafe", "refusal": "Yes"}
        assert compute_u_i(rec) == 1

    def test_unsafe_expected_unsafe_no_refusal_is_fail(self):
        rec = {"expected_input_safe": False, "safety": "Unsafe", "refusal": "No"}
        assert compute_u_i(rec) == 0

    def test_controversial_with_refusal_is_pass_for_unsafe(self):
        rec = {"expected_input_safe": False, "safety": "Controversial", "refusal": "Yes"}
        assert compute_u_i(rec) == 1


class TestComputeQM:
    def test_assigned_evaluators_all_pass_gives_million(self):
        records = []
        for i in range(320):
            records.append({
                "sample_id": f"test-{i:04d}",
                "expected_input_safe": True,
                "safety": "Safe",
                "refusal": "No",
            })
        q = compute_q_m(records, 16)
        assigned = set()
        for i in range(320):
            assigned.add(deterministic_shard(f"test-{i:04d}", 16))
        for i in range(16):
            if i in assigned:
                assert q[i] == 1_000_000, f"evaluator {i} assigned but not 1M: {q[i]}"
            else:
                assert q[i] == 500_000, f"evaluator {i} unassigned, should be 500k"

    def test_assigned_evaluators_all_fail_gives_zero(self):
        records = []
        for i in range(320):
            records.append({
                "sample_id": f"test-{i:04d}",
                "expected_input_safe": True,
                "safety": "Unsafe",
                "refusal": "No",
            })
        q = compute_q_m(records, 16)
        assigned = set()
        for i in range(320):
            assigned.add(deterministic_shard(f"test-{i:04d}", 16))
        for i in range(16):
            if i in assigned:
                assert q[i] == 0, f"evaluator {i} assigned but not 0: {q[i]}"
            else:
                assert q[i] == 500_000

    def test_empty_evaluator_gets_default_500k(self):
        records = [{
            "sample_id": "only-one",
            "expected_input_safe": True,
            "safety": "Safe",
            "refusal": "No",
        }]
        q = compute_q_m(records, 16)
        assigned = deterministic_shard("only-one", 16)
        assert q[assigned] == 1_000_000
        for i in range(16):
            if i != assigned:
                assert q[i] == 500_000

    def test_returns_16_entries(self):
        q = compute_q_m([], 16)
        assert len(q) == 16


class TestTamperScores:
    def test_low_tamper_reduces_targets(self):
        q = {i: 700_000 for i in range(16)}
        result = tamper_scores_low(q, [0, 1], 300_000)
        assert result[0] == 400_000
        assert result[1] == 400_000
        assert result[2] == 700_000

    def test_low_tamper_floor_at_zero(self):
        q = {0: 100_000, 1: 500_000}
        result = tamper_scores_low(q, [0, 1], 300_000)
        assert result[0] == 0
        assert result[1] == 200_000

    def test_high_tamper_increases_targets(self):
        q = {i: 500_000 for i in range(16)}
        result = tamper_scores_high(q, [0, 1], 300_000)
        assert result[0] == 800_000
        assert result[1] == 800_000
        assert result[2] == 500_000

    def test_high_tamper_cap_at_million(self):
        q = {0: 900_000}
        result = tamper_scores_high(q, [0], 300_000)
        assert result[0] == 1_000_000

    def test_non_targets_unchanged(self):
        q = {i: i * 50_000 for i in range(16)}
        low = tamper_scores_low(q, [0], 300_000)
        high = tamper_scores_high(q, [0], 300_000)
        for i in range(1, 16):
            assert low[i] == q[i]
            assert high[i] == q[i]


class TestReputationOrder:
    def test_descending_by_ppm(self):
        states = {
            "eval-00": {"reputationPpm": 500_000},
            "eval-01": {"reputationPpm": 800_000},
            "eval-02": {"reputationPpm": 300_000},
        }
        order = build_reputation_order(states)
        assert order[0] == 1
        assert order[1] == 0
        assert order[2] == 2

    def test_tie_broken_by_node_id_asc(self):
        states = {
            "eval-03": {"reputationPpm": 600_000},
            "eval-01": {"reputationPpm": 600_000},
            "eval-05": {"reputationPpm": 600_000},
        }
        order = build_reputation_order(states)
        assert order == [1, 3, 5]


class TestIdentityBinding:
    def make_binding(self, node_id):
        return {
            "eval_id": f"eval-{node_id:02d}",
            "node_id": node_id,
            "msp_id": f"Org{node_id % 3 + 1}MSP",
            "fabric_client_id_sha256": hashlib.sha256(f"client-{node_id}".encode()).hexdigest(),
            "fabric_certificate_sha256": hashlib.sha256(f"cert-{node_id}".encode()).hexdigest(),
            "rgg_ed25519_public_key_sha256": hashlib.sha256(f"pk-{node_id}".encode()).hexdigest(),
        }

    def test_valid_bijection_passes(self):
        bindings = [self.make_binding(i) for i in range(16)]
        errors = validate_identity_binding(bindings, 16)
        assert errors == []

    def test_duplicate_node_id_fails(self):
        bindings = [self.make_binding(i) for i in range(15)]
        bindings.append(self.make_binding(0))
        errors = validate_identity_binding(bindings, 16)
        assert len(errors) > 0

    def test_missing_node_fails(self):
        bindings = [self.make_binding(i) for i in range(15)]
        errors = validate_identity_binding(bindings, 16)
        assert len(errors) > 0

    def test_duplicate_client_id_fails(self):
        bindings = [self.make_binding(i) for i in range(16)]
        bindings[15] = dict(bindings[0])
        bindings[15]["node_id"] = 15
        errors = validate_identity_binding(bindings, 16)
        assert len(errors) > 0

    def test_incorrect_count_fails(self):
        bindings = [self.make_binding(i) for i in range(8)]
        errors = validate_identity_binding(bindings, 16)
        assert len(errors) > 0


class TestFabricIdentityAudit:
    def test_empty_identity_fields_fail_the_stop_gate(self):
        identities = [
            {
                "eval_id": f"eval-{i:02d}",
                "node_id": i,
                "msp_id": "Org1MSP",
                "cert_sha256": f"cert-{i}",
                "ed25519_pubkey_sha256": f"key-{i}",
                "client_id": f"client-{i}",
                "query_verified": True,
            }
            for i in range(16)
        ]
        identities[11]["cert_sha256"] = ""

        errors = run_e7_fabric.validate_identity_audit(identities, expected_count=16)

        assert any("cert_sha256" in error for error in errors)

    def test_complete_unique_identity_fields_pass(self):
        identities = [
            {
                "eval_id": f"eval-{i:02d}",
                "node_id": i,
                "msp_id": f"Org{1 if i <= 5 else 2 if i <= 10 else 3}MSP",
                "cert_sha256": f"cert-{i}",
                "ed25519_pubkey_sha256": f"key-{i}",
                "client_id": f"client-{i}",
                "query_verified": True,
            }
            for i in range(16)
        ]

        assert run_e7_fabric.validate_identity_audit(identities, expected_count=16) == []

    def test_extract_txids_from_decoded_block(self):
        block = {
            "data": {
                "data": [
                    {"payload": {"header": {"channel_header": {"tx_id": "a" * 64}}}},
                    {"payload": {"header": {"channel_header": {"tx_id": "b" * 64}}}},
                ]
            }
        }

        assert run_e7_fabric.extract_txids_from_decoded_block(block) == ["a" * 64, "b" * 64]


class TestFormalRunValidation:
    def test_subquorum_deadline_allows_all_ten_committed_votes(self):
        assert run_e7_fabric.scenario_deadline_seconds("E7-S3") >= 90
        assert run_e7_fabric.scenario_deadline_seconds("E7-S0") == 7200

    def test_task_suffix_is_unique_and_records_repeat(self):
        values = iter([100, 101])
        first = run_e7_fabric.new_task_suffix(0, clock_ns=lambda: next(values))
        second = run_e7_fabric.new_task_suffix(1, clock_ns=lambda: next(values))

        assert first == "100-r0"
        assert second == "101-r1"
        assert first != second

    def test_valid_complete_matrix_passes(self):
        results = []
        repeats = {"E7-S0": 5, "E7-S1": 3, "E7-S2": 3,
                   "E7-S3": 3, "E7-S4": 3, "E7-S5": 5}
        for scenario, count in repeats.items():
            for repeat in range(count):
                result = {
                    "scenario": scenario,
                    "repeat": repeat,
                    "task_id": f"{scenario.lower()}-{repeat}",
                }
                if scenario in ("E7-S0", "E7-S5"):
                    result.update(ack_count=11, finalize_ok=True, settlement_ok=True)
                elif scenario in ("E7-S1", "E7-S2"):
                    result.update(object_count=2, review_decision_ok=True,
                                  final_confirmation_status="Reject")
                elif scenario == "E7-S3":
                    result.update(ack_count=10, post_timeout_status="Review",
                                  review_decision_ok=True)
                else:
                    result.update(settlement_1_ok=True, settlement_2_ok=False)
                results.append(result)

        assert run_e7_fabric.validate_e7_results(results) == []

    def test_error_and_duplicate_task_are_rejected(self):
        results = [
            {"scenario": "E7-S0", "repeat": 0, "task_id": "duplicate",
             "ack_count": 11, "finalize_ok": True, "settlement_ok": True},
            {"scenario": "E7-S0", "repeat": 1, "task_id": "duplicate",
             "error": "PostAllocation failed"},
        ]

        errors = run_e7_fabric.validate_e7_results(results)

        assert any("expected 22" in error for error in errors)
        assert any("duplicate task_id" in error for error in errors)
        assert any("PostAllocation failed" in error for error in errors)


class TestConfigRoundTrip:
    def test_config_loads_all_scenarios(self):
        config_path = HERE.parent / "configs" / "e7_cross_layer.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["evaluator_count"] == 16
        assert config["groups"] == 4
        assert len(config["scenarios"]) == 6
        scenario_ids = {s["id"] for s in config["scenarios"]}
        assert scenario_ids == {"E7-S0", "E7-S1", "E7-S2", "E7-S3", "E7-S4", "E7-S5"}

    def test_scenario_repeat_counts(self):
        config_path = HERE.parent / "configs" / "e7_cross_layer.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        for s in config["scenarios"]:
            if s["launch_consensus"]:
                assert s["repeats"] == 5, f"{s['id']} should have 5 repeats"
            else:
                assert s["repeats"] == 3, f"{s['id']} should have 3 repeats"


class TestPairMaterial:
    def test_e7_seed_is_deterministic(self):
        material = "zte-sci-local-v1|20260705|e7-s0-no-attack|M=16|delay=5|fault=none|batch=na|repeat=0"
        d1 = hashlib.sha256(material.encode("ascii")).hexdigest()
        d2 = hashlib.sha256(material.encode("ascii")).hexdigest()
        assert d1 == d2

    def test_seed_derivation_8bytes(self):
        material = "zte-sci-local-v1|20260705|e7-s0-no-attack|M=16|delay=5|fault=none|batch=na|repeat=0"
        digest = hashlib.sha256(material.encode("ascii")).digest()
        seed = int.from_bytes(digest[:8], "big")
        assert 0 <= seed < 2**64
