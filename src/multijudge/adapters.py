from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from .schema import CanonicalSample, JudgeServiceIdentity


InputMode = Literal["native", "panel"]


class JudgeAdapter(ABC):
    identity: JudgeServiceIdentity

    @abstractmethod
    def judge(
        self,
        sample: CanonicalSample,
        *,
        input_mode: InputMode,
        panel_dir: Path,
    ) -> dict[str, Any]:
        """Return one standardized raw judgment record."""

    @abstractmethod
    def close(self) -> None:
        """Release model and accelerator resources."""
