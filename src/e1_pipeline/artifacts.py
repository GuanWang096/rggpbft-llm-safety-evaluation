import hashlib
import json
import os
from pathlib import Path
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_artifact_hashes(model_path: Path) -> dict[str, str]:
    model_path = Path(model_path)
    weights = sorted(model_path.glob("*.safetensors"))
    if not weights:
        raise FileNotFoundError(f"No safetensors weights found in {model_path}")
    runtime_files = weights
    for pattern in ("*.json", "*.txt", "*.model"):
        runtime_files.extend(sorted(model_path.glob(pattern)))
    unique_files = sorted(set(runtime_files), key=lambda path: path.name)
    return {path.name: sha256_file(path) for path in unique_files}


def write_checksum_manifest(root: Path, filenames: list[str]) -> None:
    root = Path(root)
    lines = []
    for filename in sorted(set(filenames)):
        path = root / filename
        if path.is_file():
            lines.append(f"{sha256_file(path)}  {filename}\n")
    (root / "checksums.sha256").write_text("".join(lines), encoding="ascii")


class JsonlCheckpoint:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.completed_ids: set[str] = set()
        if self.path.exists():
            raw_lines = self.path.read_bytes().splitlines(keepends=True)
            valid_bytes = 0
            for line_number, raw_line in enumerate(raw_lines, start=1):
                terminated = raw_line.endswith((b"\n", b"\r"))
                if not raw_line.strip():
                    valid_bytes += len(raw_line)
                    continue
                try:
                    line = raw_line.decode("utf-8")
                    record = json.loads(line)
                    self.completed_ids.add(record["sample_id"])
                except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
                    if line_number == len(raw_lines) and not terminated:
                        with self.path.open("r+b") as handle:
                            handle.truncate(valid_bytes)
                        break
                    raise ValueError(
                        f"Invalid checkpoint record at {self.path}:{line_number}"
                    ) from exc
                valid_bytes += len(raw_line)

    def append(self, record: dict[str, Any]) -> bool:
        sample_id = record.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError("Checkpoint records require a non-empty sample_id")
        if sample_id in self.completed_ids:
            return False
        encoded = canonical_json_bytes(record) + b"\n"
        with self.path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        self.completed_ids.add(sample_id)
        return True
