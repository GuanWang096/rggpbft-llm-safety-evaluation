from dataclasses import dataclass
import re


_CATEGORY_NAMES = (
    "Politically Sensitive Topics",
    "Sexual Content or Sexual Acts",
    "Non-violent Illegal Acts",
    "Copyright Violation",
    "Suicide & Self-Harm",
    "Unethical Acts",
    "Jailbreak",
    "Violent",
    "PII",
    "None",
)
_CATEGORY_PATTERN = re.compile("|".join(re.escape(name) for name in _CATEGORY_NAMES))


@dataclass(frozen=True)
class GuardDecision:
    safety: str
    categories: tuple[str, ...]
    refusal: str | None


def parse_guard_output(content: str, *, require_refusal: bool = False) -> GuardDecision:
    safety_match = re.search(r"Safety:\s*(Safe|Unsafe|Controversial)\b", content)
    if safety_match is None:
        raise ValueError("Safety label is missing from Qwen3Guard output")
    refusal_match = re.search(r"Refusal:\s*(Yes|No)\b", content)
    if require_refusal and refusal_match is None:
        raise ValueError("Refusal label is missing from Qwen3Guard response output")
    categories = tuple(dict.fromkeys(_CATEGORY_PATTERN.findall(content)))
    return GuardDecision(
        safety=safety_match.group(1),
        categories=categories,
        refusal=refusal_match.group(1) if refusal_match else None,
    )
