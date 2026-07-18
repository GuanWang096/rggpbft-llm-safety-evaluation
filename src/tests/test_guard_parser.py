import pytest

from e1_pipeline.guard import GuardDecision, parse_guard_output


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "Safety: Safe\nCategories: None\nRefusal: Yes",
            GuardDecision("Safe", ("None",), "Yes"),
        ),
        (
            "Safety: Unsafe\nCategories: Violent, PII\nRefusal: No",
            GuardDecision("Unsafe", ("Violent", "PII"), "No"),
        ),
        (
            "Safety: Controversial\nCategories: Politically Sensitive Topics",
            GuardDecision("Controversial", ("Politically Sensitive Topics",), None),
        ),
    ],
)
def test_parse_guard_output(raw, expected):
    assert parse_guard_output(raw) == expected


def test_parse_guard_output_rejects_missing_safety_label():
    with pytest.raises(ValueError, match="Safety label"):
        parse_guard_output("Categories: None")


def test_response_moderation_requires_refusal_label():
    with pytest.raises(ValueError, match="Refusal label"):
        parse_guard_output(
            "Safety: Safe\nCategories: None",
            require_refusal=True,
        )
