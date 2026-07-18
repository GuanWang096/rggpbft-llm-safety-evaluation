import verify_repository


def test_required_tests_passed_requires_every_test_to_pass():
    report = {
        "tests": {
            "python": {"return_code": 0, "passed": True},
            "go": {"return_code": 1, "passed": False},
        }
    }

    assert verify_repository.required_tests_passed(report) is False


def passing_probe():
    return {
        "dual_container_50ms": {
            "exit_code": 0,
            "safety_violations": 0,
            "rounds_completed": 2,
        }
    }


def test_build_status_marks_failed_test_report_as_failed():
    report = {"tests": {"python": {"return_code": 1, "passed": False}}}

    status = verify_repository.build_status(["m0_test_report.json"], report, passing_probe())

    assert status["state"] == "failed"
    assert status["failed_tests"] == ["python"]


def test_build_status_marks_all_passing_tests_completed():
    report = {
        "tests": {
            "python": {"return_code": 0, "passed": True},
            "go": {"return_code": 0, "passed": True},
        }
    }

    status = verify_repository.build_status(["m0_test_report.json"], report, passing_probe())

    assert status["state"] == "completed"
    assert status["failed_tests"] == []


def test_build_status_rejects_failed_netem_probe():
    report = {"tests": {"python": {"return_code": 0, "passed": True}}}
    probe = {"dual_container_50ms": {"exit_code": 1}}

    status = verify_repository.build_status(["m0_netem_probe.json"], report, probe)

    assert status["state"] == "failed"
    assert "netem_probe" in status["failed_checks"]


def test_probe_round_count_accepts_current_and_legacy_summary_fields():
    assert verify_repository.probe_round_count({"round_count": 2}) == 2
    assert verify_repository.probe_round_count({"client_committed_rounds": 2}) == 2
