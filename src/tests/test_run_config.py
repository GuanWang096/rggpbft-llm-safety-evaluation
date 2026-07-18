import pytest

from e1_pipeline.run_config import write_once


def test_write_once_accepts_identical_resume_and_rejects_changed_config(tmp_path):
    path = tmp_path / "config.json"
    write_once(path, {"seed": 7, "limit": 12})
    write_once(path, {"limit": 12, "seed": 7})

    with pytest.raises(ValueError, match="does not match"):
        write_once(path, {"seed": 8, "limit": 12})

