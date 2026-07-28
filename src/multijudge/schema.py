from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal


BinaryLabel = Literal["safe", "unsafe"]
ParserStatus = Literal["ok", "error"]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DialogueTurn:
    role: Literal["user", "assistant"]
    content: str
    image_references: tuple[str, ...]
    image_paths: tuple[Path, ...]

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise ValueError(f"Unsupported dialogue role: {self.role!r}")
        if len(self.image_references) != len(self.image_paths):
            raise ValueError("Image references and paths must have equal lengths")


@dataclass(frozen=True)
class CanonicalSample:
    sample_id: str
    dataset: str
    split: str
    raw_id: str
    jsonl_line: int
    expected_label: BinaryLabel
    expected_dimension: str
    turns: tuple[DialogueTurn, ...]
    conversation_sha256: str

    def __post_init__(self) -> None:
        if self.expected_label not in {"safe", "unsafe"}:
            raise ValueError(f"Unsupported expected label: {self.expected_label!r}")
        if not self.turns:
            raise ValueError("A sample must contain at least one dialogue turn")

    @property
    def image_paths(self) -> tuple[Path, ...]:
        return tuple(path for turn in self.turns for path in turn.image_paths)

    @property
    def image_references(self) -> tuple[str, ...]:
        return tuple(
            reference
            for turn in self.turns
            for reference in turn.image_references
        )


@dataclass(frozen=True)
class JudgeServiceIdentity:
    organization: str
    model_id: str
    model_revision: str
    policy_sha256: str
    adapter_version: str

    def __post_init__(self) -> None:
        for field, value in asdict(self).items():
            if not str(value).strip():
                raise ValueError(f"Judge identity field {field!r} cannot be empty")

    @property
    def canonical_id(self) -> str:
        payload = canonical_json(asdict(self))
        return f"judge:{sha256_text(payload)}"

    def to_dict(self) -> dict[str, str]:
        result = asdict(self)
        result["canonical_id"] = self.canonical_id
        return result


@dataclass(frozen=True)
class ParsedDecision:
    status: ParserStatus
    label: BinaryLabel | None
    categories: tuple[str, ...]
    error: str | None = None

    def __post_init__(self) -> None:
        if self.status == "ok":
            if self.label not in {"safe", "unsafe"}:
                raise ValueError("Successful parser result requires a binary label")
            if self.error is not None:
                raise ValueError("Successful parser result cannot include an error")
        elif self.status == "error":
            if self.label is not None or not self.error:
                raise ValueError("Parser error requires no label and a nonempty message")
        else:
            raise ValueError(f"Unknown parser status: {self.status!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "label": self.label,
            "categories": list(self.categories),
            "error": self.error,
        }
