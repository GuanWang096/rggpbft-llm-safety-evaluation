import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from run_e9_netem import generate_matrix, generate_qualification_matrix


def test_qualification_matrix_covers_profile_protocol_and_scale_once():
    matrix, profiles = generate_qualification_matrix()
    assert len(matrix) == 16
    assert {run["network_profile"] for run in matrix} == set(profiles)
    assert {run["protocol"] for run in matrix} == {"pbft", "rgg"}
    assert {run["nodes"] for run in matrix} == {16, 24}
    assert {run["repeat"] for run in matrix} == {1}
    assert all(run["series"] == "e9-runner-v3-qualification" for run in matrix)


def test_full_matrix_remains_160_runs():
    matrix, _ = generate_matrix()
    assert len(matrix) == 160
