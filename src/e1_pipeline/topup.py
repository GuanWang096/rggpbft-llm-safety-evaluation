from collections.abc import Iterable


_IMMUTABLE_INPUT_FIELDS = (
    "dataset",
    "variant",
    "risk_category",
    "expected_input_safe",
    "image_sha256",
    "prompt",
    "model_id",
    "input_tokens",
)


def _index_unique(rows: Iterable[dict], label: str) -> dict[str, dict]:
    indexed = {}
    for row in rows:
        sample_id = row.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"{label} records require non-empty sample IDs")
        if sample_id in indexed:
            raise ValueError(f"duplicate {label} sample ID: {sample_id}")
        indexed[sample_id] = row
    return indexed


def select_limit_hits(rows: Iterable[dict], *, base_limit: int) -> list[dict]:
    rows = list(rows)
    _index_unique(rows, "base")
    return [row for row in rows if int(row["output_tokens"]) >= base_limit]


def validate_model_provenance(base_config: dict, current_provenance: dict) -> None:
    base_manifest = base_config.get("model_manifest_sha256")
    current_manifest = current_provenance.get("model_manifest_sha256")
    if not base_manifest or base_manifest != current_manifest:
        raise ValueError(
            "Top-up model manifest does not match the base generation model manifest"
        )


def merge_generation_records(
    base_rows: Iterable[dict],
    replacement_rows: Iterable[dict],
    *,
    base_limit: int,
    topup_limit: int,
) -> list[dict]:
    if topup_limit <= base_limit:
        raise ValueError("top-up limit must exceed the base limit")
    base_rows = list(base_rows)
    base_by_id = _index_unique(base_rows, "base")
    replacements = _index_unique(replacement_rows, "replacement")
    expected = {
        sample_id
        for sample_id, row in base_by_id.items()
        if int(row["output_tokens"]) >= base_limit
    }
    if set(replacements) != expected:
        missing = sorted(expected - set(replacements))
        extra = sorted(set(replacements) - expected)
        raise ValueError(
            f"replacement IDs do not match limit hits; missing={missing}, extra={extra}"
        )

    for sample_id, replacement in replacements.items():
        base = base_by_id[sample_id]
        for field in _IMMUTABLE_INPUT_FIELDS:
            if field in base or field in replacement:
                if base.get(field) != replacement.get(field):
                    raise ValueError(
                        f"Top-up field mismatch for {sample_id}: {field}"
                    )

    merged = []
    for base in base_rows:
        sample_id = base["sample_id"]
        if sample_id in replacements:
            row = dict(replacements[sample_id])
            row["generation_budget"] = topup_limit
            row["generation_strategy"] = "limit_hit_topup"
        else:
            row = dict(base)
            row["generation_budget"] = base_limit
            row["generation_strategy"] = "reused_natural_completion"
        merged.append(row)
    return merged
