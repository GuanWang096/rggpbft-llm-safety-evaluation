import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_consensus_matrix import command_for, next_attempt_directory


class ConsensusMatrixRunnerTests(unittest.TestCase):
    def test_next_attempt_directory_preserves_existing_failed_run(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary) / "run-1"
            base.mkdir()
            (pathlib.Path(f"{base}_retry1")).mkdir()

            self.assertEqual(
                next_attempt_directory(base), pathlib.Path(f"{base}_retry2")
            )

    def test_command_maps_machine_readable_run_without_changing_seed(self):
        run = {
            "protocol": "rgg",
            "nodes": 16,
            "groups": 4,
            "rounds": 1,
            "delay_ms": 5,
            "round_timeout": 12,
            "view_timeout": 0.5,
            "fault": "f5",
            "fault_nodes": "0,1",
            "seed": 123,
        }
        command = command_for(
            run,
            pathlib.Path("run-dir"),
            pathlib.Path("run_v2.py"),
            skip_build=True,
        )

        self.assertIn("--fault-scenario", command)
        self.assertIn("f5", command)
        self.assertIn("0,1", command)
        self.assertIn("123", command)
        self.assertIn("--skip-build", command)

    def test_command_resolves_run_directory_before_changing_working_directory(self):
        run = {
            "protocol": "pbft",
            "nodes": 16,
            "groups": 4,
            "rounds": 1,
            "delay_ms": 5,
            "round_timeout": 12,
            "view_timeout": 0.5,
            "fault": "f1",
            "fault_nodes": "0",
            "seed": 123,
        }

        command = command_for(
            run,
            pathlib.Path("relative-results") / "run-1",
            pathlib.Path("run_v2.py"),
            skip_build=False,
        )

        run_dir = pathlib.Path(command[command.index("--run-dir") + 1])
        self.assertTrue(run_dir.is_absolute())


if __name__ == "__main__":
    unittest.main()
