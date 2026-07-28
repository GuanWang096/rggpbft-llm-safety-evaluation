from multijudge.parsers import merge_panel_decisions, parse_canonical_decision
from multijudge.schema import ParsedDecision


CATEGORIES = (
    "Violence & Harm",
    "Illegal Activities",
)


def test_parse_safe_json() -> None:
    result = parse_canonical_decision(
        '{"label":"safe"}',
        CATEGORIES,
    )
    assert result.status == "ok"
    assert result.label == "safe"


def test_parse_unsafe_json_in_code_fence() -> None:
    result = parse_canonical_decision(
        '```json\n{"label":"unsafe"}\n```',
        CATEGORIES,
    )
    assert result.status == "ok"
    assert result.label == "unsafe"


def test_parser_rejects_extra_text() -> None:
    result = parse_canonical_decision(
        'Decision: {"label":"safe"}',
        CATEGORIES,
    )
    assert result.status == "error"
    assert result.error is not None


def test_parser_rejects_extra_key() -> None:
    result = parse_canonical_decision(
        '{"label":"safe","reason":"benign"}',
        CATEGORIES,
    )
    assert result.status == "error"


def test_parser_rejects_category_key() -> None:
    result = parse_canonical_decision(
        '{"label":"unsafe","categories":["Illegal Activities"]}',
        CATEGORIES,
    )
    assert result.status == "error"


def test_parser_rejects_invalid_label() -> None:
    result = parse_canonical_decision(
        '{"label":"Unsafe"}',
        CATEGORIES,
    )
    assert result.status == "error"


def test_panel_merge_uses_unsafe_or() -> None:
    merged = merge_panel_decisions(
        [
            ParsedDecision(status="ok", label="safe", categories=()),
            ParsedDecision(
                status="ok",
                label="unsafe",
                categories=(),
            ),
        ]
    )
    assert merged.label == "unsafe"
    assert merged.categories == ()


def test_panel_merge_propagates_parser_error() -> None:
    merged = merge_panel_decisions(
        [
            ParsedDecision(status="ok", label="safe", categories=()),
            ParsedDecision(
                status="error",
                label=None,
                categories=(),
                error="invalid_json",
            ),
        ]
    )
    assert merged.status == "error"
    assert merged.label is None
