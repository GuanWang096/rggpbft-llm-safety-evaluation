from build_e1_batches import build_evidence_records, chunk_records, stratified_sample


def generation(index):
    return {
        "sample_id": f"sample-{index:04d}",
        "dataset": "a" if index % 2 == 0 else "b",
        "variant": f"v{index % 3}",
        "risk_category": "safe" if index % 5 == 0 else "unsafe",
        "expected_input_safe": index % 5 == 0,
        "image_sha256": f"image-{index}",
        "prompt": f"prompt {index}",
        "response": f"response {index}",
        "model_id": "model",
        "output_tokens": index + 1,
    }


def moderation(index):
    return {
        "sample_id": f"sample-{index:04d}",
        "safety": "Safe",
        "refusal": "No",
        "categories": ["None"],
        "guard_model_id": "guard",
    }


def test_build_evidence_records_follows_configured_order_and_hashes_text():
    generations = [generation(0), generation(1)]
    moderations = [moderation(0), moderation(1)]
    order = ["sample-0001", "sample-0000"]

    records = build_evidence_records(generations, moderations, order)

    assert [record["sample_id"] for record in records] == order
    assert records[0]["prompt"] == "prompt 1"
    assert len(records[0]["prompt_sha256"]) == 64
    assert len(records[0]["response_sha256"]) == 64


def test_chunk_records_creates_32_full_batches_and_one_batch_of_14():
    records = [{"sample_id": f"sample-{index}"} for index in range(2062)]

    batches = chunk_records(records, 64)

    assert len(batches) == 33
    assert [len(batch) for batch in batches[:-1]] == [64] * 32
    assert len(batches[-1]) == 14
    assert len({row["sample_id"] for batch in batches for row in batch}) == 2062


def test_stratified_sample_is_deterministic_and_unique():
    records = build_evidence_records(
        [generation(index) for index in range(400)],
        [moderation(index) for index in range(400)],
        [f"sample-{index:04d}" for index in range(400)],
    )

    first = stratified_sample(records, 256, seed=20260705)
    second = stratified_sample(records, 256, seed=20260705)

    assert [row["sample_id"] for row in first] == [row["sample_id"] for row in second]
    assert len(first) == 256
    assert len({row["sample_id"] for row in first}) == 256


def test_build_evidence_records_rejects_missing_moderation():
    generations = [generation(0)]

    try:
        build_evidence_records(generations, [], ["sample-0000"])
    except ValueError as exc:
        assert "moderation" in str(exc)
    else:
        raise AssertionError("missing moderation was accepted")

