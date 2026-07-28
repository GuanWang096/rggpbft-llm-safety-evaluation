from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


V15_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = (
    V15_ROOT / "src"
    if (V15_ROOT / "src" / "multijudge").is_dir()
    else V15_ROOT
)
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from multijudge.runtime import collect_environment, fingerprint_model  # noqa: E402


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture runtime metadata and hash a local model directory."
    )
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    print("Capturing runtime environment...", flush=True)
    write_json(output / "environment.json", collect_environment())
    print("Hashing model files; this may take several minutes...", flush=True)
    write_json(
        output / "model_fingerprint.json",
        fingerprint_model(args.model_path),
    )
    print(f"Runtime metadata written to {output}")


if __name__ == "__main__":
    main()
