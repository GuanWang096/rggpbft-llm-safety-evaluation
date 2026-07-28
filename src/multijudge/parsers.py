from __future__ import annotations

import json
import re
from typing import Any

from .schema import ParsedDecision


_FENCE = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL | re.IGNORECASE)


def _json_payload(raw_output: str) -> str:
    stripped = raw_output.strip()
    fenced = _FENCE.fullmatch(stripped)
    return fenced.group(1).strip() if fenced else stripped


def parse_canonical_decision(
    raw_output: str,
    canonical_categories: tuple[str, ...],
) -> ParsedDecision:
    del canonical_categories
    try:
        value: Any = json.loads(_json_payload(raw_output))
    except json.JSONDecodeError as exc:
        return ParsedDecision(
            status="error",
            label=None,
            categories=(),
            error=f"invalid_json:{exc.msg}",
        )

    if not isinstance(value, dict):
        return ParsedDecision(
            status="error",
            label=None,
            categories=(),
            error="root_must_be_object",
        )
    if set(value) != {"label"}:
        return ParsedDecision(
            status="error",
            label=None,
            categories=(),
            error="keys_must_be_exactly_label",
        )

    label = value["label"]
    if label not in {"safe", "unsafe"}:
        return ParsedDecision(
            status="error",
            label=None,
            categories=(),
            error="label_must_be_lowercase_safe_or_unsafe",
        )
    return ParsedDecision(
        status="ok",
        label=label,
        categories=(),
    )


def merge_panel_decisions(
    decisions: list[ParsedDecision],
) -> ParsedDecision:
    if not decisions:
        raise ValueError("At least one panel decision is required")
    errors = [
        f"panel_{index}:{decision.error}"
        for index, decision in enumerate(decisions, start=1)
        if decision.status == "error"
    ]
    if errors:
        return ParsedDecision(
            status="error",
            label=None,
            categories=(),
            error=";".join(errors),
        )
    unsafe = [decision for decision in decisions if decision.label == "unsafe"]
    if not unsafe:
        return ParsedDecision(status="ok", label="safe", categories=())
    categories = tuple(
        sorted({category for decision in unsafe for category in decision.categories})
    )
    return ParsedDecision(status="ok", label="unsafe", categories=categories)
