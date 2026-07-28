from __future__ import annotations

import argparse
import json
from pathlib import Path


def _directory_size_gib(path: Path) -> float:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) / 1024**3


def run(args: argparse.Namespace) -> None:
    model_path = args.model_path.resolve()
    config_path = model_path / "config.json"
    if not config_path.is_file():
        raise SystemExit(f"Missing model config: {config_path}")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    architectures = config.get("architectures") or []
    if args.expected_architecture and args.expected_architecture not in architectures:
        raise SystemExit(
            f"Unexpected architecture {architectures}; "
            f"expected {args.expected_architecture}"
        )

    weight_files = sorted(model_path.rglob("*.safetensors"))
    if not weight_files:
        raise SystemExit(f"No safetensors weights found under {model_path}")

    if args.dataset_root:
        dataset_root = args.dataset_root.resolve()
        required = [
            dataset_root / "MMDS" / "mmds.jsonl",
            dataset_root / "MMDS" / "images",
        ]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise SystemExit(f"Missing required dataset paths: {missing}")

    print(f"model_path={model_path}")
    if args.revision:
        print(f"revision={args.revision}")
    print(f"architectures={architectures}")
    print(f"weight_files={len(weight_files)}")
    print(f"directory_size_gib={_directory_size_gib(model_path):.2f}")

    if args.require_cuda:
        import torch
        import transformers

        if not torch.cuda.is_available():
            raise SystemExit("CUDA is unavailable")
        print(f"torch={torch.__version__}")
        if args.print_torchvision:
            import torchvision

            print(f"torchvision={torchvision.__version__}")
        print(f"transformers={transformers.__version__}")
        print(f"gpu={torch.cuda.get_device_name(0)}")
        print(
            "vram_gib="
            f"{torch.cuda.get_device_properties(0).total_memory / 1024**3:.2f}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--expected-architecture")
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("--print-torchvision", action="store_true")
    return parser


if __name__ == "__main__":
    run(build_parser().parse_args())
