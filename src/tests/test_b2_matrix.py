from pathlib import Path

from run_b2_replay import (
    build_ablation_matrix,
    build_matrix,
    completed_canonical_pair_ids,
    derive_pair_seed,
    next_attempt_id,
)


def test_derive_pair_seed_is_deterministic_and_protocol_independent():
    first = derive_pair_seed(block="B2", batch=64, repeat=1)
    second = derive_pair_seed(block="B2", batch=64, repeat=1)

    assert first == second
    assert 0 <= first["seed"] <= 0x7FFFFFFFFFFFFFFF
    assert len(first["sha256"]) == 64


def test_build_matrix_has_15_unique_paired_configurations():
    matrix = build_matrix()

    assert len(matrix) == 15
    assert len({row["pair_id"] for row in matrix}) == 15
    assert {row["concurrency"] for row in matrix} == {1, 4, 8}
    assert {row["repeat"] for row in matrix} == {1, 2, 3, 4, 5}


def test_build_ablation_matrix_has_20_batch_size_pairs():
    matrix = build_ablation_matrix(Path("input"))

    assert len(matrix) == 20
    assert len({row["pair_id"] for row in matrix}) == 20
    assert {row["batch_size"] for row in matrix} == {1, 16, 64, 256}
    assert {row["concurrency"] for row in matrix} == {4}
    assert all(row["evidence_record_count"] == 256 for row in matrix)


def test_resume_maps_retry_directory_to_canonical_pair(tmp_path):
    completed = tmp_path / "pair-a"
    completed.mkdir()
    (completed / "summary.json").write_text(
        '{"pair":{"pair_id":"canonical-a"}}', encoding="utf-8"
    )

    assert completed_canonical_pair_ids(tmp_path, ["pair-a"]) == {"canonical-a"}


def test_retry_attempt_id_preserves_failed_directory(tmp_path):
    (tmp_path / "pair-a").mkdir()
    (tmp_path / "pair-a_retry1").mkdir()

    assert next_attempt_id(tmp_path, "pair-a") == ("pair-a_retry2", 2)
