import json
from pathlib import Path

from .artifacts import canonical_json_bytes


def write_once(path: Path, config: dict) -> None:
    path = Path(path)
    encoded = canonical_json_bytes(config) + b"\n"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if canonical_json_bytes(existing) != canonical_json_bytes(config):
            raise ValueError(f"Existing run config does not match requested config: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)

