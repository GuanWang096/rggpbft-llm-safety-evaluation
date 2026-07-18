from pathlib import Path


SCRIPT = (
    Path(__file__).parents[2]
    / "src"
    / "fabric"
    / "network"
    / "scripts"
    / "anchor_e1_results.sh"
)


def test_anchor_defaults_to_authoritative_topup_run():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "results/e1-final-2048-topup" in text


def test_anchor_bundle_includes_topup_provenance_files():
    text = SCRIPT.read_text(encoding="utf-8")

    for filename in (
        "topup_config.json",
        "topup_generation_status.json",
        "generation_topup.jsonl",
    ):
        assert filename in text


def test_anchor_derives_generation_budget_from_config():
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'config["max_new_tokens"]' in text
    assert 'row.get("output_tokens") == 128' not in text
    assert '"max_new_tokens": 128' not in text


def test_anchor_names_safe_non_refusal_as_a_guard_proxy():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "guard_safe_non_refusal_rate_ppm" in text
    assert "safe_input_utility_rate_ppm" not in text


def test_anchor_manifest_lists_only_files_included_in_bundle():
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"${CORE_FILES[@]}" <<\'PY\'' in text
    assert "included_files = set(sys.argv[3:])" in text
    assert "path.name in included_files" in text


def test_anchor_records_terminal_status_and_checksums_it():
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"state":"running"' in text
    assert '"state":"completed"' in text
    assert "status.json" in text
