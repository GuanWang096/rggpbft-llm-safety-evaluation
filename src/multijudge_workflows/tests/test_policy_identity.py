import json
from pathlib import Path

from multijudge.policy import load_policy, render_policy_prompt
from multijudge.schema import JudgeServiceIdentity


def _policy_file(tmp_path: Path) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "policy_id": "test",
                "version": "1",
                "task": "binary",
                "safe_definition": "safe",
                "unsafe_definition": "unsafe",
                "categories": ["A", "B"],
                "output_schema": {"type": "object"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_policy_hash_and_prompt_are_stable(tmp_path: Path) -> None:
    policy = load_policy(_policy_file(tmp_path))
    second = load_policy(_policy_file(tmp_path))
    assert policy.sha256 == second.sha256
    prompt = render_policy_prompt(policy)
    assert '{"label":"safe"}' in prompt
    assert '"categories"' not in prompt
    assert "- A" in prompt


def test_service_identity_changes_with_revision() -> None:
    first = JudgeServiceIdentity("org", "model", "r1", "p", "a")
    second = JudgeServiceIdentity("org", "model", "r2", "p", "a")
    assert first.canonical_id != second.canonical_id
    assert first.canonical_id == JudgeServiceIdentity(
        "org", "model", "r1", "p", "a"
    ).canonical_id
